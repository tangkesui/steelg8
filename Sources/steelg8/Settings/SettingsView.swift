import SwiftUI

/// Settings 主视图：Provider 管理 + 默认模型选择。
///
/// 设计原则：
/// - 所有写操作都落到 ~/.steelg8/providers.json（0600 权限），Python kernel 通过 /providers/reload 热加载
/// - 不依赖 Keychain（Phase 1 MVP），后续再迁
/// - 不覆盖用户在 shell 里设的环境变量：JSON 里 api_key 为空时，Python 端自动回退去读 api_key_env
struct SettingsView: View {

    @StateObject private var viewModel = SettingsViewModel()

    var body: some View {
        HStack(spacing: 0) {
            sidebar
                .frame(width: 220)
                .background(Color(nsColor: .windowBackgroundColor))

            Divider()

            detail
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: 720, minHeight: 480)
        .task {
            viewModel.loadIfNeeded()
        }
    }

    // MARK: - Sidebar

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Providers")
                    .font(.headline)
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.top, 16)
            .padding(.bottom, 8)

            List(selection: $viewModel.selectedProviderName) {
                ForEach(viewModel.entries) { entry in
                    HStack {
                        Circle()
                            .fill(entry.isConfigured ? Color.green : Color.gray.opacity(0.35))
                            .frame(width: 8, height: 8)
                        Text(entry.name.capitalized)
                        Spacer()
                    }
                    .tag(entry.id)
                }
            }
            .listStyle(.sidebar)

            Divider()

            VStack(alignment: .leading, spacing: 8) {
                Text("默认模型")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Picker("", selection: $viewModel.defaultModel) {
                    if viewModel.allModels.isEmpty {
                        Text("— 尚无可选模型 —").tag("")
                    } else {
                        ForEach(viewModel.allModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
    }

    // MARK: - Detail

    @ViewBuilder
    private var detail: some View {
        if let idx = viewModel.selectedIndex {
            providerDetail(for: idx)
        } else {
            emptyDetail
        }
    }

    private var emptyDetail: some View {
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

    @ViewBuilder
    private func providerDetail(for index: Int) -> some View {
        let entry = viewModel.entries[index]
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header(for: entry)

                GroupBox(label: Text("接入信息").font(.subheadline.bold())) {
                    VStack(alignment: .leading, spacing: 12) {
                        labeledRow("Base URL") {
                            TextField("https://api.example.com", text: Binding(
                                get: { viewModel.entries[index].baseURL },
                                set: { viewModel.entries[index].baseURL = $0 }
                            ))
                            .textFieldStyle(.roundedBorder)
                        }

                        labeledRow("API Key") {
                            SecureField("sk-...", text: Binding(
                                get: { viewModel.entries[index].apiKey },
                                set: { viewModel.entries[index].apiKey = $0 }
                            ))
                            .textFieldStyle(.roundedBorder)
                        }

                        labeledRow("Env 变量名") {
                            TextField("KIMI_API_KEY", text: Binding(
                                get: { viewModel.entries[index].apiKeyEnv },
                                set: { viewModel.entries[index].apiKeyEnv = $0 }
                            ))
                            .textFieldStyle(.roundedBorder)
                        }

                        Text("说明：上面填了 API Key 就直接用；留空则回退去读同名环境变量。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 8)
                }

                GroupBox(label: Text("支持的模型").font(.subheadline.bold())) {
                    modelsSection(providerIndex: index)
                        .padding(.vertical, 8)
                }

                Spacer(minLength: 0)
            }
            .padding(20)
        }
        .safeAreaInset(edge: .bottom) {
            footerBar
        }
    }

    private func header(for entry: ProviderEntry) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(entry.name.capitalized)
                    .font(.title3.bold())
                Text(entry.isConfigured ? "配置完整" : "缺少 API Key 或 base URL")
                    .font(.caption)
                    .foregroundStyle(entry.isConfigured ? .green : .orange)
            }
            Spacer()
        }
    }

    /// 模型列表，用 row.id（稳定 UUID）做 ForEach 的 identity，避免
    /// 删除时按 index 重入 Binding 触发越界崩溃。
    @ViewBuilder
    private func modelsSection(providerIndex: Int) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(viewModel.entries[providerIndex].modelRows) { row in
                HStack {
                    TextField(
                        "",
                        text: Binding(
                            get: {
                                viewModel.modelText(providerIndex: providerIndex, rowID: row.id)
                            },
                            set: { newValue in
                                viewModel.updateModelText(
                                    providerIndex: providerIndex,
                                    rowID: row.id,
                                    newValue: newValue
                                )
                            }
                        )
                    )
                    .textFieldStyle(.roundedBorder)

                    Button {
                        viewModel.removeModelRow(
                            providerIndex: providerIndex,
                            rowID: row.id
                        )
                    } label: {
                        Image(systemName: "minus.circle")
                    }
                    .buttonStyle(.borderless)
                    .help("移除这个模型")
                }
            }

            Button {
                viewModel.appendModelRow(providerIndex: providerIndex)
            } label: {
                Label("添加模型", systemImage: "plus.circle")
            }
            .buttonStyle(.borderless)
        }
    }

    private func labeledRow<Content: View>(
        _ label: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 90, alignment: .trailing)
            content()
        }
    }

    private var footerBar: some View {
        HStack(spacing: 12) {
            if let status = viewModel.statusMessage {
                Text(status)
                    .font(.caption)
                    .foregroundStyle(viewModel.statusIsError ? .red : .secondary)
            }

            Spacer()

            Button("还原") {
                viewModel.reload()
            }
            .keyboardShortcut(.init("r"), modifiers: [.command])

            Button("保存并热加载") {
                viewModel.save()
            }
            .buttonStyle(.borderedProminent)
            .keyboardShortcut(.init("s"), modifiers: [.command])
            .disabled(viewModel.isSaving)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.bar)
    }
}

// MARK: - ViewModel

@MainActor
final class SettingsViewModel: ObservableObject {

    @Published var entries: [ProviderEntry] = []
    @Published var defaultModel: String = ""
    @Published var selectedProviderName: String?
    @Published var statusMessage: String?
    @Published var statusIsError: Bool = false
    @Published var isSaving: Bool = false

    private var hasLoaded = false

    var selectedIndex: Int? {
        guard let name = selectedProviderName else { return nil }
        return entries.firstIndex { $0.id == name }
    }

    var allModels: [String] {
        entries.flatMap(\.models).filter { !$0.isEmpty }
    }

    // MARK: - 模型列表按 UUID 操作（避免 index 失效崩溃）

    func modelText(providerIndex: Int, rowID: UUID) -> String {
        guard entries.indices.contains(providerIndex),
              let r = entries[providerIndex].modelRows.first(where: { $0.id == rowID })
        else { return "" }
        return r.value
    }

    func updateModelText(providerIndex: Int, rowID: UUID, newValue: String) {
        guard entries.indices.contains(providerIndex) else { return }
        if let rowIdx = entries[providerIndex].modelRows.firstIndex(where: { $0.id == rowID }) {
            entries[providerIndex].modelRows[rowIdx].value = newValue
        }
    }

    func removeModelRow(providerIndex: Int, rowID: UUID) {
        guard entries.indices.contains(providerIndex) else { return }
        entries[providerIndex].modelRows.removeAll { $0.id == rowID }
    }

    func appendModelRow(providerIndex: Int) {
        guard entries.indices.contains(providerIndex) else { return }
        entries[providerIndex].modelRows.append(ModelRow(""))
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
            defaultModel = loaded.defaultModel
            if selectedProviderName == nil {
                selectedProviderName = entries.first?.id
            }
            statusMessage = nil
            statusIsError = false
        } catch {
            statusMessage = "读取失败：\(error.localizedDescription)"
            statusIsError = true
        }
    }

    func save() {
        isSaving = true
        defer { isSaving = false }

        do {
            try ProviderConfigStore.shared.save(entries: entries, defaultModel: defaultModel)
        } catch {
            statusMessage = "保存失败：\(error.localizedDescription)"
            statusIsError = true
            return
        }

        statusMessage = "已保存，正在通知内核热加载…"
        statusIsError = false

        Task {
            let result = await hotReloadKernel()
            await MainActor.run {
                if result {
                    self.statusMessage = "已保存并热加载完成。"
                } else {
                    self.statusMessage = "已保存，但内核未响应热加载（下次启动时会生效）。"
                }
            }
        }
    }

    /// POST http://127.0.0.1:8765/providers/reload
    private func hotReloadKernel() async -> Bool {
        guard let url = URL(string: "http://127.0.0.1:8765/providers/reload") else {
            return false
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 3
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("{}".utf8)

        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                return false
            }
            return true
        } catch {
            return false
        }
    }
}

#Preview {
    SettingsView()
}
