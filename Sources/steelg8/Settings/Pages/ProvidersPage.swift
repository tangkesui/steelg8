import SwiftUI

struct ProvidersPage: View {
    @StateObject private var viewModel = ProvidersViewModel()

    var body: some View {
        HStack(spacing: 0) {
            sidebar
                .frame(width: 240)

            Divider()

            detail
                .frame(minWidth: 0, maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: 680, minHeight: 540)
        .task {
            viewModel.loadIfNeeded()
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("供应商")
                    .font(.headline)
                Spacer()
                addMenu
            }
            .padding(.horizontal, 16)
            .padding(.top, 16)
            .padding(.bottom, 8)

            List(selection: $viewModel.selectedProviderName) {
                ForEach(viewModel.entries) { entry in
                    ProviderSidebarRow(entry: entry)
                        .tag(entry.id)
                        .contextMenu {
                            Button("删除") {
                                viewModel.removeProvider(name: entry.name)
                            }
                        }
                }
            }
            .listStyle(.sidebar)
        }
    }

    private var addMenu: some View {
        Menu {
            presetSection("云端模型 Provider", kind: .cloudModel)
            presetSection("本地 Runtime", kind: .localRuntime)
            presetSection("工具 Provider", kind: .tool)
            Divider()
            Button("自定义（空模板）…") {
                viewModel.addProviderFromPreset(nil)
            }
        } label: {
            Image(systemName: "plus.circle")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help("添加供应商")
    }

    @ViewBuilder
    private func presetSection(_ title: String, kind: ProviderCatalog.PresetKind) -> some View {
        Section(title) {
            ForEach(ProviderCatalog.all.filter { $0.kind == kind }, id: \.id) { preset in
                Button(preset.name) {
                    viewModel.addProviderFromPreset(preset)
                }
                .disabled(viewModel.hasProvider(id: preset.id))
            }
        }
    }

    @ViewBuilder
    private var detail: some View {
        if let entry = viewModel.selectedEntry {
            ProviderDetailView(
                entry: entry,
                catalogModels: viewModel.catalogModels(for: entry.name),
                selectedIDs: viewModel.selectedModelIDs(for: entry.name),
                newModelText: viewModel.newModelTextBinding(for: entry.name),
                isRefreshing: viewModel.refreshingProvider == entry.name,
                isSaving: viewModel.isSaving,
                statusMessage: viewModel.statusMessage,
                statusIsError: viewModel.statusIsError,
                onSetField: { field, value in
                    viewModel.setProviderField(name: entry.name, field: field, value: value)
                },
                onToggleModel: { modelID, enabled in
                    viewModel.setModelSelected(provider: entry.name, modelID: modelID, selected: enabled)
                },
                onAddManualModel: {
                    viewModel.addManualModel(provider: entry.name)
                },
                onRefreshCatalog: {
                    viewModel.refreshCatalog(provider: entry.name)
                },
                onApplyRecommended: {
                    viewModel.applyRecommended(provider: entry.name)
                },
                onUseEnv: {
                    viewModel.useExternalEnv(provider: entry.name)
                },
                onDelete: {
                    viewModel.removeProvider(name: entry.name)
                },
                onSave: {
                    viewModel.save()
                },
                onReload: {
                    viewModel.reload()
                }
            )
        } else {
            VStack(spacing: 8) {
                Spacer()
                Image(systemName: "rectangle.and.pencil.and.ellipsis")
                    .font(.system(size: 32))
                    .foregroundStyle(.secondary)
                Text("从左侧选一个 Provider 开始配置")
                    .foregroundStyle(.secondary)
                Spacer()
            }
            .frame(maxWidth: .infinity)
        }
    }
}

private struct ProviderSidebarRow: View {
    let entry: ProviderEntry

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: iconName)
                .foregroundStyle(.secondary)
                .frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.displayName.isEmpty ? entry.name : entry.displayName)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Text(entry.name)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .layoutPriority(1)
            Spacer()
            Circle()
                .fill(entry.isConfigured ? Color.green : Color.gray.opacity(0.35))
                .frame(width: 7, height: 7)
        }
    }

    private var iconName: String {
        switch entry.kind {
        case "local-runtime": return "desktopcomputer"
        case "tool": return "wrench.and.screwdriver"
        default: return "cloud"
        }
    }
}

@MainActor
final class ProvidersViewModel: ObservableObject {
    @Published var entries: [ProviderEntry] = []
    @Published var selectedProviderName: String?
    @Published var statusMessage: String?
    @Published var statusIsError = false
    @Published var isSaving = false
    @Published var refreshingProvider: String?
    @Published private var catalogByProvider: [String: [CatalogModel]] = [:]
    @Published private var pendingSelection: [String: Set<String>] = [:]
    @Published private var newModelText: [String: String] = [:]

    private let api = ProvidersAPI()
    private var hasLoaded = false

    var selectedEntry: ProviderEntry? {
        guard let selectedProviderName else { return nil }
        return entries.first { $0.name == selectedProviderName }
    }

    func loadIfNeeded() {
        guard !hasLoaded else { return }
        hasLoaded = true
        reload()
    }

    func reload() {
        do {
            let loaded = try ProviderConfigStore.shared.load()
            entries = loaded.entries
            if selectedProviderName == nil || !entries.contains(where: { $0.name == selectedProviderName }) {
                selectedProviderName = entries.first?.name
            }
            catalogByProvider = [:]
            pendingSelection = Dictionary(
                uniqueKeysWithValues: entries.map { ($0.name, Set($0.models)) }
            )
            statusMessage = nil
            statusIsError = false
            loadCatalogsForCurrentEntries()
        } catch {
            statusMessage = "读取失败：\(error.localizedDescription)"
            statusIsError = true
        }
    }

    func hasProvider(id: String) -> Bool {
        entries.contains { $0.name.lowercased() == id.lowercased() }
    }

    func addProviderFromPreset(_ preset: ProviderCatalog.Preset?) {
        if let preset {
            guard !hasProvider(id: preset.id) else { return }
            let entry = ProviderEntry(
                name: preset.id,
                displayName: preset.name,
                baseURL: preset.baseURL,
                apiKeyEnv: preset.apiKeyEnv,
                apiKey: "",
                kind: preset.kind.providerKind,
                models: preset.defaultModels
            )
            entries.append(entry)
            setPendingSelection(provider: entry.name, ids: Set(preset.defaultModels))
            selectedProviderName = entry.name
        } else {
            var i = 1
            while hasProvider(id: "custom-\(i)") { i += 1 }
            let entry = ProviderEntry(
                name: "custom-\(i)",
                displayName: "Custom \(i)",
                baseURL: "",
                apiKeyEnv: "",
                apiKey: "",
                kind: "openai-compatible",
                models: []
            )
            entries.append(entry)
            setPendingSelection(provider: entry.name, ids: [])
            selectedProviderName = entry.name
        }
    }

    func removeProvider(name: String) {
        entries.removeAll { $0.name == name }
        removeCatalog(provider: name)
        removePendingSelection(provider: name)
        setNewModelText(provider: name, value: nil)
        if selectedProviderName == name {
            selectedProviderName = entries.first?.name
        }
    }

    func catalogModels(for provider: String) -> [CatalogModel] {
        catalogByProvider[provider] ?? modelsFromPending(provider: provider)
    }

    func selectedModelIDs(for provider: String) -> Set<String> {
        pendingSelection[provider] ?? []
    }

    func setModelSelected(provider: String, modelID: String, selected: Bool) {
        var ids = pendingSelection[provider] ?? []
        if selected {
            ids.insert(modelID)
        } else {
            ids.remove(modelID)
        }
        setPendingSelection(provider: provider, ids: ids)
    }

    func newModelTextBinding(for provider: String) -> Binding<String> {
        Binding(
            get: { self.newModelText[provider] ?? "" },
            set: { self.setNewModelText(provider: provider, value: $0) }
        )
    }

    func addManualModel(provider: String) {
        let modelID = (newModelText[provider] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !modelID.isEmpty else { return }
        var models = catalogByProvider[provider] ?? modelsFromPending(provider: provider)
        if !models.contains(where: { $0.id == modelID }) {
            models.append(CatalogModel.manual(id: modelID, selected: true))
        }
        setCatalogModels(provider: provider, models: models)
        setModelSelected(provider: provider, modelID: modelID, selected: true)
        setNewModelText(provider: provider, value: "")
    }

    func setProviderField(name: String, field: ProviderDetailField, value: String) {
        guard let idx = entries.firstIndex(where: { $0.name == name }) else { return }
        switch field {
        case .displayName: entries[idx].displayName = value
        case .baseURL: entries[idx].baseURL = value
        case .apiKey: entries[idx].apiKey = value
        case .apiKeyEnv: entries[idx].apiKeyEnv = value
        }
    }

    func refreshCatalog(provider: String) {
        refreshingProvider = provider
        statusMessage = "正在刷新 catalog…"
        statusIsError = false
        Task {
            do {
                let response = try await api.refreshCatalog(provider: provider)
                await MainActor.run {
                    self.setCatalogModels(provider: provider, models: response.models)
                    self.setPendingSelection(provider: provider, ids: Set(response.models.filter(\.selected).map(\.id)))
                    self.refreshingProvider = nil
                    self.statusMessage = "已刷新 \(response.count) 个模型。"
                    self.statusIsError = false
                }
            } catch {
                await MainActor.run {
                    self.refreshingProvider = nil
                    self.statusMessage = "刷新失败：\(error.localizedDescription)"
                    self.statusIsError = true
                }
            }
        }
    }

    func applyRecommended(provider: String) {
        let recommended = RecommendedModelsClient.forProvider(provider)
        guard !recommended.isEmpty else {
            statusMessage = "这个 provider 暂无推荐清单。"
            statusIsError = false
            return
        }
        var models = catalogByProvider[provider] ?? []
        for modelID in recommended where !models.contains(where: { $0.id == modelID }) {
            models.append(CatalogModel.manual(id: modelID, selected: true))
        }
        setCatalogModels(provider: provider, models: models)
        setPendingSelection(provider: provider, ids: Set(recommended))
        saveSelection(provider: provider, message: "已应用推荐清单。")
    }

    func useExternalEnv(provider: String) {
        guard let preset = ProviderCatalog.preset(by: provider) else { return }
        setProviderField(name: provider, field: .apiKey, value: "")
        setProviderField(name: provider, field: .apiKeyEnv, value: preset.apiKeyEnv)
    }

    func save() {
        isSaving = true
        let entriesForValidation = entriesWithPendingModels()
        let validation = ProviderConfigStore.shared.validateEntries(entriesForValidation)
        guard validation.isValid else {
            statusMessage = "保存前检查失败：\(validation.shortSummary)"
            statusIsError = true
            isSaving = false
            return
        }

        do {
            try ProviderConfigStore.shared.saveEntriesPreservingDefault(entriesForValidation)
        } catch {
            statusMessage = "保存失败：\(error.localizedDescription)"
            statusIsError = true
            isSaving = false
            return
        }

        Task {
            do {
                for entry in entriesForValidation where entry.kind != "tool" {
                    let ids = Array(pendingSelection[entry.name] ?? [])
                        .sorted()
                    _ = try await api.updateCatalogSelection(provider: entry.name, modelIds: ids)
                }
                try await api.reloadProviders()
                await MainActor.run {
                    self.statusMessage = validation.warnings.isEmpty
                        ? "已保存并热加载。"
                        : "已保存并热加载。本地提示：\(validation.shortSummary)"
                    self.statusIsError = false
                    self.isSaving = false
                }
            } catch {
                await MainActor.run {
                    self.statusMessage = "已保存配置，但热加载失败：\(error.localizedDescription)"
                    self.statusIsError = true
                    self.isSaving = false
                }
            }
        }
    }

    private func saveSelection(provider: String, message: String) {
        Task {
            do {
                let ids = Array(pendingSelection[provider] ?? []).sorted()
                let response = try await api.updateCatalogSelection(provider: provider, modelIds: ids)
                await MainActor.run {
                    self.setCatalogModels(provider: provider, models: response.models)
                    self.setPendingSelection(provider: provider, ids: Set(response.models.filter(\.selected).map(\.id)))
                    self.statusMessage = message
                    self.statusIsError = false
                }
            } catch {
                await MainActor.run {
                    self.statusMessage = "写入 catalog 失败：\(error.localizedDescription)"
                    self.statusIsError = true
                }
            }
        }
    }

    private func loadCatalogsForCurrentEntries() {
        let providerIDs = entries.map(\.name)
        Task {
            for provider in providerIDs {
                do {
                    let response = try await api.readCatalog(provider: provider)
                    await MainActor.run {
                        self.setCatalogModels(provider: provider, models: response.models)
                        self.setPendingSelection(provider: provider, ids: Set(response.models.filter(\.selected).map(\.id)))
                    }
                } catch {
                    // 新 provider 或首次启动没有 catalog 是正常状态。
                }
            }
        }
    }

    private func entriesWithPendingModels() -> [ProviderEntry] {
        entries.map { entry in
            var copy = entry
            let ids = Array(pendingSelection[entry.name] ?? Set(entry.models)).sorted()
            copy.modelRows = ids.map { ModelRow($0) }
            return copy
        }
    }

    private func modelsFromPending(provider: String) -> [CatalogModel] {
        let ids = Array(pendingSelection[provider] ?? []).sorted()
        return ids.map { CatalogModel.manual(id: $0, selected: true) }
    }

    private func setCatalogModels(provider: String, models: [CatalogModel]) {
        var copy = catalogByProvider
        copy[provider] = models
        catalogByProvider = copy
    }

    private func removeCatalog(provider: String) {
        var copy = catalogByProvider
        copy.removeValue(forKey: provider)
        catalogByProvider = copy
    }

    private func setPendingSelection(provider: String, ids: Set<String>) {
        var copy = pendingSelection
        copy[provider] = ids
        pendingSelection = copy
    }

    private func removePendingSelection(provider: String) {
        var copy = pendingSelection
        copy.removeValue(forKey: provider)
        pendingSelection = copy
    }

    private func setNewModelText(provider: String, value: String?) {
        var copy = newModelText
        if let value {
            copy[provider] = value
        } else {
            copy.removeValue(forKey: provider)
        }
        newModelText = copy
    }
}

private extension CatalogModel {
    static func manual(id: String, selected: Bool) -> CatalogModel {
        CatalogModel(id: id, selected: selected, pricingPerMToken: nil)
    }
}
