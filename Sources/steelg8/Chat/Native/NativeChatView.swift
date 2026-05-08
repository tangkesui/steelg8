import SwiftUI

// MARK: - NativeChatView

/// 原生 Chat 主视图。布局：左侧边栏 | 中间消息区 + 输入框 | 右侧 Canvas（可选）。
/// toolbar 用自定义 HStack（不用 SwiftUI .toolbar，避免 items 泄漏到 Settings 窗口）。
/// 窗口拖动靠 AppController.configureMainWindow 里的 isMovableByWindowBackground = true。
struct NativeChatView: View {
    @StateObject private var vm = ChatViewModel()
    @State private var inputText = ""
    @State private var composerHeight: CGFloat = ComposerView.minHeight
    @State private var showNewProject = false
    @State private var showSearch = false
    @State private var showModelConfig = false
    @State private var modelMenuHovered = false
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ZStack {
            HStack(spacing: 0) {
                // 侧边栏：从顶到底贯穿整列
                if vm.sidebarVisible {
                    ChatSidebarView(vm: vm, showNewProject: $showNewProject, showSearch: $showSearch, showModelConfig: $showModelConfig)
                    Divider()
                }

                // 右侧主区：toolbar + 消息 + composer
                VStack(spacing: 0) {
                    chatToolbar
                    Divider()
                    ChatMessagesView(vm: vm)
                        .safeAreaInset(edge: .bottom, spacing: 0) {
                            composerBar
                        }
                }

                // Canvas 面板
                if vm.canvasVisible {
                    Divider()
                    ChatCanvasView(vm: vm)
                        .frame(minWidth: 300)
                }
            }

            // 居中浮层：新建项目
            if showNewProject {
                modalOverlay { showNewProject = false }
                NewProjectPanel(isPresented: $showNewProject) { path, name, rebuild in
                    Task {
                        do {
                            try await ChatAPI().openProject(
                                path: path, name: name.isEmpty ? nil : name, rebuild: rebuild)
                            await vm.loadProjects()
                        } catch {}
                    }
                }
                .modalCard()
                .frame(width: 360)
                .zIndex(1)
            }

            // 居中浮层：搜索
            if showSearch {
                modalOverlay { showSearch = false }
                SearchPanel(vm: vm, isPresented: $showSearch)
                    .modalCard()
                    .frame(width: 560, height: 440)
                    .zIndex(1)
            }

            // 居中浮层：模型与设置
            if showModelConfig {
                modalOverlay { showModelConfig = false }
                ModelConfigPanel(vm: vm, isPresented: $showModelConfig)
                    .modalCard()
                    .frame(width: 420)
                    .zIndex(1)
            }
        }
        .frame(minWidth: 960, minHeight: 600)
        .onAppear {
            vm.canvasVisible = false
            vm.onAppear()
        }
        .onDisappear { vm.onDisappear() }
        .alert("发送出错", isPresented: .init(
            get: { vm.sendError != nil },
            set: { if !$0 { vm.sendError = nil } }
        )) {
            Button("好") { vm.sendError = nil }
        } message: {
            Text(vm.sendError ?? "")
        }
    }

    // MARK: - 自定义 Toolbar（内容区顶部 HStack，不用 SwiftUI .toolbar）

    private var chatToolbar: some View {
        HStack(spacing: 10) {
            WindowDragArea()
                .frame(maxWidth: .infinity, minHeight: 28, maxHeight: 28)
                .contentShape(Rectangle())

            // 模型选择（自定义 Menu，完全黑白，单 chevron，hover 变灰底）
            Menu {
                ForEach(groupedModels, id: \.provider) { group in
                    Section(providerLabel(group.provider)) {
                        ForEach(group.models, id: \.self) { m in
                            Button(shortModelName(m)) { vm.selectedModel = m }
                        }
                    }
                }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.system(size: 8, weight: .medium))
                    Text(vm.selectedModel.isEmpty ? "选择模型" : shortModelName(vm.selectedModel))
                        .font(.system(size: 12))
                        .lineLimit(1)
                }
                .foregroundStyle(.primary)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(
                    RoundedRectangle(cornerRadius: 5)
                        .fill(modelMenuHovered ? Color(nsColor: .controlBackgroundColor) : Color.clear)
                )
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .onHover { modelMenuHovered = $0 }

            // 健康状态指示（8pt dot + glow ring）
            ZStack {
                let dotColor = vm.isHealthy ? SG.success(colorScheme) : SG.danger
                Circle().fill(dotColor.opacity(0.20)).frame(width: 14, height: 14)
                Circle().fill(dotColor).frame(width: 8, height: 8)
            }
            .help(vm.isHealthy ? "内核运行正常" : "内核未就绪")

            // Canvas 开关
            if vm.canvasVisible {
                Button("关闭 Canvas") {
                    vm.canvasVisible = false
                }
                .buttonStyle(.bordered)
            } else {
                Button {
                    vm.canvasVisible = true
                } label: {
                    Image(systemName: "rectangle.split.2x1")
                        .foregroundStyle(Color.secondary)
                }
                .buttonStyle(.plain)
                .help("打开 Canvas")
            }

            // 侧边栏开关（移到右上角）
            Button {
                withAnimation(.easeInOut(duration: 0.18)) {
                    vm.sidebarVisible.toggle()
                }
            } label: {
                Image(systemName: vm.sidebarVisible ? "sidebar.left" : "sidebar.squares.left")
                    .foregroundStyle(Color.secondary)
            }
            .buttonStyle(.plain)
            .help(vm.sidebarVisible ? "隐藏侧边栏" : "显示侧边栏")
        }
        .padding(.horizontal, 12)
        .frame(height: 40)
        .background(SG.chrome(colorScheme))
    }

    // MARK: - Composer

    private var composerBar: some View {
        let isEmpty = inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return HStack(alignment: .bottom, spacing: 8) {
            ComposerView(text: $inputText, height: $composerHeight) {
                sendMessage()
            }
            .frame(height: composerHeight)

            if vm.isSending {
                Button { vm.stopSending() } label: {
                    Image(systemName: "stop.fill")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 28, height: 28)
                        .background(SG.danger)
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .help("停止")
            } else {
                Button { sendMessage() } label: {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(isEmpty ? Color.secondary : Color(nsColor: .windowBackgroundColor))
                        .frame(width: 28, height: 28)
                        .background(isEmpty ? Color.secondary.opacity(0.15) : Color.primary)
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .disabled(isEmpty)
                .help("发送 (⏎)")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(SG.surface(colorScheme))
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(SG.codeBorder(colorScheme), lineWidth: 1))
        )
        .padding(.horizontal, 48)
        .padding(.vertical, 10)
        .background(SG.bg(colorScheme))
    }

    private func sendMessage() {
        let text = inputText
        inputText = ""
        composerHeight = ComposerView.minHeight
        vm.send(text: text)
    }

    private struct ModelGroup { let provider: String; let models: [String] }

    private var groupedModels: [ModelGroup] {
        var result: [ModelGroup] = []
        var currentProvider = ""
        var currentModels: [String] = []
        for m in vm.availableModels {
            let provider = m.split(separator: "/", maxSplits: 1).first.map(String.init) ?? ""
            if provider != currentProvider {
                if !currentModels.isEmpty { result.append(ModelGroup(provider: currentProvider, models: currentModels)) }
                currentProvider = provider
                currentModels = []
            }
            currentModels.append(m)
        }
        if !currentModels.isEmpty { result.append(ModelGroup(provider: currentProvider, models: currentModels)) }
        return result
    }

    private func providerLabel(_ id: String) -> String {
        switch id {
        case "bailian":    return "百炼"
        case "deepseek":   return "DeepSeek"
        case "kimi":       return "Kimi"
        case "openrouter": return "OpenRouter"
        default:           return id
        }
    }

    // "provider/model" or "provider/org/model" → last path component
    private func shortModelName(_ fullId: String) -> String {
        let parts = fullId.split(separator: "/", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return fullId }
        return parts[1].split(separator: "/").last.map(String.init) ?? parts[1]
    }

    @ViewBuilder
    private func modalOverlay(dismiss: @escaping () -> Void) -> some View {
        Color.black.opacity(0.35)
            .ignoresSafeArea()
            .onTapGesture { dismiss() }
    }
}

// MARK: - View+modalCard

private extension View {
    func modalCard() -> some View {
        self.background(
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(nsColor: .windowBackgroundColor))
                .shadow(color: .black.opacity(0.25), radius: 24, x: 0, y: 8)
        )
        .transition(.opacity.combined(with: .scale(scale: 0.96, anchor: .center)))
        .animation(.easeOut(duration: 0.15), value: true)
    }
}

// MARK: - SearchPanel

struct SearchPanel: View {
    @ObservedObject var vm: ChatViewModel
    @Binding var isPresented: Bool
    @State private var query = ""
    @Environment(\.colorScheme) private var colorScheme

    private var filtered: [ConversationItem] {
        guard !query.isEmpty else { return vm.conversations }
        return vm.conversations.filter {
            ($0.title ?? "新对话").localizedCaseInsensitiveContains(query)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            // 搜索框
            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 15))
                    .foregroundStyle(.secondary)
                TextField("搜索对话…", text: $query)
                    .textFieldStyle(.plain)
                    .font(.system(size: 15))
                if !query.isEmpty {
                    Button { query = "" } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 16)

            Divider()

            if filtered.isEmpty {
                Text("无匹配对话")
                    .font(.system(size: 13))
                    .foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        if query.isEmpty {
                            sectionLabel("近期对话")
                        }
                        ForEach(filtered) { conv in
                            convRow(conv)
                        }
                    }
                    .padding(.vertical, 8)
                }
            }
        }
    }

    private func sectionLabel(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold))
            .tracking(0.4)
            .foregroundStyle(.tertiary)
            .padding(.horizontal, 20)
            .padding(.top, 8)
            .padding(.bottom, 4)
    }

    private func convRow(_ conv: ConversationItem) -> some View {
        Button {
            Task { await vm.selectConversation(conv) }
            isPresented = false
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "bubble.left")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                Text(conv.title ?? "新对话")
                    .font(.system(size: 13))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Spacer()
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 9)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - ModelConfigPanel

struct ModelConfigPanel: View {
    @ObservedObject var vm: ChatViewModel
    @Binding var isPresented: Bool

    var body: some View {
        VStack(spacing: 0) {
            // 标题栏
            HStack {
                Text("当前会话模型")
                    .font(.system(size: 14, weight: .semibold))
                Spacer()
                Button { isPresented = false } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .frame(width: 22, height: 22)
                        .background(Color.secondary.opacity(0.12))
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 20)
            .padding(.top, 18)
            .padding(.bottom, 14)

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Picker("", selection: $vm.selectedModel) {
                        Text("自动（路由决定）").tag("")
                        ForEach(vm.availableModels, id: \.self) { m in
                            Text(m.split(separator: "/").last.map(String.init) ?? m).tag(m)
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)

                    Text("仅影响本次会话。默认模型 / 默认选择 / 路由配置请到 ⌘, 「设置 → 模型管理 / 路由设置」。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)

                    Button {
                        NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
                        isPresented = false
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "gearshape")
                            Text("打开设置")
                        }
                    }
                }
                .padding(20)
            }

            Divider()

            HStack {
                Spacer()
                Button("完成") { isPresented = false }
                    .buttonStyle(.borderedProminent)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)
        }
    }

    private func settingGroup<C: View>(_ title: String, @ViewBuilder content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .tracking(0.3)
                .foregroundStyle(.secondary)
            content()
        }
    }
}
