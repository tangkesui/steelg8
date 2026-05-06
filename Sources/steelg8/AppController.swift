import AppKit
import Carbon
import SwiftUI

@MainActor
final class AppController: ObservableObject {
    static let shared = AppController()
    nonisolated static let mainWindowIdentifier = NSUserInterfaceItemIdentifier("com.local.steelg8.main-window")

    @Published private(set) var runtimeStatus = "未启动"
    @Published private(set) var activeModel = "mock-local"
    @Published private(set) var lastAgentResponse = ""
    @Published private(set) var lastErrorMessage: String?
    @Published private(set) var isAgentBusy = false

    var statusItem: NSStatusItem?

    var soulFilePath: String {
        pythonRuntime.soulFileURL.path
    }

    var userMdURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appending(path: ".steelg8")
            .appending(path: "user.md")
    }

    private let captureOverlay = ScreenCaptureOverlay()
    private let silentOCR = SilentOCREngine()
    private let pythonRuntime = PythonRuntime()
    private lazy var agentBridge = AgentBridge(runtime: pythonRuntime)
    private let statusPresenter = StatusItemMessagePresenter()

    private var isSetup = false
    private var runtimeStatusItem: NSMenuItem?
    private var lastReplyItem: NSMenuItem?
    private var terminationObserver: NSObjectProtocol?
    private var windowCloseObserver: NSObjectProtocol?
    private var windowKeyObserver: NSObjectProtocol?
    private var fallbackMainWindowController: NSWindowController?
    private var initializedMainWindows = Set<ObjectIdentifier>()

    func setup() {
        guard !isSetup else { return }
        isSetup = true

        setupStatusBar()
        setupHotkey()
        setupCaptureFlow()
        setupTerminationObserver()
        setupWindowActivationObservers()

        Task {
            await bootPythonRuntime()
        }

        NSLog("steelg8: setup complete")
    }

    func sendHelloToAgent() async {
        isAgentBusy = true
        runtimeStatus = "请求中"
        refreshMenuState()

        do {
            let response = try await agentBridge.chat(
                message: "给我一句 steelg8 的启动问候，并提醒我这条链路已经打通。",
                model: activeModel == "mock-local" ? nil : activeModel
            )

            lastAgentResponse = response.content
            activeModel = response.model
            runtimeStatus = "在线（\(response.source)）"
            lastErrorMessage = nil
            refreshMenuState()
            statusPresenter.present(shorten(response.content), on: statusItem)
        } catch {
            runtimeStatus = "调用失败"
            lastErrorMessage = error.localizedDescription
            refreshMenuState()
            statusPresenter.present("Agent 调用失败", on: statusItem)
        }

        isAgentBusy = false
    }

    func openSoulFile() {
        ensureSoulFileExists()
        NSWorkspace.shared.open(pythonRuntime.soulFileURL)
    }

    @objc func openTemplatesFolder() {
        // 读当前 preferences 里的 templates_dir 而不是写死路径
        Task { @MainActor in
            let path = await currentTemplatesDir()
            let url = URL(fileURLWithPath: path)
            try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
            NSWorkspace.shared.open(url)
        }
    }

    @objc func changeTemplatesFolder() {
        Task { @MainActor in
            let panel = NSOpenPanel()
            panel.title = "选一个新的模板库目录"
            panel.message = "把 Word / Excel / PPT 模板放进去，steelg8 会在对话里自动找到。"
            panel.prompt = "设为模板库"
            panel.canChooseDirectories = true
            panel.canChooseFiles = false
            panel.allowsMultipleSelection = false
            panel.canCreateDirectories = true
            NSApp.activate(ignoringOtherApps: true)

            let resp = await panel.beginSheetSafe()
            guard resp == .OK, let url = panel.url else { return }

            // 写 preferences
            var req = URLRequest(url: KernelConfig.url(path: "preferences"))
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            KernelConfig.authorize(&req)
            req.httpBody = try? JSONSerialization.data(
                withJSONObject: ["templates_dir": url.path]
            )
            req.timeoutInterval = 5
            _ = try? await URLSession.shared.data(for: req)

            statusPresenter.present("模板库已切到 \(url.lastPathComponent)", on: statusItem)
        }
    }

    /// 从 kernel 查当前 templates_dir；失败回退默认
    @MainActor
    private func currentTemplatesDir() async -> String {
        let fallback = FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Documents")
            .appending(path: "steelg8")
            .appending(path: "templates").path
        var req = URLRequest(url: KernelConfig.url(path: "preferences"))
        req.timeoutInterval = 3
        KernelConfig.authorize(&req)
        guard
            let (data, _) = try? await URLSession.shared.data(for: req),
            let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let dir = dict["templates_dir"] as? String
        else { return fallback }
        return dir
    }

    @objc func openKnowledgeFolder() {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appending(path: ".steelg8")
            .appending(path: "knowledge")
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }

    @objc func openUserMdFile() {
        // Python 端首次 GET /chat 时会创建它；这里兜底：文件不存在则建空骨架
        let url = userMdURL
        if !FileManager.default.fileExists(atPath: url.path) {
            try? FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            let stub = """
            # steelg8 用户画像（L2）

            > 这个文件会在每次对话拼进 system prompt。随时手动编辑。

            ## 基本

            （空）

            ## 写作口吻与偏好

            （空）
            """
            try? stub.write(to: url, atomically: true, encoding: .utf8)
        }
        NSWorkspace.shared.open(url)
    }

    @objc func startCapture() {
        captureOverlay.show()
    }

    @objc func testAgentHello() {
        Task {
            await sendHelloToAgent()
        }
    }

    @objc func restartPythonKernel() {
        Task { [weak self] in
            guard let self else { return }
            pythonRuntime.stop()
            do {
                try await pythonRuntime.startIfNeeded()
                await MainActor.run { [weak self] in
                    self?.runtimeStatusItem?.title = "内核状态：已重启"
                }
            } catch {
                await MainActor.run { [weak self] in
                    self?.runtimeStatusItem?.title = "内核重启失败：\(error.localizedDescription)"
                }
            }
        }
    }

    @objc func showMainWindow() {
        enterForegroundWindowMode()
        ensureStatusBarVisible()

        if let window = existingMainWindow() {
            present(window)
            return
        }

        if let fallbackWindow = fallbackMainWindowController?.window {
            configureMainWindow(fallbackWindow)
            present(fallbackWindow)
            return
        }

        let root = ContentView()
            .environmentObject(self)
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1080, height: 700),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "steelg8"
        window.center()
        window.isReleasedWhenClosed = false
        configureMainWindow(window)
        window.contentView = NSHostingView(rootView: root)

        let controller = NSWindowController(window: window)
        fallbackMainWindowController = controller
        controller.showWindow(nil)
        present(window)
    }

    func configureMainWindow(_ window: NSWindow) {
        guard !(window is NSPanel) else { return }
        window.identifier = Self.mainWindowIdentifier
        window.title = "steelg8"
        window.minSize = NSSize(width: 960, height: 640)
        window.isMovable = true
        window.isMovableByWindowBackground = true

        let windowID = ObjectIdentifier(window)
        if !initializedMainWindows.contains(windowID) {
            initializedMainWindows.insert(windowID)

            // 一次性迁移：首次进入原生 Chat 时，清除旧 WebView 最大化帧，重新居中
            let migKey = "steelg8.nativeChatWindowMigrated"
            if !UserDefaults.standard.bool(forKey: migKey) {
                UserDefaults.standard.set(true, forKey: migKey)
                // 清除两个可能存在的旧 autosave key
                UserDefaults.standard.removeObject(forKey: "NSWindow Frame steelg8-main-window")
                UserDefaults.standard.removeObject(forKey: "NSWindow Frame steelg8-native-window")
            }

            // setFrameAutosaveName 必须在清除旧 key 之后调用，否则会立即恢复最大化状态。
            // 只在窗口实例初始化时设置；SwiftUI 更新期间反复 setFrame/center 会把用户拖动打回原位。
            window.setFrameAutosaveName("steelg8-native-window")

            // 无已保存帧（含刚清除的情况）→ 仅首次设置合理尺寸再居中。
            if UserDefaults.standard.object(forKey: "NSWindow Frame steelg8-native-window") == nil {
                window.setFrame(NSRect(x: 0, y: 0, width: 1080, height: 700), display: false)
                window.center()
            }
        }

        if window.isVisible || window.isKeyWindow {
            enterForegroundWindowMode()
        }
    }

    @objc func openSettingsWindow() {
        enterForegroundWindowMode()
        ensureStatusBarVisible()
        NSApp.activate(ignoringOtherApps: true)
        // macOS 14+ 的标准选择器；SwiftUI Settings scene 监听此 action
        NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
    }

    @objc func openProjectPicker() {
        Task { [weak self] in
            let result = await ProjectPicker.pickAndOpen()
            switch result {
            case .success(let proj):
                await MainActor.run {
                    self?.statusPresenter.present("项目已加载：\(proj.name)", on: self?.statusItem)
                }
            case .failure(let err):
                await MainActor.run {
                    if err.message != "已取消" {
                        self?.statusPresenter.present("打开项目失败：\(err.message)", on: self?.statusItem)
                    }
                }
            }
        }
    }

    @objc func quitApp() {
        pythonRuntime.stop()
        NSApplication.shared.terminate(nil)
    }

    deinit {
        if let terminationObserver {
            NotificationCenter.default.removeObserver(terminationObserver)
        }
        if let windowCloseObserver {
            NotificationCenter.default.removeObserver(windowCloseObserver)
        }
        if let windowKeyObserver {
            NotificationCenter.default.removeObserver(windowKeyObserver)
        }
    }

    private func setupStatusBar() {
        if statusItem != nil {
            return
        }

        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        if let button = item.button {
            // 和 app icon 呼应：金库的四辐转盘。
            // SF Symbols 里 "circle.grid.cross" 是十字握柄的圆框，最接近那个保险柜形象
            if let image = NSImage(systemSymbolName: "circle.grid.cross", accessibilityDescription: "steelg8") {
                button.image = image
                button.imagePosition = .imageLeading
            }
            button.title = " steelg8"
            button.toolTip = "steelg8"
        }

        let menu = NSMenu()

        let runtimeStatusItem = NSMenuItem(title: "内核状态：\(runtimeStatus)", action: nil, keyEquivalent: "")
        runtimeStatusItem.isEnabled = false
        menu.addItem(runtimeStatusItem)
        self.runtimeStatusItem = runtimeStatusItem

        let lastReplyItem = NSMenuItem(title: "最近回复：暂无", action: nil, keyEquivalent: "")
        lastReplyItem.isEnabled = false
        menu.addItem(lastReplyItem)
        self.lastReplyItem = lastReplyItem

        menu.addItem(.separator())

        let helloItem = NSMenuItem(title: "测试 Agent 链路", action: #selector(testAgentHello), keyEquivalent: "")
        helloItem.target = self
        menu.addItem(helloItem)

        let captureItem = NSMenuItem(title: "截图识别 (⌘⇧D)", action: #selector(startCapture), keyEquivalent: "")
        captureItem.target = self
        menu.addItem(captureItem)

        let openSoulItem = NSMenuItem(title: "打开 soul.md（L1 人格）", action: #selector(openSoulFileMenuItem), keyEquivalent: "")
        openSoulItem.target = self
        menu.addItem(openSoulItem)

        let openUserItem = NSMenuItem(title: "打开 user.md（L2 画像）", action: #selector(openUserMdFile), keyEquivalent: "")
        openUserItem.target = self
        menu.addItem(openUserItem)

        let openTemplatesItem = NSMenuItem(
            title: "打开模板库文件夹",
            action: #selector(openTemplatesFolder),
            keyEquivalent: ""
        )
        openTemplatesItem.target = self
        menu.addItem(openTemplatesItem)

        let changeTemplatesItem = NSMenuItem(
            title: "更换模板库目录…",
            action: #selector(changeTemplatesFolder),
            keyEquivalent: ""
        )
        changeTemplatesItem.target = self
        menu.addItem(changeTemplatesItem)

        let openKnowledgeItem = NSMenuItem(
            title: "打开知识库文件夹",
            action: #selector(openKnowledgeFolder),
            keyEquivalent: ""
        )
        openKnowledgeItem.target = self
        menu.addItem(openKnowledgeItem)

        let openProjectItem = NSMenuItem(
            title: "打开项目文件夹…",
            action: #selector(openProjectPicker),
            keyEquivalent: "o"
        )
        openProjectItem.target = self
        openProjectItem.keyEquivalentModifierMask = [.command, .shift]
        menu.addItem(openProjectItem)

        menu.addItem(.separator())

        let restartKernelItem = NSMenuItem(
            title: "重启 Python 内核",
            action: #selector(restartPythonKernel),
            keyEquivalent: "r"
        )
        restartKernelItem.target = self
        restartKernelItem.keyEquivalentModifierMask = [.command, .shift]
        menu.addItem(restartKernelItem)

        let settingsItem = NSMenuItem(
            title: "设置…",
            action: #selector(openSettingsWindow),
            keyEquivalent: ","
        )
        settingsItem.target = self
        menu.addItem(settingsItem)

        let showWindowItem = NSMenuItem(title: "打开主窗口", action: #selector(showMainWindow), keyEquivalent: "")
        showWindowItem.target = self
        menu.addItem(showWindowItem)

        menu.addItem(.separator())

        let quitItem = NSMenuItem(title: "退出", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        item.menu = menu
        statusItem = item
        refreshMenuState()
    }

    func ensureStatusBarVisible() {
        setupStatusBar()
    }

    private func setupHotkey() {
        // ⌘⇧D 截图 OCR（旧）
        HotkeyManager.shared.register(
            id: "capture-ocr",
            binding: .init(keyCode: UInt32(kVK_ANSI_D), modifiers: UInt32(cmdKey | shiftKey)),
            handler: { [weak self] in self?.startCapture() }
        )
        // ⌘⇧N 召唤 Scratch 捕获台独立窗
        HotkeyManager.shared.register(
            id: "scratch",
            binding: .init(keyCode: UInt32(kVK_ANSI_N), modifiers: UInt32(cmdKey | shiftKey)),
            handler: { ScratchSummonWindow.shared.toggle() }
        )
    }

    private func setupCaptureFlow() {
        captureOverlay.onCapture = { [weak self] image in
            DispatchQueue.main.async {
                self?.silentOCR.recognizeAndCopy(from: image)
            }
        }
    }

    private func setupTerminationObserver() {
        terminationObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.pythonRuntime.stop()
            }
        }
    }

    private func setupWindowActivationObservers() {
        windowCloseObserver = NotificationCenter.default.addObserver(
            forName: NSWindow.willCloseNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let self,
                  let window = notification.object as? NSWindow,
                  window.identifier == Self.mainWindowIdentifier
            else {
                return
            }

            Task { @MainActor in
                self.enterMenuBarOnlyModeIfNeeded()
            }
        }

        windowKeyObserver = NotificationCenter.default.addObserver(
            forName: NSWindow.didBecomeKeyNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let self,
                  let window = notification.object as? NSWindow,
                  window.identifier == Self.mainWindowIdentifier
            else {
                return
            }

            Task { @MainActor in
                self.enterForegroundWindowMode()
                self.ensureStatusBarVisible()
            }
        }
    }

    private func bootPythonRuntime() async {
        do {
            ensureSoulFileExists()
            try await pythonRuntime.startIfNeeded()
            runtimeStatus = "已启动"
            lastErrorMessage = nil
            refreshMenuState()
            statusPresenter.present("本地内核已启动", on: statusItem)
        } catch {
            runtimeStatus = "启动失败"
            lastErrorMessage = error.localizedDescription
            refreshMenuState()
            statusPresenter.present("本地内核启动失败", on: statusItem)
        }
    }

    private func refreshMenuState() {
        runtimeStatusItem?.title = "内核状态：\(runtimeStatus)"
        lastReplyItem?.title = "最近回复：\(lastAgentResponse.isEmpty ? "暂无" : shorten(lastAgentResponse))"
    }

    private func existingMainWindow() -> NSWindow? {
        NSApp.windows.first { window in
            window.identifier == Self.mainWindowIdentifier
                && !(window is NSPanel)
                && window.canBecomeMain
        }
    }

    private func present(_ window: NSWindow) {
        enterForegroundWindowMode()
        ensureStatusBarVisible()
        window.deminiaturize(nil)
        window.orderFrontRegardless()
        window.makeKeyAndOrderFront(nil)
        activateApplicationFrontmost()

        // LSUIElement -> regular 的转换不是完全同步的；稍后再激活一次，
        // 避免窗口看似在前面，但菜单栏仍属于上一个 app。
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            self.enterForegroundWindowMode()
            window.orderFrontRegardless()
            window.makeKeyAndOrderFront(nil)
            self.activateApplicationFrontmost()
        }
    }

    private func enterForegroundWindowMode() {
        if NSApp.activationPolicy() != .regular {
            NSApp.setActivationPolicy(.regular)
        }
    }

    private func activateApplicationFrontmost() {
        NSApp.unhide(nil)
        NSApp.activate(ignoringOtherApps: true)
        NSRunningApplication.current.activate(options: [
            .activateAllWindows,
        ])
    }

    private func enterMenuBarOnlyModeIfNeeded() {
        ensureStatusBarVisible()
    }

    private func shorten(_ value: String, limit: Int = 42) -> String {
        if value.count <= limit {
            return value
        }

        let endIndex = value.index(value.startIndex, offsetBy: limit)
        return String(value[..<endIndex]) + "…"
    }

    private func ensureSoulFileExists() {
        do {
            try pythonRuntime.bootstrapSoulFileIfNeeded()
        } catch {
            lastErrorMessage = error.localizedDescription
        }
    }

    @objc private func openSoulFileMenuItem() {
        openSoulFile()
    }
}
