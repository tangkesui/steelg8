import SwiftUI

/// 「基础」页：上下文压缩 + 外观 + 日志级别 + 实验性功能。
/// 2026-05-08 起：默认模型已移到「模型管理」页。
struct GeneralPage: View {

    @StateObject private var viewModel = GeneralPageViewModel()
    @AppStorage("experimental.nativeChat") private var nativeChat = true

    var body: some View {
        Form {
            Section {
                HStack {
                    Slider(value: $viewModel.compressionTriggerRatio, in: 0.50...0.90, step: 0.05) {
                        labelWithInfo("上下文压缩触发比例", info: "达到历史预算这个比例后，自动把早期对话压成纪要。")
                    }
                    Text("\(viewModel.compressionTriggerPercent)%")
                        .font(.body.monospacedDigit())
                        .frame(width: 48, alignment: .trailing)
                        .foregroundStyle(.secondary)
                }
            }

            Section {
                Picker(selection: $viewModel.appearance) {
                    ForEach(AppAppearance.allCases) { mode in
                        Text(mode.displayName).tag(mode)
                    }
                } label: {
                    labelWithInfo("外观", info: "跟随系统会随 macOS 浅色/深色自动切换。手动选项即时预览，按「保存」才持久化。")
                }
                .onChange(of: viewModel.appearance) { _, newValue in
                    newValue.apply()  // live preview, save() 才落盘
                }
            }

            Section {
                Picker(selection: $viewModel.logLevel) {
                    ForEach(AppLogLevel.allCases) { level in
                        Text(level.displayName).tag(level)
                    }
                } label: {
                    labelWithInfo("日志级别", info: "决定 system 面板「日志」标签里看得到多详细的事件。改成 debug 后会话日志体积会涨。")
                }
            }

            Section {
                Toggle(isOn: $nativeChat) {
                    labelWithInfo("原生 SwiftUI 聊天界面", info: "替换 WebView 聊天窗口为原生 SwiftUI 实现。Phase 12.19 起 Web 聊天已删，此开关保留向后兼容，重启生效。")
                }
            } header: {
                Text("实验性功能")
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

    private func labelWithInfo(_ title: String, info: String) -> some View {
        HStack(spacing: 4) {
            Text(title)
            InfoBadge(text: info)
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
            Button("默认") {
                viewModel.reload()
            }
            .keyboardShortcut("r", modifiers: [.command])
            Button("保存") {
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

    @Published var compressionTriggerRatio: Double = AppPreferencesStore.defaultCompressionTriggerRatio
    @Published var logLevel: AppLogLevel = AppPreferencesStore.defaultLogLevel
    @Published var appearance: AppAppearance = AppPreferencesStore.defaultAppearance
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
        let prefs = AppPreferencesStore.shared.loadOrDefaults()
        compressionTriggerRatio = prefs.compressionTriggerRatio
        logLevel = AppLogLevel(rawValue: prefs.logLevel) ?? AppPreferencesStore.defaultLogLevel
        appearance = AppAppearance(rawValue: prefs.appearance) ?? AppPreferencesStore.defaultAppearance
        appearance.apply()
        statusMessage = nil
        statusIsError = false
    }

    func save() {
        isSaving = true
        do {
            try AppPreferencesStore.shared.save(
                compressionTriggerRatio: compressionTriggerRatio,
                logLevel: logLevel.rawValue,
                appearance: appearance.rawValue
            )
        } catch {
            statusMessage = "保存失败：\(error.localizedDescription)"
            statusIsError = true
            isSaving = false
            return
        }
        statusMessage = "已保存。"
        statusIsError = false
        isSaving = false
    }
}

/// 共享的最小热加载触发器；模型管理 / 路由设置 / 供应商管理 都用它。
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
