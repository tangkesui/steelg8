import SwiftUI
import AppKit

// MARK: - ComposerView

/// 多行输入框：⏎ 发送，Shift+⏎ 换行。
/// 通过 @Binding height 把所需高度回传 SwiftUI，由 frame(height:) 精确控制尺寸。
struct ComposerView: NSViewRepresentable {
    @Binding var text: String
    @Binding var height: CGFloat
    var onSend: () -> Void

    static let minHeight: CGFloat = 44   // ≈ 2 行
    static let maxHeight: CGFloat = 130  // ≈ 6 行，超出后内部滚动

    func makeCoordinator() -> Coordinator {
        Coordinator(text: $text, height: $height, onSend: onSend)
    }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.borderType = .noBorder
        scroll.backgroundColor = .clear

        let textView = NSTextView()
        textView.isEditable = true
        textView.isRichText = false
        textView.allowsUndo = true
        textView.isAutomaticQuoteSubstitutionEnabled = false
        textView.isAutomaticDashSubstitutionEnabled = false
        textView.font = .systemFont(ofSize: NSFont.systemFontSize)
        textView.textColor = .labelColor
        textView.backgroundColor = .clear
        textView.textContainerInset = NSSize(width: 2, height: 5)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.autoresizingMask = [.width]
        textView.textContainer?.widthTracksTextView = true

        textView.delegate = context.coordinator
        context.coordinator.textView = textView

        scroll.documentView = textView
        return scroll
    }

    func updateNSView(_ nsView: NSScrollView, context: Context) {
        guard let tv = nsView.documentView as? NSTextView else { return }
        // IME 输入中（marked text 状态，例如中文拼音预览）时，外部 SSE re-render
        // 触发的 updateNSView 不能动 tv.string，否则会把进行中的 marked text 抹掉，
        // 用户感受为"打字打到一半被打断"。
        if tv.hasMarkedText() { return }
        if tv.string != text {
            tv.string = text
            context.coordinator.recalcHeight(tv)
        }
    }

    // MARK: - Coordinator

    final class Coordinator: NSObject, NSTextViewDelegate {
        @Binding var text: String
        @Binding var height: CGFloat
        var onSend: () -> Void
        weak var textView: NSTextView?

        init(text: Binding<String>, height: Binding<CGFloat>, onSend: @escaping () -> Void) {
            _text = text
            _height = height
            self.onSend = onSend
        }

        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            text = tv.string
            recalcHeight(tv)
        }

        func recalcHeight(_ tv: NSTextView) {
            guard let lm = tv.layoutManager, let tc = tv.textContainer else { return }
            lm.ensureLayout(for: tc)
            let used = lm.usedRect(for: tc)
            let inset = tv.textContainerInset
            let lineH = tv.font?.boundingRectForFont.height ?? 16
            let minH = lineH * 2 + inset.height * 2
            let natural = used.height + inset.height * 2
            let newH = min(ComposerView.maxHeight, max(minH, natural))
            if abs(newH - height) > 0.5 {
                DispatchQueue.main.async { self.height = newH }
            }
        }

        func textView(_ textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
            if commandSelector == #selector(NSResponder.insertNewline(_:)) {
                let shift = NSEvent.modifierFlags.contains(.shift)
                if !shift { onSend(); return true }
            }
            return false
        }
    }
}
