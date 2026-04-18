import Foundation

struct HotkeyDefinition: Identifiable {
    let id: String
    let title: String
    let shortcut: String
    let isImplemented: Bool

    var status: String {
        isImplemented ? "已接线" : "待实现"
    }
}

enum HotkeyRegistry {
    static let items: [HotkeyDefinition] = [
        HotkeyDefinition(id: "capture-ocr", title: "截图 OCR", shortcut: "⌘⇧D", isImplemented: true),
        HotkeyDefinition(id: "translate", title: "划词翻译", shortcut: "⌘⇧T", isImplemented: false),
        HotkeyDefinition(id: "scratch", title: "Scratch 召唤窗", shortcut: "⌘⇧N", isImplemented: true),
        HotkeyDefinition(id: "chat", title: "主对话窗", shortcut: "⌘⇧A", isImplemented: false),
        HotkeyDefinition(id: "capture-project", title: "截图存入当前项目", shortcut: "⌘⇧S", isImplemented: false)
    ]
}
