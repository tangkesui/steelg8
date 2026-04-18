import AppKit
import WebKit

/// ⌘⇧N 召唤出来的"捕获台独立窗"。
///
/// 要求（产品设计方案 §7.3）：
/// - 非 always-on-top
/// - 召唤时出现；ESC 关闭
/// - 与主窗口共享同一份 scratch 数据（靠后端 HTTP 接口，不用直连）
///
/// 实现：单例持有一个 NSPanel + WKWebView，panel 加载 Web/chat/index.html#scratch。
/// 前端 chat.js 检测到 `location.hash === "#scratch"` 后会隐藏 chat 列、把侧栏撑满。
@MainActor
final class ScratchSummonWindow {
    static let shared = ScratchSummonWindow()

    private var panel: NSPanel?
    private var webView: WKWebView?
    private var escMonitor: Any?

    /// 召唤窗（没创建就创建，已存在就 toggle 可见性）。
    func toggle() {
        if panel?.isVisible == true {
            hide()
        } else {
            show()
        }
    }

    func show() {
        if panel == nil {
            build()
        }
        guard let panel = panel else { return }
        centerOnMouseScreen(panel)
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)

        // ESC 关闭：panel key 时监听 local keyDown
        if escMonitor == nil {
            escMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                if event.keyCode == 53 {  // ESC
                    self?.hide()
                    return nil
                }
                return event
            }
        }
    }

    func hide() {
        panel?.orderOut(nil)
        if let m = escMonitor {
            NSEvent.removeMonitor(m)
            escMonitor = nil
        }
    }

    // MARK: - 构建

    private func build() {
        let rect = NSRect(x: 0, y: 0, width: 420, height: 560)
        let style: NSWindow.StyleMask = [.titled, .closable, .resizable, .nonactivatingPanel, .fullSizeContentView]
        let p = NSPanel(
            contentRect: rect,
            styleMask: style,
            backing: .buffered,
            defer: false
        )
        p.title = "steelg8 · 捕获台"
        p.level = .floating    // 召唤时浮在其它窗口上（非 always-on-top，hide 后就消失）
        p.isMovableByWindowBackground = true
        p.titleVisibility = .hidden
        p.titlebarAppearsTransparent = true
        p.hidesOnDeactivate = false  // 主动 hide，不因失焦就消失
        p.animationBehavior = .utilityWindow
        p.hasShadow = true

        let config = WKWebViewConfiguration()
        config.websiteDataStore = WKWebsiteDataStore.nonPersistent()
        config.setValue(true, forKey: "allowUniversalAccessFromFileURLs")
        config.preferences.setValue(true, forKey: "allowFileAccessFromFileURLs")
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")

        let wv = WKWebView(frame: rect, configuration: config)
        wv.setValue(false, forKey: "drawsBackground")
        wv.autoresizingMask = [.width, .height]
        if #available(macOS 13.3, *) {
            wv.isInspectable = true
        }
        p.contentView = wv

        // 加载 Web/chat/index.html#scratch
        if let indexURL = locateIndex() {
            let dir = indexURL.deletingLastPathComponent()
            // 用 WKWebView 的 loadFileRequest 会把 hash 吃掉，这里用 loadFileURL + 手写 hash
            var comps = URLComponents(url: indexURL, resolvingAgainstBaseURL: false)!
            comps.fragment = "scratch"
            if let u = comps.url {
                wv.loadFileURL(u, allowingReadAccessTo: dir)
            } else {
                wv.loadFileURL(indexURL, allowingReadAccessTo: dir)
            }
        } else {
            wv.loadHTMLString(
                "<body style='font-family:-apple-system,sans-serif;padding:40px;color:#999'>"
                + "<h3>未找到 Web/chat/index.html</h3></body>",
                baseURL: nil
            )
        }

        self.panel = p
        self.webView = wv
    }

    private func centerOnMouseScreen(_ panel: NSPanel) {
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first(where: { NSMouseInRect(mouse, $0.frame, false) })
                     ?? NSScreen.main
                     ?? NSScreen.screens.first
        guard let screen else { return }
        let size = panel.frame.size
        let frame = screen.visibleFrame
        let origin = NSPoint(
            x: frame.midX - size.width / 2,
            y: frame.midY - size.height / 2
        )
        panel.setFrameOrigin(origin)
    }

    private func locateIndex() -> URL? {
        if let bundled = Bundle.main.url(
            forResource: "index",
            withExtension: "html",
            subdirectory: "Web/chat"
        ) {
            return bundled
        }
        let dev = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()     // Chat/
            .deletingLastPathComponent()     // steelg8/
            .deletingLastPathComponent()     // Sources/
            .deletingLastPathComponent()     // repo root
            .appendingPathComponent("Web/chat/index.html")
        return FileManager.default.fileExists(atPath: dev.path) ? dev : nil
    }
}
