import SwiftUI
import Combine

// MARK: - 消息数据模型

struct ChatMessage: Identifiable {
    let id: UUID
    var role: MessageRole
    var content: String
    var toolCalls: [ToolCallInfo]
    var meta: MessageMeta?
    var ragCount: Int
    var isStreaming: Bool
    var isCompressed: Bool

    enum MessageRole { case user, assistant }

    init(
        role: MessageRole,
        content: String = "",
        isStreaming: Bool = false,
        isCompressed: Bool = false
    ) {
        self.id = UUID()
        self.role = role
        self.content = content
        self.toolCalls = []
        self.meta = nil
        self.ragCount = 0
        self.isStreaming = isStreaming
        self.isCompressed = isCompressed
    }
}

struct ToolCallInfo: Identifiable {
    var id: String
    var name: String
    var args: [String: Any]
    var result: [String: Any]?
    var isRunning: Bool
}

struct MessageMeta {
    var provider: String
    var model: String
    var layer: String
    var promptTokens: Int
    var completionTokens: Int
    var costUsd: Double
    var source: String?
}

// MARK: - ChatViewModel

@MainActor
final class ChatViewModel: ObservableObject {

    // 侧边栏数据
    @Published var projects: [ProjectItem] = []
    @Published var projectStatus: ProjectStatus?
    @Published var conversations: [ConversationItem] = []
    @Published var activeConversationId: Int?
    @Published var scratchText: String = ""
    @Published var availableModels: [String] = []

    // 聊天区数据
    @Published var messages: [ChatMessage] = []
    @Published var selectedModel: String = ""

    // 发送状态
    @Published var isSending: Bool = false
    @Published var sendError: String?

    // 健康状态
    @Published var isHealthy: Bool = false

    // 布局状态
    @Published var sidebarVisible: Bool = true
    @Published var canvasContent: String = ""
    @Published var canvasVisible: Bool = false

    private let api = ChatAPI()
    private var initialLoadTask: Task<Void, Never>?
    private var streamTask: Task<Void, Never>?
    private var healthTask: Task<Void, Never>?
    private var scratchSaveTask: Task<Void, Never>?
    private var didLoadInitialState = false
    private let restoredMessageDisplayLimit = 12
    private let restoredMessageCharacterLimit = 4_000

    // MARK: - 初始化

    func onAppear() {
        startInitialLoad()
        if healthTask == nil {
            startHealthPolling()
        }
    }

    func onDisappear() {
        healthTask?.cancel()
        healthTask = nil
        initialLoadTask?.cancel()
        initialLoadTask = nil
        streamTask?.cancel()
    }

    private func startInitialLoad() {
        guard !didLoadInitialState, initialLoadTask == nil else { return }
        initialLoadTask = Task { [weak self] in
            guard let self else { return }
            for attempt in 0..<20 {
                if Task.isCancelled { return }
                if await self.loadInitialStateOnce() {
                    self.didLoadInitialState = true
                    self.initialLoadTask = nil
                    return
                }
                let delay: UInt64 = attempt < 6 ? 500_000_000 : 1_000_000_000
                try? await Task.sleep(nanoseconds: delay)
            }
            self.initialLoadTask = nil
        }
    }

    private func loadInitialStateOnce() async -> Bool {
        var loadedAnything = false

        if let items = try? await api.listProjects() {
            projects = items
            loadedAnything = true
        }
        if let status = try? await api.projectStatus() {
            projectStatus = status
            loadedAnything = true
        }
        if let convs = try? await api.listConversations() {
            conversations = convs
            loadedAnything = true
        }
        if let scratch = try? await api.loadScratch() {
            scratchText = scratch
            loadedAnything = true
        }
        if let models = try? await api.listModels() {
            availableModels = models
            loadedAnything = true
        }

        guard loadedAnything else { return false }

        if activeConversationId == nil && messages.isEmpty {
            await restoreStartupConversation()
        }
        return true
    }

    private func restoreStartupConversation() async {
        if let projConv = try? await api.projectConversation(),
           let conv = projConv.conversation,
           !projConv.messages.isEmpty {
            activeConversationId = conv.id
            messages = displayMessages(from: projConv.messages)
            if !conversations.contains(where: { $0.id == conv.id }) {
                await loadConversations()
            }
            return
        }

        for conv in conversations {
            if let msgs = try? await api.loadMessages(conv.id), !msgs.isEmpty {
                activeConversationId = conv.id
                messages = displayMessages(from: msgs)
                return
            }
        }

        if let projConv = try? await api.projectConversation(),
           let conv = projConv.conversation {
            activeConversationId = conv.id
            messages = displayMessages(from: projConv.messages)
            if !conversations.contains(where: { $0.id == conv.id }) {
                await loadConversations()
            }
        }
    }

    // MARK: - Projects

    func loadProjects() async {
        do {
            async let items = api.listProjects()
            async let status = api.projectStatus()
            projects = try await items
            projectStatus = try await status
        } catch {
            // 静默失败——内核未就绪时正常
        }
    }

    func activateProject(_ item: ProjectItem) async {
        do {
            try await api.activateProject(item.id)
            await loadProjects()
            // 加载该项目的对话
            let projConv = try? await api.projectConversation()
            if let conv = projConv?.conversation {
                activeConversationId = conv.id
                messages = displayMessages(from: projConv?.messages ?? [])
            } else {
                activeConversationId = nil
                messages = []
            }
            await loadConversations()
        } catch {}
    }

    func closeProject() async {
        do {
            try await api.closeProject()
            await loadProjects()
            activeConversationId = nil
            messages = []
        } catch {}
    }

    func reindexProject() async {
        do { try await api.reindexProject() } catch {}
        await loadProjects()
    }

    func deleteProject(_ item: ProjectItem) async {
        do { try await api.deleteProject(item.id) } catch {}
        await loadProjects()
    }

    // MARK: - Conversations

    func loadConversations() async {
        do {
            conversations = try await api.listConversations()
        } catch {}
    }

    func selectConversation(_ item: ConversationItem) async {
        activeConversationId = item.id
        do {
            let msgs = try await api.loadMessages(item.id)
            messages = displayMessages(from: msgs)
        } catch {
            messages = []
        }
    }

    func newConversation() {
        activeConversationId = nil
        messages = []
    }

    func deleteConversation(_ item: ConversationItem) async {
        do { try await api.deleteConversation(item.id) } catch {}
        if activeConversationId == item.id {
            activeConversationId = nil
            messages = []
        }
        await loadConversations()
    }

    func renameConversation(_ item: ConversationItem, title: String) async {
        do { try await api.renameConversation(item.id, title: title) } catch {}
        await loadConversations()
    }

    // MARK: - Scratch

    func loadScratch() async {
        do { scratchText = try await api.loadScratch() } catch {}
    }

    func scheduleScratchSave() {
        scratchSaveTask?.cancel()
        scratchSaveTask = Task {
            try? await Task.sleep(nanoseconds: 1_000_000_000)  // 1s debounce
            guard !Task.isCancelled else { return }
            do { try await api.saveScratch(scratchText) } catch {}
        }
    }

    // MARK: - Models

    func loadModels() async {
        do { availableModels = try await api.listModels() } catch {}
    }

    // MARK: - Sending

    func send(text: String) {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        guard !isSending else { return }

        let userMsg = ChatMessage(role: .user, content: text)
        messages.append(userMsg)
        isSending = true
        sendError = nil

        let assistantMsg = ChatMessage(role: .assistant, content: "", isStreaming: true)
        messages.append(assistantMsg)
        let assistantId = assistantMsg.id

        streamTask = Task {
            do {
                let req = try api.chatStreamRequest(
                    message: text,
                    model: selectedModel.isEmpty ? nil : selectedModel,
                    conversationId: activeConversationId
                )
                for try await event in SSEClient.stream(request: req) {
                    if Task.isCancelled { break }
                    await handleSSEEvent(event, assistantId: assistantId)
                    if event.type == .done { break }
                }
            } catch {
                if !Task.isCancelled {
                    sendError = error.localizedDescription
                }
            }
            let wasCancelled = Task.isCancelled
            finalizeAssistant(assistantId: assistantId)
            if !wasCancelled {
                await syncActiveConversationAfterStream()
            }
            isSending = false
        }
    }

    func stopSending() {
        streamTask?.cancel()
        streamTask = nil
        if let idx = messages.lastIndex(where: { $0.isStreaming }) {
            messages[idx].isStreaming = false
        }
        isSending = false
    }

    // MARK: - Canvas

    func openCanvas(_ content: String) {
        canvasContent = content
        canvasVisible = true
    }

    func closeCanvas() {
        canvasVisible = false
    }

    // MARK: - SSE 事件处理

    private func handleSSEEvent(_ event: SSEEvent, assistantId: UUID) async {
        switch event.type {
        case .conversation:
            if let convId = event.conversationId {
                let wasNew = activeConversationId == nil || activeConversationId != convId
                activeConversationId = convId
                if wasNew {
                    try? await Task.sleep(nanoseconds: 150_000_000)
                    await loadConversations()
                }
            }

        case .meta:
            if let decision = event.decision {
                updateMeta(assistantId: assistantId) { meta in
                    meta.provider = decision["provider"] as? String ?? ""
                    meta.model = decision["model"] as? String ?? ""
                    meta.layer = decision["layer"] as? String ?? ""
                }
            }

        case .rag:
            let count = (event.ragHits ?? []).count
            if count > 0, let idx = msgIdx(assistantId) {
                messages[idx].ragCount = count
            }

        case .toolStart:
            if let id = event.toolId, let name = event.toolName, let idx = msgIdx(assistantId) {
                let tc = ToolCallInfo(id: id, name: name, args: event.toolArgs ?? [:], result: nil, isRunning: true)
                messages[idx].toolCalls.append(tc)
            }

        case .toolResult:
            if let id = event.toolId, let idx = msgIdx(assistantId) {
                if let tcIdx = messages[idx].toolCalls.firstIndex(where: { $0.id == id }) {
                    messages[idx].toolCalls[tcIdx].result = event.toolResultDict
                    messages[idx].toolCalls[tcIdx].isRunning = false
                }
            }

        case .delta:
            if let content = event.content, let idx = msgIdx(assistantId) {
                messages[idx].content += content
            }

        case .usage:
            let u = event.usageDict ?? [:]
            updateMeta(assistantId: assistantId) { meta in
                meta.promptTokens = u["prompt_tokens"] as? Int ?? 0
                meta.completionTokens = u["completion_tokens"] as? Int ?? 0
                meta.costUsd = event.costUsd ?? 0
            }

        case .done:
            if let full = event.full, let idx = msgIdx(assistantId) {
                messages[idx].content = full
            }
            if let src = event.source {
                updateMeta(assistantId: assistantId) { meta in meta.source = src }
            }
            // 检查是否值得打开 canvas
            if let idx = msgIdx(assistantId), shouldAutoCanvas(messages[idx].content) {
                canvasContent = messages[idx].content
                canvasVisible = true
            }

        case .error:
            sendError = event.errorMessage

        }
    }

    private func finalizeAssistant(assistantId: UUID) {
        if let idx = msgIdx(assistantId) {
            messages[idx].isStreaming = false
        }
    }

    private func syncActiveConversationAfterStream() async {
        guard let convId = activeConversationId else { return }
        guard let history = try? await api.loadMessages(convId) else { return }
        messages = displayMessages(from: history)
        await loadConversations()
    }

    // MARK: - Health polling

    private func startHealthPolling() {
        healthTask = Task {
            while !Task.isCancelled {
                do {
                    let h = try await api.health()
                    isHealthy = h.ok && (!h.authRequired || h.authenticated)
                    if isHealthy && !didLoadInitialState {
                        startInitialLoad()
                    }
                } catch {
                    isHealthy = false
                }
                try? await Task.sleep(nanoseconds: 8_000_000_000)
            }
        }
    }

    // MARK: - Helpers

    private func msgIdx(_ id: UUID) -> Int? {
        messages.indices.first { messages[$0].id == id }
    }

    private func updateMeta(assistantId: UUID, update: (inout MessageMeta) -> Void) {
        guard let idx = msgIdx(assistantId) else { return }
        if messages[idx].meta == nil {
            messages[idx].meta = MessageMeta(provider: "", model: "", layer: "",
                                             promptTokens: 0, completionTokens: 0, costUsd: 0)
        }
        update(&messages[idx].meta!)
    }

    private func histToChat(_ h: HistoryMessage) -> ChatMessage {
        let msg = ChatMessage(
            role: h.role == "user" ? .user : .assistant,
            content: h.content,
            isCompressed: h.compressed ?? false
        )
        return msg
    }

    private func displayMessages(from history: [HistoryMessage]) -> [ChatMessage] {
        history
            .filter { $0.role == "user" || $0.role == "assistant" }
            .suffix(restoredMessageDisplayLimit)
            .map { histToChat($0) }
            .map { trimmedForDisplay($0) }
    }

    private func trimmedForDisplay(_ message: ChatMessage) -> ChatMessage {
        guard message.content.count > restoredMessageCharacterLimit else {
            return message
        }

        var trimmed = message
        let idx = trimmed.content.index(
            trimmed.content.startIndex,
            offsetBy: restoredMessageCharacterLimit
        )
        trimmed.content = String(trimmed.content[..<idx])
            + "\n\n[前端仅显示前 \(restoredMessageCharacterLimit) 字，完整历史仍保存在本地数据库。]"
        return trimmed
    }

    private func shouldAutoCanvas(_ content: String) -> Bool {
        // 含 mermaid 或大代码块时值得打开 canvas
        let hasMermaid = content.contains("```mermaid")
        let hasLargeCode = content.contains("```") && content.count > 800
        return hasMermaid || hasLargeCode
    }
}
