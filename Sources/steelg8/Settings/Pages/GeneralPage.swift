import SwiftUI

/// 「基础」页：默认模型 + 上下文压缩 + 日志级别。Phase 12.3 引入。
///
/// 默认模型来源：从 ProviderConfigStore 现存 entries 中收集 model id；保存时只覆盖
/// providers.json 的 default_model 字段（preserve providers），避免与"供应商与模型"
/// 页冲突。
struct GeneralPage: View {

    @StateObject private var viewModel = GeneralPageViewModel()
    @AppStorage("experimental.nativeChat") private var nativeChat = true

    var body: some View {
        Form {
            Section {
                Picker("默认模型", selection: $viewModel.defaultModel) {
                    if viewModel.allModels.isEmpty {
                        Text("— 尚无可选模型 —").tag("")
                    } else {
                        ForEach(viewModel.allModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                }
                .pickerStyle(.menu)
            } footer: {
                Text("路由不命中显式 model 时使用。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section {
                HStack {
                    Slider(value: $viewModel.compressionTriggerRatio, in: 0.50...0.90, step: 0.05) {
                        Text("上下文压缩触发比例")
                    }
                    Text("\(viewModel.compressionTriggerPercent)%")
                        .font(.body.monospacedDigit())
                        .frame(width: 48, alignment: .trailing)
                        .foregroundStyle(.secondary)
                }
            } footer: {
                Text("达到历史预算这个比例后，自动把早期对话压成纪要。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section {
                Picker("日志级别", selection: $viewModel.logLevel) {
                    ForEach(AppLogLevel.allCases) { level in
                        Text(level.displayName).tag(level)
                    }
                }
            } footer: {
                Text("决定 system 面板「日志」标签里看得到多详细的事件。改成 debug 后会话日志体积会涨。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section {
                Toggle("原生 SwiftUI 聊天界面", isOn: $nativeChat)
            } header: {
                Text("实验性功能")
            } footer: {
                Text("替换 WebView 聊天窗口为原生 SwiftUI 实现（Phase 12 Track B）。重启后生效。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding()
        .safeAreaInset(edge: .bottom) {
            footerBar
        }
        .task {
            viewModel.loadIfNeeded()
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
            .keyboardShortcut("r", modifiers: [.command])
            Button("保存并热加载") {
                viewModel.save()
            }
            .buttonStyle(.borderedProminent)
            .keyboardShortcut("s", modifiers: [.command])
            .disabled(viewModel.isSaving)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.bar)
    }
}

@MainActor
final class GeneralPageViewModel: ObservableObject {

    @Published var defaultModel: String = ""
    @Published var allModels: [String] = []
    @Published var compressionTriggerRatio: Double = AppPreferencesStore.defaultCompressionTriggerRatio
    @Published var logLevel: AppLogLevel = AppPreferencesStore.defaultLogLevel
    @Published var statusMessage: String?
    @Published var statusIsError: Bool = false
    @Published var isSaving: Bool = false

    private var hasLoaded = false

    var compressionTriggerPercent: Int {
        Int((compressionTriggerRatio * 100).rounded())
    }

    func loadIfNeeded() {
        guard !hasLoaded else { return }
        hasLoaded = true
        reload()
    }

    func reload() {
        do {
            let loaded = try ProviderConfigStore.shared.load()
            allModels = loaded.entries.flatMap(\.models).filter { !$0.isEmpty }
            defaultModel = loaded.defaultModel
            let prefs = AppPreferencesStore.shared.loadOrDefaults()
            compressionTriggerRatio = prefs.compressionTriggerRatio
            logLevel = AppLogLevel(rawValue: prefs.logLevel) ?? AppPreferencesStore.defaultLogLevel
            statusMessage = nil
            statusIsError = false
        } catch {
            statusMessage = "读取失败：\(error.localizedDescription)"
            statusIsError = true
        }
    }

    func save() {
        isSaving = true
        let trimmed = defaultModel.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty && !allModels.contains(trimmed) {
            statusMessage = "默认模型不在已配置的 provider 模型列表里：\(trimmed)"
            statusIsError = true
            isSaving = false
            return
        }

        do {
            try ProviderConfigStore.shared.saveDefaultModelPreservingEntries(trimmed)
            try AppPreferencesStore.shared.save(
                compressionTriggerRatio: compressionTriggerRatio,
                logLevel: logLevel.rawValue
            )
        } catch {
            statusMessage = "保存失败：\(error.localizedDescription)"
            statusIsError = true
            isSaving = false
            return
        }

        statusMessage = "已保存，正在通知内核热加载…"
        statusIsError = false

        Task { @MainActor in
            let ok = await SettingsKernelReload.providers()
            if ok {
                self.statusMessage = "已保存并热加载完成。"
                self.statusIsError = false
            } else {
                self.statusMessage = "已保存，但内核未响应热加载（下次启动时会生效）。"
                self.statusIsError = false
            }
            self.isSaving = false
        }
    }
}

/// 共享的最小热加载触发器；不解析详细 readyProviders / 校验 issues，
/// 只关心成功/失败。"供应商与模型"页有自己的更详细解析路径。
enum SettingsKernelReload {
    @MainActor
    static func providers() async -> Bool {
        var req = URLRequest(url: KernelConfig.url(path: "providers/reload"))
        req.httpMethod = "POST"
        req.timeoutInterval = 3
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        KernelConfig.authorize(&req)
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
