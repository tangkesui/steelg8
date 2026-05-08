import SwiftUI

/// 「路由设置」页（2026-05-08 新增，**占位实现**）。
///
/// 当前路由 `Python/router.py` 是 v0.3 简化版的 4 层瀑布
/// （explicit / default / fallback / mock）；不是智能路由。本页只暴露目前
/// 隐藏在 providers.json 里的两个开关：
///   1. default_provider —— tie-breaker（同 model id 多 provider 时谁赢）
///   2. providers[] 数组顺序 —— fallback 优先级
///
/// 未来升级到模型编排（路由 v2，支持 sub-agent / 任务级别 model picking）
/// 时整页替换。
struct RouterPage: View {

    @StateObject private var vm = RouterPageViewModel()

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    placeholderBanner

                    Form {
                        Section {
                            Picker(selection: $vm.defaultProvider) {
                                Text("无（按数组顺序自动）").tag("")
                                ForEach(vm.providerOrder, id: \.self) { pid in
                                    Text(vm.providerLabel(pid)).tag(pid)
                                }
                            } label: {
                                labelWithInfo(
                                    "默认供应商",
                                    info: "tie-breaker：当 default_model 这个 id 同时被多个 provider 注册时（例如 deepseek-chat 既在 DeepSeek 又在 OpenRouter 出现），路由首选这家。留空则按下面的 fallback 顺序。"
                                )
                            }
                            .pickerStyle(.menu)
                        }

                        Section {
                            VStack(alignment: .leading, spacing: 8) {
                                labelWithInfo(
                                    "Fallback 优先级",
                                    info: "explicit / default 都没命中时，按这个顺序找首个就绪 provider。可拖拽重排（macOS Form 偶尔抖动，必要时用上下箭头按钮）。"
                                )
                                List {
                                    ForEach(vm.providerOrder, id: \.self) { pid in
                                        HStack {
                                            Image(systemName: "line.horizontal.3")
                                                .foregroundStyle(.tertiary)
                                            Text(vm.providerLabel(pid))
                                                .font(.system(size: 12.5))
                                        }
                                    }
                                    .onMove { src, dest in vm.reorder(from: src, to: dest) }
                                }
                                .listStyle(.plain)
                                .frame(minHeight: 140, maxHeight: 220)
                            }
                        }
                    }
                    .formStyle(.grouped)

                    routerStateCard
                }
                .padding()
            }
            footerBar
        }
        .task { await vm.loadIfNeeded() }
    }

    private var placeholderBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("占位页：当前路由是 v0.3 简化的 4 层瀑布")
                    .font(.system(size: 12, weight: .semibold))
                Text("未来会重做（模型编排 / sub-agent / 任务级 model picking）。本页本身可被整片替换，配置不丢。")
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

    private var routerStateCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("最近一次路由命中")
                    .font(.system(size: 12, weight: .semibold))
                Spacer()
                Button {
                    Task { await vm.refreshState() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .imageScale(.small)
                }
                .buttonStyle(.plain)
                .help("刷新")
            }
            if let last = vm.lastDecision {
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 4) {
                    GridRow {
                        Text("layer").foregroundStyle(.secondary)
                        Text(last.layer).font(.system(size: 11.5, design: .monospaced))
                    }
                    GridRow {
                        Text("provider").foregroundStyle(.secondary)
                        Text(last.provider).font(.system(size: 11.5, design: .monospaced))
                    }
                    GridRow {
                        Text("model").foregroundStyle(.secondary)
                        Text(last.model).font(.system(size: 11.5, design: .monospaced))
                    }
                    GridRow {
                        Text("reason").foregroundStyle(.secondary)
                        Text(last.reason).font(.system(size: 11.5))
                    }
                }
                .font(.system(size: 11.5))
            } else {
                Text("还没有命中记录（刚启动或 kernel 重启过）。")
                    .font(.system(size: 11.5))
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.secondary.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
            Button("保存") { Task { await vm.save() } }
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
final class RouterPageViewModel: ObservableObject {

    @Published var defaultProvider: String = ""
    @Published var providerOrder: [String] = []
    @Published var lastDecision: RouterStateResponse.RouterDecision?
    @Published var statusMessage: String?
    @Published var statusIsError: Bool = false
    @Published var isSaving: Bool = false

    private let api = ProvidersAPI()
    private var loaded = false
    private var initialOrder: [String] = []
    private var initialDefault: String = ""

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
        case "tavily":     return "Tavily"
        default:           return id
        }
    }

    func loadIfNeeded() async {
        guard !loaded else { return }
        loaded = true
        await reload()
    }

    func reload() async {
        statusMessage = nil
        statusIsError = false
        do {
            let cfg = try ProviderConfigStore.shared.load()
            // 默认 provider 来自 providers.json，但 ProviderConfigStore.load() 不直接返
            // 该字段；先从 entries 推断（取首条或读原始 JSON）。MVP 走读 providers.json 直读。
            let raw = try readProvidersDoc()
            defaultProvider = (raw["default_provider"] as? String) ?? ""
            initialDefault = defaultProvider
            providerOrder = cfg.entries.map(\.name)
            initialOrder = providerOrder
        } catch {
            statusMessage = "读取失败：\(error.localizedDescription)"
            statusIsError = true
        }
        await refreshState()
    }

    func refreshState() async {
        do {
            let resp = try await api.routerState()
            lastDecision = resp.last
        } catch {
            // 静默：路由状态非关键
        }
    }

    func reorder(from src: IndexSet, to dest: Int) {
        providerOrder.move(fromOffsets: src, toOffset: dest)
    }

    func save() async {
        isSaving = true
        defer { isSaving = false }
        do {
            if defaultProvider != initialDefault {
                _ = try await api.updateDefaultProvider(defaultProvider)
                initialDefault = defaultProvider
            }
            if providerOrder != initialOrder {
                _ = try await api.updateProviderOrder(providerOrder)
                initialOrder = providerOrder
            }
            _ = await SettingsKernelReload.providers()
            statusMessage = "已保存并热加载完成。"
            statusIsError = false
        } catch {
            statusMessage = "保存失败：\(error.localizedDescription)"
            statusIsError = true
        }
    }

    private func readProvidersDoc() throws -> [String: Any] {
        let url = KernelConfig.userConfigDirectoryURL.appending(path: "providers.json")
        let data = try Data(contentsOf: url)
        let obj = try JSONSerialization.jsonObject(with: data)
        return (obj as? [String: Any]) ?? [:]
    }
}
