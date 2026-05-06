import SwiftUI
import WebKit

// MARK: - MermaidView

/// 用 WKWebView 沙箱渲染单个 Mermaid 代码块。
/// 直接加载 Web/chat/mermaid-frame.html，注入 postMessage 桥接脚本，
/// 通过 evaluateJavaScript 发送 mermaid:render 事件，
/// 接收 mermaid:rendered / mermaid:error 回调并动态调整高度。
struct MermaidView: NSViewRepresentable {
    let source: String

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()

        // 注入 postMessage 桥：拦截 parent.postMessage / window.postMessage → webkit.messageHandlers
        let shim = """
        (function () {
          var orig = window.postMessage.bind(window);
          function bridge(data, target) {
            try { webkit.messageHandlers.mermaidBridge.postMessage(data); } catch (_) {}
            orig(data, target || "*");
          }
          // parent === window 在顶级 WKWebView 里，直接替换即可
          Object.defineProperty(window, 'postMessage', { value: bridge, writable: true, configurable: true });
          try {
            Object.defineProperty(window, 'parent', {
              get: function () { return { postMessage: bridge }; },
              configurable: true
            });
          } catch (_) {}
        })();
        """
        let shimScript = WKUserScript(source: shim,
                                      injectionTime: .atDocumentStart,
                                      forMainFrameOnly: true)
        config.userContentController.addUserScript(shimScript)
        config.userContentController.add(context.coordinator, name: "mermaidBridge")

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        context.coordinator.webView = webView
        context.coordinator.source = source

        // 透明背景
        webView.setValue(false, forKey: "drawsBackground")

        // 加载 mermaid-frame.html（优先 bundle，否则 dev 路径）
        if let url = mermaidFrameURL() {
            webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        }

        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {
        if context.coordinator.source != source {
            context.coordinator.source = source
            context.coordinator.renderPending = true
            context.coordinator.sendRenderIfReady()
        }
    }

    private func mermaidFrameURL() -> URL? {
        // Bundle 模式
        if let res = Bundle.main.resourceURL {
            let u = res.appendingPathComponent("Web/chat/mermaid-frame.html")
            if FileManager.default.fileExists(atPath: u.path) { return u }
        }
        // Dev 模式：从源码目录推断
        let src = URL(fileURLWithPath: #filePath)
        let devRoot = src
            .deletingLastPathComponent() // Native/
            .deletingLastPathComponent() // Chat/
            .deletingLastPathComponent() // steelg8/ (Sources/steelg8)
            .deletingLastPathComponent() // Sources/
            .deletingLastPathComponent() // steelg8/ (package root)
        let u = devRoot.appendingPathComponent("Web/chat/mermaid-frame.html")
        return FileManager.default.fileExists(atPath: u.path) ? u : nil
    }

    // MARK: - Coordinator

    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        weak var webView: WKWebView?
        var source: String = ""
        var renderPending = false
        private var ready = false
        private var renderSeq = 0

        func userContentController(_ ucc: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard let dict = message.body as? [String: Any],
                  let type = dict["type"] as? String else { return }
            switch type {
            case "mermaid:ready":
                ready = true
                sendRenderIfReady()
            case "mermaid:rendered":
                if let h = dict["height"] as? CGFloat, let wv = webView {
                    DispatchQueue.main.async {
                        wv.frame.size.height = h
                        // 触发 SwiftUI 重新布局
                        wv.invalidateIntrinsicContentSize()
                    }
                }
            case "mermaid:error":
                break
            default:
                break
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            // HTML 加载完毕后等 mermaid:ready 消息
        }

        func sendRenderIfReady() {
            guard ready, renderPending, let wv = webView else { return }
            renderPending = false
            renderSeq += 1
            let id = "mmd_\(renderSeq)"
            let escaped = source
                .replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "`", with: "\\`")
                .replacingOccurrences(of: "$", with: "\\$")
            let js = """
            window.dispatchEvent(new MessageEvent('message', {
              data: { type: 'mermaid:render', id: '\(id)', source: `\(escaped)` }
            }));
            """
            wv.evaluateJavaScript(js, completionHandler: nil)
        }

        // 初次加载也要触发渲染
        func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
            ready = false
            renderPending = true
        }
    }
}
