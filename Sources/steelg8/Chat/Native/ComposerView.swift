import SwiftUI
import AppKit

// MARK: - ComposerView

/// 多行输入框：⏎ 发送，Shift+⏎ 换行。
/// 封装 NSTextView 以获得正确的高度自适应行为。
struct ComposerView: NSViewRepresentable {
    @Binding var text: String
    var onSend: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(text: $text, onSend: onSend) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.borderType = .bezelBorder

        let textView = ComposerTextView()
        textView.isEditable = true
        textView.isRichText = false
        textView.allowsUndo = true
        textView.isAutomaticQuoteSubstitutionEnabled = false
        textView.isAutomaticDashSubstitutionEnabled = false
        textView.font = .systemFont(ofSize: NSFont.systemFontSize)
        textView.textColor = .labelColor
        textView.backgroundColor = .textBackgroundColor
        textView.textContainerInset = NSSize(width: 4, height: 6)
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
        if tv.string != text {
            tv.string = text
        }
    }

    // MARK: - Coordinator

    final class Coordinator: NSObject, NSTextViewDelegate {
        @Binding var text: String
        var onSend: () -> Void
        weak var textView: NSTextView?

        init(text: Binding<String>, onSend: @escaping () -> Void) {
            _text = text
            self.onSend = onSend
        }

        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            text = tv.string
        }

        func textView(_ textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
            // ⏎ 发送；Shift+⏎ 换行
            if commandSelector == #selector(NSResponder.insertNewline(_:)) {
                let shift = NSEvent.modifierFlags.contains(.shift)
                if !shift {
                    onSend()
                    return true
                }
            }
            return false
        }
    }
}

// NSTextView 子类：覆盖 intrinsicContentSize 支持高度自适应
private final class ComposerTextView: NSTextView {
    override var intrinsicContentSize: NSSize {
        guard let lm = layoutManager, let tc = textContainer else {
            return super.intrinsicContentSize
        }
        lm.ensureLayout(for: tc)
        let used = lm.usedRect(for: tc)
        let inset = textContainerInset
        let h = max(60, used.height + inset.height * 2 + 12)
        return NSSize(width: NSView.noIntrinsicMetric, height: h)
    }

    override func didChangeText() {
        super.didChangeText()
        invalidateIntrinsicContentSize()
    }
}
