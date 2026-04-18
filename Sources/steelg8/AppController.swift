import AppKit
import Carbon
import SwiftUI

@MainActor
final class AppController: ObservableObject {
    static let shared = AppController()

    @Published private(set) var runtimeStatus = "未启动"
    @Published private(set) var activeModel = "mock-local"
    @Published private(set) var lastAgentResponse = ""
    @Published private(set) var lastErrorMessage: String?
    @Published private(set) var isAgentBusy = false

    var statusItem: NSStatusItem?

    var soulFilePath: String {
        pythonRuntime.soulFileURL.path
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

    func setup() {
        guard !isSetup else { return }
        isSetup = true

        setupStatusBar()
        setupHotkey()
        setupCaptureFlow()
        setupTerminationObserver()

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

    @objc func startCapture() {
        captureOverlay.show()
    }

    @objc func testAgentHello() {
        Task {
            await sendHelloToAgent()
        }
    }

    @objc func showMainWindow() {
        NSApp.activate(ignoringOtherApps: true)
        for window in NSApp.windows where !(window is NSPanel) && window.canBecomeMain {
            window.makeKeyAndOrderFront(nil)
            return
        }
    }

    @objc func openSettingsWindow() {
        NSApp.activate(ignoringOtherApps: true)
        // macOS 14+ 的标准选择器；SwiftUI Settings scene 监听此 action
        NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
    }

    @objc func quitApp() {
        pythonRuntime.stop()
        NSApplication.shared.terminate(nil)
    }

    deinit {
        if let terminationObserver {
            NotificationCenter.default.removeObserver(terminationObserver)
        }
    }

    private func setupStatusBar() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        if let button = item.button {
            if let image = NSImage(systemSymbolName: "hammer.circle", accessibilityDescription: "steelg8") {
                button.image = image
            } else {
                button.title = "steelg8"
            }
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

        let openSoulItem = NSMenuItem(title: "打开 soul.md", action: #selector(openSoulFileMenuItem), keyEquivalent: "")
        openSoulItem.target = self
        menu.addItem(openSoulItem)

        menu.addItem(.separator())

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
