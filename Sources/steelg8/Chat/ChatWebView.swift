import AppKit
import SwiftUI
import WebKit

/// 开发阶段禁用 WKWebView 持久 Cache，避免改了 HTML/JS/CSS 后加载不到最新版本。
/// Release 可去掉。
private func purgeWebViewCaches() {
    let store = WKWebsiteDataStore.default()
    let types: Set<String> = [
        WKWebsiteDataTypeMemoryCache,
        WKWebsiteDataTypeDiskCache,
        WKWebsiteDataTypeOfflineWebApplicationCache,
    ]
    store.removeData(
        ofTypes: types,
        modifiedSince: Date(timeIntervalSince1970: 0),
        completionHandler: {}
    )
}

/// WKWebView 承载 Web/chat/index.html。Phase 1 的主交互窗口。
///
/// 策略：
/// - 本地 file:// 协议加载，WKWebView 允许访问到整个 Web/chat 目录（loadFileURL(allowingReadAccessTo:)）
/// - 注入当前 kernel 端口，chat.js 再用 localhost 拉后端
/// - 开发阶段允许 web inspector；release 再关
struct ChatWebView: NSViewRepresentable {

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> WKWebView {
        purgeWebViewCaches()

        let config = WKWebViewConfiguration()
        config.websiteDataStore = WKWebsiteDataStore.nonPersistent()
        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        config.defaultWebpagePreferences = prefs

        // 注入 JS 桥：前端调 `window.webkit.messageHandlers.steelg8.postMessage(...)`
        config.userContentController.add(context.coordinator, name: "steelg8")
        config.userContentController.addUserScript(
            WKUserScript(
                source: KernelConfig.webBootstrapScript,
                injectionTime: .atDocumentStart,
                forMainFrameOnly: true
            )
        )

        // 允许 file:// 访问本地其他文件、跨源读 127.0.0.1（本地内核）
        // 这些都是 WKWebView 未公开的 KVC key，在 macOS 14+ 仍可用
        config.setValue(true, forKey: "allowUniversalAccessFromFileURLs")
        config.preferences.setValue(true, forKey: "allowFileAccessFromFileURLs")
        #if DEBUG
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        #endif

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.setValue(false, forKey: "drawsBackground") // 让 SwiftUI 的背景色透出来
        webView.allowsLinkPreview = false
        webView.allowsBackForwardNavigationGestures = false
        webView.uiDelegate = context.coordinator   // 让 JS 的 alert/confirm/prompt 能弹出原生面板

        #if DEBUG
        if #available(macOS 13.3, *) {
            webView.isInspectable = true
        }
        #endif

        loadChatPage(into: webView)
        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {
        // 外部状态变化时不重载，chat.js 自己拉后端
    }

    // MARK: - helpers

    private func loadChatPage(into webView: WKWebView) {
        guard let indexURL = locateIndexHTML() else {
            let fallback = """
            <html><body style='font-family:-apple-system,sans-serif;padding:40px;color:#999'>
            <h3>找不到 Web/chat/index.html</h3>
            <p>请确保项目根目录下存在 <code>Web/chat/</code> 资源。</p>
            </body></html>
            """
            webView.loadHTMLString(fallback, baseURL: nil)
            return
        }

        let directory = indexURL.deletingLastPathComponent()
        webView.loadFileURL(indexURL, allowingReadAccessTo: directory)
    }

    /// 按多路径查找 Web/chat/index.html：
    /// 1) 打包成 .app 后：Resources/Web/chat/index.html
    /// 2) 开发阶段：仓库根 Web/chat/index.html
    private func locateIndexHTML() -> URL? {
        KernelConfig.webIndexURL
    }

    /// JS→Swift 消息派发（+ UIDelegate 让 alert/confirm/prompt 能弹出来）
    final class Coordinator: NSObject, WKScriptMessageHandler, WKUIDelegate {

        // MARK: - WKUIDelegate: JS 对话框
        func webView(
            _ webView: WKWebView,
            runJavaScriptAlertPanelWithMessage message: String,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping () -> Void
        ) {
            let alert = NSAlert()
            alert.messageText = "steelg8"
            alert.informativeText = message
            alert.addButton(withTitle: "好")
            alert.runModal()
            completionHandler()
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptConfirmPanelWithMessage message: String,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping (Bool) -> Void
        ) {
            let alert = NSAlert()
            alert.messageText = "steelg8"
            alert.informativeText = message
            alert.addButton(withTitle: "确认")
            alert.addButton(withTitle: "取消")
            let resp = alert.runModal()
            completionHandler(resp == .alertFirstButtonReturn)
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptTextInputPanelWithPrompt prompt: String,
            defaultText: String?,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping (String?) -> Void
        ) {
            let alert = NSAlert()
            alert.messageText = "steelg8"
            alert.informativeText = prompt
            let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 22))
            field.stringValue = defaultText ?? ""
            alert.accessoryView = field
            alert.addButton(withTitle: "确认")
            alert.addButton(withTitle: "取消")
            let resp = alert.runModal()
            completionHandler(resp == .alertFirstButtonReturn ? field.stringValue : nil)
        }

        func userContentController(
            _ controller: WKUserContentController,
            didReceive msg: WKScriptMessage
        ) {
            guard let body = msg.body as? [String: Any] else { return }
            let action = (body["action"] as? String) ?? ""
            let payload = body
            Task { @MainActor in
                Coordinator.handle(action: action, payload: payload)
            }
        }

        @MainActor
        static func handle(action: String, payload: [String: Any] = [:]) {
            switch action {
            case "openProjectPicker":
                Task { @MainActor in
                    _ = await ProjectPicker.pickAndOpen()
                }
            case "closeProject":
                Task {
                    var req = URLRequest(url: KernelConfig.url(path: "project/close"))
                    req.httpMethod = "POST"
                    req.httpBody = "{}".data(using: .utf8)
                    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    KernelConfig.authorize(&req)
                    _ = try? await URLSession.shared.data(for: req)
                }
            case "reindexProject":
                Task {
                    var req = URLRequest(url: KernelConfig.url(path: "project/reindex"))
                    req.httpMethod = "POST"
                    req.httpBody = "{}".data(using: .utf8)
                    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    KernelConfig.authorize(&req)
                    _ = try? await URLSession.shared.data(for: req)
                }
            case "openFile":
                guard let path = payload["path"] as? String, let url = URL(fileURLWithPath: path) as URL? else {
                    return
                }
                if FileManager.default.fileExists(atPath: path) {
                    NSWorkspace.shared.open(url)
                } else {
                    NSLog("steelg8: openFile 目标不存在：\(path)")
                }
            case "revealInFinder":
                guard let path = payload["path"] as? String else { return }
                if FileManager.default.fileExists(atPath: path) {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
                }
            case "saveToNotes":
                let folder = (payload["folder"] as? String) ?? "steelg8"
                let title = (payload["title"] as? String) ?? "steelg8 捕获"
                let body = (payload["body"] as? String) ?? ""
                Task {
                    let result = await NotesBridge.addNote(folder: folder, title: title, body: body)
                    await MainActor.run {
                        Coordinator.notifyNotesResult(result)
                    }
                }
            default:
                NSLog("steelg8: 未知 JS bridge action: \(action)")
            }
        }

        /// 把 Notes 操作结果通过系统通知回显给用户
        @MainActor
        static func notifyNotesResult(_ result: Result<Void, NotesBridge.NotesError>) {
            let item = AppController.shared.statusItem
            switch result {
            case .success:
                StatusItemMessagePresenter().present("已存到 Apple 备忘录", on: item)
            case .failure(let err):
                NSLog("steelg8 Notes failed: \(err.localizedDescription)")
                StatusItemMessagePresenter().present("存备忘录失败：\(err.localizedDescription)", on: item)
            }
        }
    }
}
