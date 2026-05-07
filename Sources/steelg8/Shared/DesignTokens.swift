import SwiftUI
import AppKit

// MARK: - Hex color initializers

extension Color {
    init(hex: String) {
        var h = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        if h.count == 3 { h = h.map { String($0) + String($0) }.joined() }
        var val: UInt64 = 0
        Scanner(string: h).scanHexInt64(&val)
        self.init(
            red: Double((val >> 16) & 0xff) / 255,
            green: Double((val >> 8) & 0xff) / 255,
            blue: Double(val & 0xff) / 255
        )
    }
}

extension NSColor {
    convenience init(hex: String) {
        var h = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        if h.count == 3 { h = h.map { String($0) + String($0) }.joined() }
        var val: UInt64 = 0
        Scanner(string: h).scanHexInt64(&val)
        self.init(
            red: CGFloat((val >> 16) & 0xff) / 255,
            green: CGFloat((val >> 8) & 0xff) / 255,
            blue: CGFloat(val & 0xff) / 255,
            alpha: 1
        )
    }
}

// MARK: - Design tokens

enum SG {
    static func bg(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color(hex: "#0f1014") : Color(hex: "#f7f7f5")
    }
    static func surface(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color(hex: "#1a1c22") : Color(hex: "#ffffff")
    }
    static func chrome(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color(hex: "#1a1c22") : Color(hex: "#ececec")
    }
    static func sidebarBg(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color(hex: "#16181d") : Color(hex: "#efefec")
    }
    static func userBubble(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color(hex: "#2b2f3a") : Color(hex: "#ece8e1")
    }
    static func codeBg(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color(hex: "#1a1c22") : Color(hex: "#f0f0ed")
    }
    static func codeBorder(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color.white.opacity(0.06) : Color.black.opacity(0.08)
    }
    static func sidebarHover(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color.white.opacity(0.04) : Color.black.opacity(0.04)
    }
    static func sidebarSelected(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color(hex: "#e0e2ea").opacity(0.10) : Color(hex: "#2b2f3a").opacity(0.10)
    }
    static func pillBg(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color.white.opacity(0.06) : Color.black.opacity(0.05)
    }
    static let sendBlue = Color(hex: "#4a8bff")
    static func success(_ cs: ColorScheme) -> Color {
        cs == .dark ? Color(hex: "#6bff9b") : Color(hex: "#1f8a5b")
    }
    static let danger = Color(hex: "#ff6b6b")

    // MARK: - Syntax colors (NSColor for NSAttributedString)

    enum Syntax {
        static func keyword(dark: Bool) -> NSColor   { NSColor(hex: dark ? "#c98aff" : "#a347d3") }
        static func string(dark: Bool) -> NSColor    { NSColor(hex: dark ? "#a4d4a4" : "#3a8a4a") }
        static func number(dark: Bool) -> NSColor    { NSColor(hex: dark ? "#e8b25a" : "#a06820") }
        static func comment(dark: Bool) -> NSColor   { NSColor(hex: dark ? "#6c7280" : "#8a8e99") }
        static func function_(dark: Bool) -> NSColor { NSColor(hex: dark ? "#79b8ff" : "#3a78c8") }
        static func type_(dark: Bool) -> NSColor     { NSColor(hex: dark ? "#7ad0c6" : "#2f8a85") }
    }
}
