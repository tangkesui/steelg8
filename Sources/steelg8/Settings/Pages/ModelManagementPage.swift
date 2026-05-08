import SwiftUI

/// Optional 字段的 SortComparator：nil 永远排最后（不论升降序），
/// 非 nil 之间按 order 排。SwiftUI Table 给可空字段用。
struct OptionalLastComparator<T: Comparable>: SortComparator {
    typealias Compared = T?
    var order: SortOrder = .forward

    func compare(_ lhs: T?, _ rhs: T?) -> ComparisonResult {
        switch (lhs, rhs) {
        case (nil, nil): return .orderedSame
        case (nil, _):   return .orderedDescending
        case (_, nil):   return .orderedAscending
        case let (l?, r?):
            let base: ComparisonResult
            if l < r { base = .orderedAscending }
            else if l > r { base = .orderedDescending }
            else { base = .orderedSame }
            switch order {
            case .forward: return base
            case .reverse:
                if base == .orderedAscending { return .orderedDescending }
                if base == .orderedDescending { return .orderedAscending }
                return .orderedSame
            @unknown default: return base
            }
        }
    }
}

/// 「模型管理」页（2026-05-08 新增）。
///
/// 默认模型 picker 只列当前 provider 范围内已勾选的模型；
/// 表格点击表头排序，右键表头显隐列，每次进入 task 都 reload catalog。
/// 拉取 catalog 走本地（不打网络）；网络刷新在「供应商管理」页做。
/// 价格列右键编辑：写 pricing_source=verified 进 catalog，refresh 不会覆盖。
struct ModelManagementPage: View {

    @StateObject private var vm = ModelManagementViewModel()

    /// Table 排序状态——本会话 in-memory，不持久化（默认按发布时间新→旧）。
    @State private var sortOrder: [KeyPathComparator<CatalogRow>] = [
        KeyPathComparator(\CatalogRow.createdAt,
                          comparator: OptionalLastComparator<Int>(),
                          order: .reverse)
    ]

    /// 表头右键的列显隐 / 排序自定义状态。@SceneStorage 在 settings 窗口
    /// 关闭后再打开时仍保留（应用退出后会被重置——这是 SwiftUI 的限制）。
    @SceneStorage("modelMgmt.tableColumns")
    private var columnCustomization: TableColumnCustomization<CatalogRow>

    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section {
                    Picker(selection: $vm.filterProvider) {
                        Text("全部供应商").tag("")
                        ForEach(vm.providersInOrder, id: \.self) { pid in
                            Text(vm.providerLabel(pid)).tag(pid)
                        }
                    } label: {
                        labelWithInfo(
                            "供应商",
                            info: "选一个供应商查看 / 编辑它的模型；切到「全部」跨供应商一起看。"
                        )
                    }
                    .pickerStyle(.menu)

                    Picker(selection: $vm.defaultModel) {
                        Text("自动（由路由决定）").tag("")
                        // 当前 default 不在勾选范围内时仍显示原值，让用户看到现状
                        if !vm.defaultModel.isEmpty,
                           !vm.defaultModelChoices.contains(where: { $0.modelId == vm.defaultModel }) {
                            Text("\(vm.defaultModel)  ·  (不在当前供应商范围)")
                                .foregroundStyle(.secondary)
                                .tag(vm.defaultModel)
                        }
                        ForEach(vm.defaultModelChoices, id: \.id) { row in
                            Text("\(row.modelId)  ·  \(vm.providerLabel(row.providerId))")
                                .tag(row.modelId)
                        }
                    } label: {
                        labelWithInfo(
                            "默认模型",
                            info: "路由层 2 用的模型——显式没指定时走它。只列当前已勾选的模型；想加候选先去下面表格勾。"
                        )
                    }
                    .pickerStyle(.menu)
                    .onChange(of: vm.defaultModel) { _, _ in
                        Task { await vm.refreshDefaultRoute() }
                    }

                    if vm.defaultModelChoices.isEmpty {
                        Text("当前供应商范围内没有勾选过的模型。先去下表勾选，默认模型才能选。")
                            .font(.system(size: 11))
                            .foregroundStyle(.orange)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    if !vm.defaultModel.isEmpty, let route = vm.defaultRoute {
                        HStack(spacing: 6) {
                            Image(systemName: "arrow.triangle.branch")
                                .font(.system(size: 10))
                                .foregroundStyle(.secondary)
                            Text("通过 \(vm.providerLabel(route.provider)) 调用")
                                .font(.system(size: 11))
                                .foregroundStyle(.secondary)
                            if route.layer != "explicit" && route.layer != "default" {
                                Text("· \(route.layer)").font(.system(size: 11)).foregroundStyle(.tertiary)
                            }
                            Spacer()
                        }
                    }
                }
            }
            .formStyle(.grouped)
            .padding(.horizontal)
            .padding(.top, 12)
            .frame(maxHeight: 160)

            Divider()

            modelTable
        }
        .safeAreaInset(edge: .bottom) { footerBar }
        .task { await vm.reload() }
    }

    private var modelTable: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text("\(vm.selectedCount) / \(vm.visibleRows.count) 个模型选中")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("全选") { vm.selectAll(true) }
                Button("全不选") { vm.selectAll(false) }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(.bar)

            Divider()

            Table(
                vm.visibleRows.sorted(using: sortOrder),
                selection: .constant(Set<CatalogRow.ID>()),
                sortOrder: $sortOrder,
                columnCustomization: $columnCustomization
            ) {
                TableColumn("✓") { row in
                    Toggle("", isOn: vm.bindingForSelected(row))
                        .labelsHidden()
                }
                .width(28)
                .customizationID("checkbox")
                .disabledCustomizationBehavior(.visibility)

                TableColumn("Provider", value: \.providerId) { row in
                    Text(vm.providerLabel(row.providerId))
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                }
                .width(min: 90, ideal: 110)
                .customizationID("provider")

                TableColumn("模型 id", value: \.modelId) { row in
                    modelIdCell(row)
                }
                .width(min: 200, ideal: 280)
                .customizationID("modelId")
                .disabledCustomizationBehavior(.visibility)

                TableColumn("Capability") { row in
                    capabilityCell(row)
                }
                .width(min: 110, ideal: 140)
                .customizationID("capability")

                TableColumn("Via") { row in
                    viaCell(row)
                }
                .width(min: 60, ideal: 80)
                .customizationID("via")
                .defaultVisibility(.hidden)

                TableColumn("输入价 / Mtok",
                            value: \.inputPrice,
                            comparator: OptionalLastComparator<Double>()) { row in
                    pricingCell(row, isInput: true)
                }
                .width(min: 110, ideal: 130)
                .customizationID("input")

                TableColumn("输出价 / Mtok",
                            value: \.outputPrice,
                            comparator: OptionalLastComparator<Double>()) { row in
                    pricingCell(row, isInput: false)
                }
                .width(min: 110, ideal: 130)
                .customizationID("output")

                TableColumn("发布时间",
                            value: \.createdAt,
                            comparator: OptionalLastComparator<Int>()) { row in
                    Text(row.createdAtDisplay)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
                .width(min: 90, ideal: 110)
                .customizationID("createdAt")
            }
        }
    }

    @ViewBuilder
    private func modelIdCell(_ row: CatalogRow) -> some View {
        Text(row.modelId)
            .font(.system(size: 12, design: .monospaced))
            .lineLimit(1)
            .truncationMode(.middle)
            .contextMenu {
                Section("Capability") {
                    capabilityMenuItem(row, cap: "embedding")
                    capabilityMenuItem(row, cap: "rerank")
                    capabilityMenuItem(row, cap: "vision")
                    capabilityMenuItem(row, cap: "tool-use")
                }
            }
    }

    @ViewBuilder
    private func capabilityMenuItem(_ row: CatalogRow, cap: String) -> some View {
        let on = row.capabilities.contains(cap)
        Button {
            Task { await vm.toggleCapability(rowId: row.id, capability: cap, enabled: !on) }
        } label: {
            HStack {
                Image(systemName: on ? "checkmark" : "")
                Text(cap)
            }
        }
    }

    @ViewBuilder
    private func capabilityCell(_ row: CatalogRow) -> some View {
        HStack(spacing: 4) {
            ForEach(row.capabilities, id: \.self) { c in
                Text(c)
                    .font(.system(size: 9.5))
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1.5)
                    .background(capabilityChipColor(c).opacity(0.18))
                    .foregroundStyle(capabilityChipColor(c))
                    .clipShape(Capsule())
            }
            Spacer(minLength: 0)
        }
    }

    private func capabilityChipColor(_ c: String) -> Color {
        switch c {
        case "embedding": return .purple
        case "rerank":    return .orange
        case "vision":    return .blue
        case "tool-use":  return .teal
        default:          return .secondary
        }
    }

    @ViewBuilder
    private func viaCell(_ row: CatalogRow) -> some View {
        if let label = vm.viaLabel(for: row) {
            Text(label)
                .font(.system(size: 10.5))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.tail)
        } else {
            Text("")
        }
    }

    private func pricingCell(_ row: CatalogRow, isInput: Bool) -> some View {
        let value = isInput ? row.inputPrice : row.outputPrice
        let isVerified = row.pricingSource == "verified"
        return HStack(spacing: 4) {
            if let v = value {
                Text(String(format: "$%.3f", v))
                    .font(.system(size: 11.5, design: .monospaced))
            } else {
                Text("—")
                    .foregroundStyle(.tertiary)
            }
            if isVerified {
                Image(systemName: "checkmark.seal.fill")
                    .font(.system(size: 9))
                    .foregroundStyle(.green.opacity(0.8))
                    .help("verified · 用户手填或上游 / 爬虫拿到")
            } else if value != nil {
                Image(systemName: "circle.dotted")
                    .font(.system(size: 9))
                    .foregroundStyle(.secondary.opacity(0.6))
                    .help("fallback · 来自静态表，可能不准")
            } else {
                Image(systemName: "questionmark.circle")
                    .font(.system(size: 9))
                    .foregroundStyle(.orange.opacity(0.7))
                    .help("未知 · 可在右键编辑写入 verified")
            }
            Spacer(minLength: 0)
        }
        .contextMenu {
            Button("编辑价格…") { vm.beginEditPricing(row.id) }
            if isVerified {
                Button("恢复 fallback") { Task { await vm.resetPricing(row.id) } }
            }
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
            Button("保存") { Task { await vm.save() } }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut("s", modifiers: [.command])
                .disabled(vm.isSaving)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.bar)
        .sheet(item: $vm.editingPricing) { editingTarget in
            PricingEditorSheet(
                modelId: editingTarget.modelId,
                input: editingTarget.input,
                output: editingTarget.output
            ) { input, output in
                Task { await vm.applyPricingEdit(editingTarget, input: input, output: output) }
            }
        }
    }

    private func labelWithInfo(_ title: String, info: String) -> some View {
        HStack(spacing: 4) {
            Text(title)
            InfoBadge(text: info)
        }
    }
}

// MARK: - Row

struct CatalogRow: Identifiable, Equatable {
    let id: String   // "<provider_id>::<model_id>"
    let providerId: String
    let modelId: String
    var selected: Bool
    var inputPrice: Double?
    var outputPrice: Double?
    var pricingSource: String  // verified / fallback
    var createdAt: Int?
    var capabilities: [String]

    var createdAtDisplay: String {
        guard let ts = createdAt else { return "—" }
        let date = Date(timeIntervalSince1970: TimeInterval(ts))
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }
}

struct PricingEditTarget: Identifiable {
    var id: String { rowId }
    let rowId: String
    let providerId: String
    let modelId: String
    let input: Double?
    let output: Double?
}

// MARK: - ViewModel

@MainActor
final class ModelManagementViewModel: ObservableObject {

    @Published var defaultModel: String = ""
    @Published var providersInOrder: [String] = []
    @Published var modelsByProvider: [String: [CatalogRow]] = [:]
    @Published var allRows: [CatalogRow] = []
    @Published var filterProvider: String = ""   // "" = 全部
    @Published var defaultRoute: ResolveModelResponse?
    @Published var statusMessage: String?
    @Published var statusIsError: Bool = false
    @Published var isSaving: Bool = false
    @Published var editingPricing: PricingEditTarget?

    private let api = ProvidersAPI()

    /// 当前 provider 过滤下的可见行（若 filterProvider == "" 则全部）。
    var visibleRows: [CatalogRow] {
        guard !filterProvider.isEmpty else { return allRows }
        return allRows.filter { $0.providerId == filterProvider }
    }

    var selectedCount: Int { visibleRows.filter(\.selected).count }

    /// 默认模型 picker 的可选项：仅列**勾选过**的模型（selected:true）。
    /// 受 filterProvider 影响；"" 时跨 provider 的 selected 集合。
    var defaultModelChoices: [CatalogRow] {
        let pool = visibleRows.filter { $0.selected }
        // 同 model id 在多个 provider 下出现时，去重保留首条（用 modelId 当 picker tag）
        var seen = Set<String>()
        var out: [CatalogRow] = []
        for r in pool.sorted(by: { (a, b) in
            if a.providerId != b.providerId { return a.providerId < b.providerId }
            return a.modelId < b.modelId
        }) {
            if seen.insert(r.modelId).inserted {
                out.append(r)
            }
        }
        return out
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
        case "tavily":     return "Tavily"
        default:           return id
        }
    }

    func reload() async {
        statusMessage = nil
        statusIsError = false
        do {
            // 默认模型来源：providers.json
            let cfg = try ProviderConfigStore.shared.load()
            defaultModel = cfg.defaultModel
            providersInOrder = cfg.entries
                .filter { $0.kind != "tool" }
                .map { $0.name }

            // catalog 数据：每个 provider 单独读一次（避免在 /providers 里 inflate）
            var byProvider: [String: [CatalogRow]] = [:]
            var combined: [CatalogRow] = []
            for pid in providersInOrder {
                let resp = try await fetchCatalog(pid)
                let rows: [CatalogRow] = resp.models.map { m in
                    CatalogRow(
                        id: "\(pid)::\(m.id)",
                        providerId: pid,
                        modelId: m.id,
                        selected: m.selected,
                        inputPrice: m.pricingPerMToken?.input,
                        outputPrice: m.pricingPerMToken?.output,
                        pricingSource: m.pricingSource ?? "fallback",
                        createdAt: m.createdAt,
                        capabilities: m.capabilities ?? ["chat"]
                    )
                }
                byProvider[pid] = rows
                combined.append(contentsOf: rows)
            }
            modelsByProvider = byProvider
            allRows = combined
        } catch {
            statusMessage = "读取失败：\(error.localizedDescription)"
            statusIsError = true
        }
        await refreshDefaultRoute()
    }

    /// "via X" 标签：仅当同 model id 在 visible 范围内出现于多个 provider 时返非空。
    /// 显示规则：tie-breaker（路由会用 default_provider）→ 标"自家"；其它 provider 标"代理"。
    func viaLabel(for row: CatalogRow) -> String? {
        let same = visibleRows.filter { $0.modelId == row.modelId }
        guard same.count > 1 else { return nil }
        // route 解析能拿到 default_provider 的话用它判断；否则用 row.providerId 的字面归属
        if let routeProvider = defaultRoute?.provider, !routeProvider.isEmpty,
           defaultRoute?.model == row.modelId {
            return row.providerId == routeProvider ? "自家" : "代理"
        }
        return "代理 / 自家"
    }

    func toggleCapability(rowId: String, capability: String, enabled: Bool) async {
        guard let row = allRows.first(where: { $0.id == rowId }) else { return }
        do {
            _ = try await api.toggleCapability(
                provider: row.providerId,
                modelId: row.modelId,
                capability: capability,
                enabled: enabled,
            )
            if let idx = allRows.firstIndex(where: { $0.id == rowId }) {
                var caps = allRows[idx].capabilities
                if enabled {
                    if !caps.contains(capability) { caps.append(capability) }
                } else {
                    caps.removeAll { $0 == capability }
                    if caps.isEmpty { caps = ["chat"] }
                }
                allRows[idx].capabilities = caps
            }
            statusMessage = enabled ? "已标记 \(capability)" : "已取消 \(capability)"
            statusIsError = false
        } catch {
            statusMessage = "标记失败：\(error.localizedDescription)"
            statusIsError = true
        }
    }

    func refreshDefaultRoute() async {
        let model = defaultModel.trimmingCharacters(in: .whitespacesAndNewlines)
        if model.isEmpty {
            defaultRoute = nil
            return
        }
        do {
            defaultRoute = try await api.resolveModel(model)
        } catch {
            // 静默失败：route 标签是 nice-to-have
            defaultRoute = nil
        }
    }

    private func fetchCatalog(_ pid: String) async throws -> CatalogReadResponse {
        do {
            return try await api.readCatalog(provider: pid)
        } catch ProvidersAPIError.badStatus(404, _) {
            // catalog 还没建好，返一个空响应
            return CatalogReadResponse(ok: true, name: pid, fetchedAt: nil, models: [])
        }
    }

    func bindingForSelected(_ row: CatalogRow) -> Binding<Bool> {
        Binding(
            get: {
                self.allRows.first(where: { $0.id == row.id })?.selected ?? false
            },
            set: { newValue in
                if let idx = self.allRows.firstIndex(where: { $0.id == row.id }) {
                    self.allRows[idx].selected = newValue
                }
            }
        )
    }

    func selectAll(_ value: Bool) {
        // 只对当前 filter 范围内的行操作
        let scope: Set<String> = Set(visibleRows.map(\.id))
        for i in allRows.indices where scope.contains(allRows[i].id) {
            allRows[i].selected = value
        }
    }

    func beginEditPricing(_ rowId: String) {
        guard let row = allRows.first(where: { $0.id == rowId }) else { return }
        editingPricing = PricingEditTarget(
            rowId: row.id,
            providerId: row.providerId,
            modelId: row.modelId,
            input: row.inputPrice,
            output: row.outputPrice
        )
    }

    func applyPricingEdit(
        _ target: PricingEditTarget,
        input: Double?,
        output: Double?
    ) async {
        do {
            _ = try await api.updateCatalogPricing(
                provider: target.providerId,
                modelId: target.modelId,
                input: input,
                output: output
            )
            if let idx = allRows.firstIndex(where: { $0.id == target.rowId }) {
                allRows[idx].inputPrice = input
                allRows[idx].outputPrice = output
                allRows[idx].pricingSource = "verified"
            }
            statusMessage = "价格已保存为 verified。"
            statusIsError = false
        } catch {
            statusMessage = "保存价格失败：\(error.localizedDescription)"
            statusIsError = true
        }
        editingPricing = nil
    }

    func resetPricing(_ rowId: String) async {
        guard let row = allRows.first(where: { $0.id == rowId }) else { return }
        do {
            _ = try await api.resetCatalogPricing(
                provider: row.providerId, modelId: row.modelId
            )
            if let idx = allRows.firstIndex(where: { $0.id == row.id }) {
                allRows[idx].inputPrice = nil
                allRows[idx].outputPrice = nil
                allRows[idx].pricingSource = "fallback"
            }
            statusMessage = "已恢复 fallback。"
            statusIsError = false
        } catch {
            statusMessage = "恢复失败：\(error.localizedDescription)"
            statusIsError = true
        }
    }

    func save() async {
        isSaving = true
        defer { isSaving = false }
        do {
            // 写每个 provider 的 selected 集合
            for pid in providersInOrder {
                let selectedIds = allRows
                    .filter { $0.providerId == pid && $0.selected }
                    .map(\.modelId)
                _ = try await api.updateCatalogSelection(
                    provider: pid, modelIds: selectedIds
                )
            }
            // 写默认模型（providers.json）
            try ProviderConfigStore.shared.saveDefaultModelPreservingEntries(
                defaultModel.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            // 热加载
            _ = await SettingsKernelReload.providers()
            statusMessage = "已保存并热加载完成。"
            statusIsError = false
        } catch {
            statusMessage = "保存失败：\(error.localizedDescription)"
            statusIsError = true
        }
    }
}

// MARK: - Pricing Editor Sheet

private struct PricingEditorSheet: View {
    let modelId: String
    @State var input: Double?
    @State var output: Double?
    let onSave: (Double?, Double?) -> Void
    @Environment(\.dismiss) private var dismiss

    @State private var inputText: String
    @State private var outputText: String

    init(modelId: String, input: Double?, output: Double?, onSave: @escaping (Double?, Double?) -> Void) {
        self.modelId = modelId
        self._input = State(initialValue: input)
        self._output = State(initialValue: output)
        self._inputText = State(initialValue: input.map { String(format: "%.4f", $0) } ?? "")
        self._outputText = State(initialValue: output.map { String(format: "%.4f", $0) } ?? "")
        self.onSave = onSave
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("编辑价格")
                .font(.headline)
            Text(modelId)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(2)

            VStack(alignment: .leading, spacing: 6) {
                Text("输入价（USD per Mtok）").font(.caption)
                TextField("如 0.5", text: $inputText)
                    .textFieldStyle(.roundedBorder)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("输出价（USD per Mtok）").font(.caption)
                TextField("如 1.5", text: $outputText)
                    .textFieldStyle(.roundedBorder)
            }
            Text("保存后会标记为 verified，下次刷新 catalog 不会被覆盖。")
                .font(.caption)
                .foregroundStyle(.secondary)

            HStack {
                Spacer()
                Button("取消") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("保存") {
                    onSave(parseDouble(inputText), parseDouble(outputText))
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 320)
    }

    private func parseDouble(_ s: String) -> Double? {
        let trimmed = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return nil }
        return Double(trimmed)
    }
}
