import SwiftUI

/// 「RAG 管理」页（2026-05-08 新增）。
/// embedding / rerank 配置 + 策略 / backend picker（本期都只有 default）+
/// 测试 embedding + 最近一次成功 / 失败诊断卡片。
struct RAGManagementPage: View {
    @StateObject private var vm = RAGManagementViewModel()

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if vm.embeddingCandidates.isEmpty {
                        capabilityWarningBanner
                    }

                    Form {
                        embeddingSection
                        rerankSection
                        strategyBackendSection
                    }
                    .formStyle(.grouped)

                    diagnosticsCard
                }
                .padding()
            }
            footerBar
        }
        .task { await vm.loadIfNeeded() }
        .alert("修改 embedding 配置", isPresented: $vm.showDimensionsConfirm) {
            Button("取消", role: .cancel) { vm.cancelDimensionsChange() }
            Button("确认（需要重新索引）", role: .destructive) {
                vm.confirmDimensionsChange()
            }
        } message: {
            Text("Embedding 模型 / dimensions / provider 改了之后，已有的项目索引会失效，需要重新索引才能继续检索。是否继续？")
        }
    }

    // MARK: - Sections

    private var capabilityWarningBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "info.circle")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("没有任何 provider 标记了 embedding 能力")
                    .font(.system(size: 12, weight: .semibold))
                Text("去模型管理页右键你想用的模型选「标记为 embedding」；或先在供应商管理添加 bailian / openai / 本地 ollama 等支持 embedding 的 provider。")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
        }
        .padding(10)
        .background(Color.orange.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var embeddingSection: some View {
        Section {
            Picker(selection: $vm.draft.embedding.provider) {
                Text("（请选择）").tag("")
                ForEach(vm.embeddingProviderOptions, id: \.self) { pid in
                    Text(vm.providerLabel(pid)).tag(pid)
                }
            } label: {
                labelWithInfo("Embedding Provider",
                              info: "供 embed query / chunk 的 provider；切到任意配置了 OpenAI 兼容 /embeddings 的 provider 都行。")
            }
            .pickerStyle(.menu)

            Picker(selection: $vm.draft.embedding.model) {
                Text("（请选择）").tag("")
                ForEach(vm.embeddingModelOptions, id: \.self) { m in
                    Text(m).tag(m)
                }
            } label: {
                labelWithInfo("Embedding Model",
                              info: "只列当前 provider 下 capabilities 含 'embedding' 的模型。如果空：去模型管理页右键标记。")
            }
            .pickerStyle(.menu)
            .disabled(vm.draft.embedding.provider.isEmpty)

            HStack {
                labelWithInfo("Dimensions",
                              info: "向量维度。改 dimensions 会让现有索引失效——保存时确认。")
                Spacer()
                TextField("1024", value: $vm.draft.embedding.dimensions, formatter: vm.dimsFormatter)
                    .frame(width: 80)
                    .multilineTextAlignment(.trailing)
            }

            HStack {
                Button {
                    Task { await vm.runTestEmbedding() }
                } label: {
                    Label("测试 embedding", systemImage: "play.circle")
                }
                .buttonStyle(.bordered)
                .disabled(vm.testInFlight || vm.draft.embedding.provider.isEmpty)
                if let res = vm.testResult {
                    Text(res)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } header: {
            Text("Embedding")
        }
    }

    private var rerankSection: some View {
        Section {
            Picker(selection: $vm.draft.rerank.provider) {
                Text("（请选择）").tag("")
                ForEach(vm.allProviderIds, id: \.self) { pid in
                    Text(vm.providerLabel(pid)).tag(pid)
                }
            } label: {
                labelWithInfo("Rerank Provider", info: "rerank 服务来源；不强制和 embedding 同 provider。")
            }
            .pickerStyle(.menu)

            Picker(selection: $vm.draft.rerank.endpointKind) {
                Text("DashScope Native（百炼专用）").tag("dashscope-native")
                Text("OpenAI-Compat（Cohere 风格）").tag("openai-compat")
            } label: {
                labelWithInfo("Rerank 协议",
                              info: "DashScope native 走百炼原生 /rerank/text-rerank；OpenAI-Compat 调 {base_url}/rerank。")
            }
            .pickerStyle(.menu)

            Picker(selection: $vm.draft.rerank.model) {
                Text("（请选择 / 自填）").tag("")
                ForEach(vm.rerankModelOptions, id: \.self) { m in
                    Text(m).tag(m)
                }
            } label: {
                labelWithInfo("Rerank Model", info: "只列 capabilities 含 'rerank' 的；不在列表里就在右键模型管理页打 tag。")
            }
            .pickerStyle(.menu)
            .disabled(vm.draft.rerank.provider.isEmpty)

            if vm.draft.rerank.endpointKind == "dashscope-native" {
                HStack {
                    labelWithInfo("Endpoint URL Override（可选）",
                                  info: "默认 https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank。本地代理时填这里。")
                    Spacer()
                    TextField("默认", text: vm.urlOverrideBinding)
                        .frame(maxWidth: 280)
                        .textFieldStyle(.roundedBorder)
                }
            }
        } header: {
            Text("Rerank")
        }
    }

    private var strategyBackendSection: some View {
        Section {
            Picker(selection: $vm.draft.strategy.id) {
                ForEach(vm.strategies, id: \.self) { s in
                    Text(s).tag(s)
                }
            } label: {
                labelWithInfo("策略",
                              info: "本期只有 default（embed → coarse topK → rerank → top_n）。TreeRAG / GraphRAG / Hybrid 等独立立 plan，未来在这里出现。")
            }
            .pickerStyle(.menu)

            Picker(selection: $vm.draft.backend.id) {
                ForEach(vm.backends, id: \.self) { b in
                    Text(b).tag(b)
                }
            } label: {
                labelWithInfo("Backend",
                              info: "向量存储后端。本期只有 sqlite-brute-force（≤1 万 chunk 不卡）。sqlite-vec / FAISS / Qdrant 等独立立 plan。")
            }
            .pickerStyle(.menu)
        } header: {
            Text("策略 / Backend")
        }
    }

    private var diagnosticsCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("RAG 诊断")
                    .font(.system(size: 12, weight: .semibold))
                Spacer()
                Button {
                    Task { await vm.refreshDiagnostics() }
                } label: { Image(systemName: "arrow.clockwise").imageScale(.small) }
                    .buttonStyle(.plain)
                    .help("刷新")
            }

            if vm.diag == nil {
                Text("尚未发起过 RAG 调用").font(.system(size: 11)).foregroundStyle(.tertiary)
            } else {
                diagRow(title: "embed 最近成功", body: vm.embedOkSummary)
                diagRow(title: "embed 最近失败", body: vm.embedErrSummary, error: true)
                diagRow(title: "rerank 最近成功", body: vm.rerankOkSummary)
                diagRow(title: "rerank 最近失败", body: vm.rerankErrSummary, error: true)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.secondary.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func diagRow(title: String, body: String?, error: Bool = false) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(title)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(error ? .secondary : .primary)
                .frame(width: 120, alignment: .leading)
            Text(body ?? "—")
                .font(.system(size: 11))
                .foregroundStyle(error ? .red.opacity(0.8) : .secondary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }

    private var footerBar: some View {
        HStack(spacing: 12) {
            if let status = vm.statusMessage {
                Text(status)
                    .font(.caption)
                    .foregroundStyle(vm.statusIsError ? .red : .secondary)
            }
            Spacer()
            Button("默认") { Task { await vm.reload() } }
                .keyboardShortcut("r", modifiers: [.command])
            Button("保存") { vm.requestSave() }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut("s", modifiers: [.command])
                .disabled(vm.isSaving)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.bar)
    }

    private func labelWithInfo(_ title: String, info: String) -> some View {
        HStack(spacing: 4) {
            Text(title)
            InfoBadge(text: info)
        }
    }
}

// MARK: - ViewModel

@MainActor
final class RAGManagementViewModel: ObservableObject {

    @Published var draft: RagConfigPayload = .empty
    @Published var embeddingCandidates: [RagModelCandidate] = []
    @Published var rerankCandidates: [RagModelCandidate] = []
    @Published var providers: [RagProviderCandidate] = []
    @Published var strategies: [String] = ["default"]
    @Published var backends: [String] = ["sqlite-brute-force"]
    @Published var statusMessage: String?
    @Published var statusIsError: Bool = false
    @Published var isSaving: Bool = false
    @Published var testInFlight: Bool = false
    @Published var testResult: String?
    @Published var diag: RagDiagnosticsResponse?
    @Published var showDimensionsConfirm: Bool = false

    private let api = ProvidersAPI()
    private var loaded = false
    private var savedSnapshot: RagConfigPayload = .empty

    let dimsFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.minimum = 1
        f.maximum = 16384
        f.allowsFloats = false
        return f
    }()

    var allProviderIds: [String] { providers.map(\.id) }

    var embeddingProviderOptions: [String] {
        // 至少有一个 embedding 候选的 provider
        Array(Set(embeddingCandidates.map(\.provider))).sorted()
    }

    var embeddingModelOptions: [String] {
        embeddingCandidates
            .filter { $0.provider == draft.embedding.provider }
            .map(\.model)
    }

    var rerankModelOptions: [String] {
        rerankCandidates
            .filter { $0.provider == draft.rerank.provider }
            .map(\.model)
    }

    var urlOverrideBinding: Binding<String> {
        Binding(
            get: { self.draft.rerank.endpointUrlOverride ?? "" },
            set: { newVal in
                let trimmed = newVal.trimmingCharacters(in: .whitespacesAndNewlines)
                self.draft.rerank.endpointUrlOverride = trimmed.isEmpty ? nil : trimmed
            }
        )
    }

    func providerLabel(_ id: String) -> String {
        switch id {
        case "bailian":    return "百炼（阿里云）"
        case "deepseek":   return "DeepSeek"
        case "kimi":       return "Kimi"
        case "openrouter": return "OpenRouter"
        case "ollama":     return "Ollama"
        case "lmstudio":   return "LM Studio"
        case "mlx":        return "MLX"
        case "llamacpp":   return "llama.cpp"
        case "openai":     return "OpenAI"
        case "tavily":     return "Tavily"
        case "":           return "（未选）"
        default:           return id
        }
    }

    var embedOkSummary: String? {
        guard let s = diag?.embedOk else { return nil }
        var parts = [
            "\(providerLabel(s.provider)) / \(s.model)",
            "\(s.dimensions) dim",
            "\(s.totalTexts) 条",
        ]
        if s.latencyMs > 0 { parts.append("\(s.latencyMs)ms") }
        if s.batchSize > 0 { parts.append("batch=\(s.batchSize)") }
        parts.append(timeAgo(s.timestamp))
        return parts.joined(separator: " · ")
    }
    var embedErrSummary: String? {
        guard let s = diag?.embedErr else { return nil }
        return "\(s.kind) · \(s.message) · \(timeAgo(s.timestamp))"
    }
    var rerankOkSummary: String? {
        guard let s = diag?.rerankOk else { return nil }
        var parts = [
            "\(providerLabel(s.provider)) / \(s.model)",
            s.endpointKind,
            "\(s.docCount) 条",
        ]
        if s.latencyMs > 0 { parts.append("\(s.latencyMs)ms") }
        if s.fallbackUsed { parts.append("fallback") }
        parts.append(timeAgo(s.timestamp))
        return parts.joined(separator: " · ")
    }
    var rerankErrSummary: String? {
        guard let s = diag?.rerankErr else { return nil }
        return "\(s.kind) · \(s.message) · \(timeAgo(s.timestamp))"
    }

    func loadIfNeeded() async {
        guard !loaded else { return }
        loaded = true
        await reload()
    }

    func reload() async {
        do {
            let resp = try await api.getRagConfig()
            draft = resp.config
            savedSnapshot = resp.config
            providers = resp.providers
            embeddingCandidates = resp.embeddingCandidates
            rerankCandidates = resp.rerankCandidates
            strategies = resp.strategies.isEmpty ? ["default"] : resp.strategies
            backends = resp.backends.isEmpty ? ["sqlite-brute-force"] : resp.backends
            statusMessage = nil
            statusIsError = false
        } catch {
            statusMessage = "加载失败：\(error.localizedDescription)"
            statusIsError = true
        }
        await refreshDiagnostics()
    }

    func refreshDiagnostics() async {
        do {
            diag = try await api.ragDiagnostics()
        } catch {
            // 静默：诊断非关键
        }
    }

    func runTestEmbedding() async {
        testInFlight = true
        testResult = nil
        defer { testInFlight = false }
        do {
            let resp = try await api.testEmbedding("ping")
            testResult = "✅ \(resp.model) · \(resp.dimensions) dim · \(resp.elapsedMs)ms"
        } catch {
            testResult = "❌ \(error.localizedDescription)"
        }
        await refreshDiagnostics()
    }

    func requestSave() {
        // 凡 embedding 的 provider/model/dimensions/endpoint_kind 任一变了就弹确认
        let s = savedSnapshot.embedding
        let d = draft.embedding
        if s.provider != d.provider || s.model != d.model
            || s.dimensions != d.dimensions || s.endpointKind != d.endpointKind {
            showDimensionsConfirm = true
            return
        }
        Task { await save() }
    }

    func confirmDimensionsChange() {
        Task { await save() }
    }

    func cancelDimensionsChange() {
        // 仅复位 embedding 区到 saved，rerank/strategy/backend 改动保留
        draft.embedding = savedSnapshot.embedding
    }

    private func save() async {
        isSaving = true
        defer { isSaving = false }
        do {
            let resp = try await api.putRagConfig(draft)
            draft = resp.config
            savedSnapshot = resp.config
            statusMessage = "已保存。fingerprint=\(resp.fingerprint)"
            statusIsError = false
            // 触发 kernel 重新解析（catalog/fingerprint 一致性影响检索）
            _ = await SettingsKernelReload.providers()
        } catch {
            statusMessage = "保存失败：\(error.localizedDescription)"
            statusIsError = true
        }
    }

    private func timeAgo(_ ts: Double) -> String {
        let elapsed = Date().timeIntervalSince1970 - ts
        if elapsed < 60 { return "\(Int(elapsed))s 前" }
        if elapsed < 3600 { return "\(Int(elapsed / 60)) 分钟前" }
        if elapsed < 86400 { return "\(Int(elapsed / 3600)) 小时前" }
        return "\(Int(elapsed / 86400)) 天前"
    }
}

extension RagConfigPayload {
    static let empty = RagConfigPayload(
        version: 1,
        embedding: .init(provider: "", model: "", dimensions: 1024, endpointKind: "openai-compat"),
        rerank: .init(provider: "", model: "", endpointKind: "dashscope-native", endpointUrlOverride: nil),
        strategy: .init(id: "default"),
        backend: .init(id: "sqlite-brute-force")
    )
}
