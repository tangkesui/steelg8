import SwiftUI
import WebKit

// MARK: - 块级 Markdown 解析器

enum MarkdownBlock {
    case heading(level: Int, text: String)
    case codeBlock(language: String?, code: String)
    case blockquote(lines: [String])
    case unorderedList(items: [String])
    case orderedList(items: [(Int, String)])
    case thematicBreak
    case paragraph(text: String)
}

enum MarkdownParser {
    static func parse(_ markdown: String) -> [MarkdownBlock] {
        let lines = markdown.components(separatedBy: "\n")
        var blocks: [MarkdownBlock] = []
        var i = 0

        while i < lines.count {
            let startIndex = i
            defer {
                if i == startIndex {
                    i += 1
                }
            }

            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // 空行：跳过
            if trimmed.isEmpty { i += 1; continue }

            // ATX heading
            if let h = parseHeading(trimmed) {
                blocks.append(h); i += 1; continue
            }

            // 围栏代码块
            if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
                let fence = trimmed.hasPrefix("```") ? "```" : "~~~"
                let lang = String(trimmed.dropFirst(fence.count)).trimmingCharacters(in: .whitespaces)
                var code: [String] = []
                i += 1
                while i < lines.count {
                    let cl = lines[i]
                    if cl.trimmingCharacters(in: .whitespaces).hasPrefix(fence) { i += 1; break }
                    code.append(cl); i += 1
                }
                blocks.append(.codeBlock(language: lang.isEmpty ? nil : lang, code: code.joined(separator: "\n")))
                continue
            }

            // 引用块
            if trimmed.hasPrefix(">") {
                var quoteLines: [String] = []
                while i < lines.count {
                    let ql = lines[i].trimmingCharacters(in: .whitespaces)
                    if ql.isEmpty { break }
                    if ql.hasPrefix(">") {
                        quoteLines.append(String(ql.dropFirst(1)).trimmingCharacters(in: .whitespaces))
                    } else {
                        break
                    }
                    i += 1
                }
                blocks.append(.blockquote(lines: quoteLines)); continue
            }

            // 主题分隔线
            if isThematicBreak(trimmed) { blocks.append(.thematicBreak); i += 1; continue }

            // 无序列表
            if isULItem(trimmed) {
                var items: [String] = []
                while i < lines.count {
                    let il = lines[i].trimmingCharacters(in: .whitespaces)
                    if isULItem(il) { items.append(ulContent(il)); i += 1 }
                    else if il.isEmpty { i += 1; break }
                    else { break }
                }
                blocks.append(.unorderedList(items: items)); continue
            }

            // 有序列表
            if let (_, _) = parseOLItem(trimmed) {
                var items: [(Int, String)] = []
                while i < lines.count {
                    let il = lines[i].trimmingCharacters(in: .whitespaces)
                    if let (num, text) = parseOLItem(il) { items.append((num, text)); i += 1 }
                    else if il.isEmpty { i += 1; break }
                    else { break }
                }
                blocks.append(.orderedList(items: items)); continue
            }

            // 段落（连续非空行合并）
            var paraLines: [String] = []
            while i < lines.count {
                let pl = lines[i]
                let pt = pl.trimmingCharacters(in: .whitespaces)
                if pt.isEmpty { i += 1; break }
                if parseHeading(pt) != nil { break }
                if pt.hasPrefix("```") || pt.hasPrefix("~~~") { break }
                if pt.hasPrefix(">") { break }
                if isThematicBreak(pt) { break }
                if isULItem(pt) { break }
                if parseOLItem(pt) != nil { break }
                paraLines.append(pl); i += 1
            }
            if !paraLines.isEmpty {
                blocks.append(.paragraph(text: paraLines.joined(separator: "\n")))
            }
        }
        return blocks
    }

    private static func parseHeading(_ line: String) -> MarkdownBlock? {
        var level = 0
        for ch in line { if ch == "#" { level += 1 } else { break } }
        guard level >= 1, level <= 6, line.count > level else { return nil }
        let after = line[line.index(line.startIndex, offsetBy: level)...]
        guard after.hasPrefix(" ") else { return nil }
        let text = String(after.dropFirst()).trimmingCharacters(in: .whitespaces)
        return .heading(level: level, text: text)
    }

    private static func isThematicBreak(_ line: String) -> Bool {
        let cleaned = line.filter { !$0.isWhitespace }
        guard cleaned.count >= 3 else { return false }
        return cleaned.allSatisfy { $0 == "-" } || cleaned.allSatisfy { $0 == "*" } || cleaned.allSatisfy { $0 == "_" }
    }

    private static func isULItem(_ line: String) -> Bool {
        guard line.count >= 2 else { return false }
        let first = line.first!
        return (first == "-" || first == "*" || first == "+") && line[line.index(after: line.startIndex)] == " "
    }

    private static func ulContent(_ line: String) -> String {
        guard line.count >= 2 else { return line }
        return String(line.dropFirst(2))
    }

    private static func parseOLItem(_ line: String) -> (Int, String)? {
        var numStr = ""
        var idx = line.startIndex
        while idx < line.endIndex, line[idx].isNumber {
            numStr.append(line[idx])
            idx = line.index(after: idx)
        }
        guard !numStr.isEmpty,
              idx < line.endIndex,
              line[idx] == ".",
              let num = Int(numStr)
        else { return nil }
        idx = line.index(after: idx)
        guard idx < line.endIndex, line[idx] == " " else { return nil }
        idx = line.index(after: idx)
        guard idx <= line.endIndex else { return nil }
        return (num, String(line[idx...]))
    }
}

// MARK: - インライン レンダラ

enum InlineRenderer {
    static func attributedString(from text: String) -> AttributedString {
        if text.rangeOfCharacter(from: CharacterSet(charactersIn: "*_`[~")) == nil {
            return AttributedString(text)
        }

        var result = AttributedString()
        var remaining = text

        while !remaining.isEmpty {
            // **bold**
            if let r = findDelimited(in: remaining, open: "**", close: "**") {
                result += AttributedString(remaining[..<r.before])
                var bold = AttributedString(remaining[r.content])
                bold.font = .boldSystemFont(ofSize: SG.chatBody)
                result += bold
                remaining = String(remaining[r.after...])
                continue
            }
            // *italic* or _italic_
            if let r = findDelimited(in: remaining, open: "*", close: "*")
                ?? findDelimited(in: remaining, open: "_", close: "_") {
                result += AttributedString(remaining[..<r.before])
                var italic = AttributedString(remaining[r.content])
                italic.font = NSFontManager.shared.convert(
                    NSFont.systemFont(ofSize: SG.chatBody), toHaveTrait: .italicFontMask)
                result += italic
                remaining = String(remaining[r.after...])
                continue
            }
            // ~~strikethrough~~
            if let r = findDelimited(in: remaining, open: "~~", close: "~~") {
                result += AttributedString(remaining[..<r.before])
                var strike = AttributedString(remaining[r.content])
                strike.strikethroughStyle = .single
                result += strike
                remaining = String(remaining[r.after...])
                continue
            }
            // `inline code`
            if let r = findDelimited(in: remaining, open: "`", close: "`") {
                result += AttributedString(remaining[..<r.before])
                var code = AttributedString(remaining[r.content])
                code.font = NSFont.monospacedSystemFont(ofSize: SG.chatBody - 1, weight: .regular)
                code.backgroundColor = NSColor.textBackgroundColor.withAlphaComponent(0.5)
                result += code
                remaining = String(remaining[r.after...])
                continue
            }
            // [text](url)
            if let r = findLink(in: remaining) {
                result += AttributedString(remaining[..<r.before])
                var link = AttributedString(remaining[r.text])
                if let url = URL(string: String(remaining[r.url])) {
                    link.link = url
                }
                link.foregroundColor = NSColor.linkColor
                result += link
                remaining = String(remaining[r.after...])
                continue
            }
            // 普通字符
            let nextSpecial = findNextSpecial(in: remaining)
            let slice = remaining[..<nextSpecial]
            result += AttributedString(slice)
            remaining = String(remaining[nextSpecial...])
        }
        return result
    }

    // 辅助结构
    private struct DelimRange {
        var before: String.Index
        var content: Range<String.Index>
        var after: String.Index
    }

    private struct LinkRange {
        var before: String.Index
        var text: Range<String.Index>
        var url: Range<String.Index>
        var after: String.Index
    }

    private static func findDelimited(in s: String, open: String, close: String) -> DelimRange? {
        guard let openRange = s.range(of: open) else { return nil }
        let afterOpen = openRange.upperBound
        guard afterOpen < s.endIndex else { return nil }
        // close must not start at same position as open
        let searchStart = s.index(afterOpen, offsetBy: 1, limitedBy: s.endIndex) ?? afterOpen
        guard searchStart <= s.endIndex,
              let closeRange = s.range(of: close, range: searchStart..<s.endIndex)
        else { return nil }
        return DelimRange(before: openRange.lowerBound, content: afterOpen..<closeRange.lowerBound, after: closeRange.upperBound)
    }

    private static func findLink(in s: String) -> LinkRange? {
        guard let bracketOpen = s.firstIndex(of: "[") else { return nil }
        guard let bracketClose = s.range(of: "](", range: bracketOpen..<s.endIndex) else { return nil }
        guard let parenClose = s.range(of: ")", range: bracketClose.upperBound..<s.endIndex) else { return nil }
        return LinkRange(
            before: bracketOpen,
            text: s.index(after: bracketOpen)..<bracketClose.lowerBound,
            url: bracketClose.upperBound..<parenClose.lowerBound,
            after: parenClose.upperBound
        )
    }

    private static func findNextSpecial(in s: String) -> String.Index {
        let specials: [Character] = ["*", "_", "`", "[", "~"]
        var idx = s.startIndex
        while idx < s.endIndex {
            if specials.contains(s[idx]) { return idx }
            idx = s.index(after: idx)
        }
        return s.endIndex
    }
}

private extension NSFont {
    func with(traits: NSFontTraitMask) -> NSFont {
        NSFontManager.shared.convert(self, toHaveTrait: traits)
    }
}

// MARK: - MarkdownView：整体渲染

struct MarkdownView: View {
    let markdown: String
    var isStreaming: Bool = false
    var onCanvasOpen: ((String) -> Void)? = nil

    var body: some View {
        // 流式阶段用纯 Text（O(1)），避免每个 delta 触发解析和 AttributedString 构建
        if isStreaming || markdown.count > 12_000 {
            Text(markdown)
                .font(.system(size: SG.chatBody))
                .lineSpacing(SG.chatLineSpacing)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            let parsedBlocks = MarkdownParser.parse(markdown)
            VStack(alignment: .leading, spacing: SG.chatParagraphSpacing) {
                ForEach(parsedBlocks.indices, id: \.self) { i in
                    blockView(parsedBlocks[i])
                }
            }
        }
    }

    @ViewBuilder
    private func blockView(_ block: MarkdownBlock) -> some View {
        switch block {
        case .heading(let level, let text):
            Text(InlineRenderer.attributedString(from: text))
                .font(headingFont(level))
                .bold()

        case .codeBlock(let language, let code):
            if language == "mermaid" {
                MermaidView(source: code)
                    .frame(minHeight: 80)
            } else {
                CodeBlockView(language: language, code: code)
            }

        case .blockquote(let lines):
            HStack(alignment: .top, spacing: 8) {
                Rectangle()
                    .fill(Color.accentColor.opacity(0.4))
                    .frame(width: 3)
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(lines.indices, id: \.self) { i in
                        Text(InlineRenderer.attributedString(from: lines[i]))
                            .font(.system(size: SG.chatBody))
                            .lineSpacing(SG.chatLineSpacing)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.leading, 4)

        case .unorderedList(let items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(items.indices, id: \.self) { i in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("•")
                            .font(.system(size: SG.chatBody))
                            .foregroundStyle(.secondary)
                        Text(InlineRenderer.attributedString(from: items[i]))
                            .font(.system(size: SG.chatBody))
                            .lineSpacing(SG.chatLineSpacing)
                    }
                }
            }

        case .orderedList(let items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(items.indices, id: \.self) { i in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("\(items[i].0).")
                            .font(.system(size: SG.chatBody, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .frame(minWidth: 20, alignment: .trailing)
                        Text(InlineRenderer.attributedString(from: items[i].1))
                            .font(.system(size: SG.chatBody))
                            .lineSpacing(SG.chatLineSpacing)
                    }
                }
            }

        case .thematicBreak:
            Divider()

        case .paragraph(let text):
            Text(InlineRenderer.attributedString(from: text))
                .font(.system(size: SG.chatBody))
                .lineSpacing(SG.chatLineSpacing)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: return .title
        case 2: return .title2
        case 3: return .title3
        default: return .headline
        }
    }
}

// MARK: - 代码块视图（含基础高亮）

struct CodeBlockView: View {
    let language: String?
    let code: String
    @State private var copied = false
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // 语言标签 + 复制按钮
            HStack {
                if let lang = language, !lang.isEmpty {
                    Text(lang)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(SG.success(colorScheme))
                }
                Spacer()
                Button { copyCode() } label: {
                    HStack(spacing: 4) {
                        Image(systemName: copied ? "checkmark" : "doc.on.doc")
                        Text(copied ? "已复制" : "复制")
                    }
                    .font(.system(size: 11))
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12)
            .frame(height: 28)
            .background(SG.codeBg(colorScheme).opacity(0.6))

            Divider().opacity(0.5)

            // 代码内容
            ScrollView(.horizontal, showsIndicators: false) {
                Text(AttributedString(highlightedCode()))
                    .font(.system(size: SG.chatBody - 1, design: .monospaced))
                    .textSelection(.enabled)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .background(SG.codeBg(colorScheme))
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(SG.codeBorder(colorScheme), lineWidth: 1))
    }

    private func copyCode() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(code, forType: .string)
        withAnimation { copied = true }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            withAnimation { copied = false }
        }
    }

    private func highlightedCode() -> NSAttributedString {
        let lang = language?.lowercased() ?? ""
        let isDark = colorScheme == .dark
        return SyntaxHighlighter.highlight(code: code, language: lang, isDark: isDark)
    }
}

// MARK: - 基础语法高亮器

enum SyntaxHighlighter {
    static func highlight(code: String, language: String, isDark: Bool = true) -> NSAttributedString {
        let result = NSMutableAttributedString(string: code)
        let fullRange = NSRange(code.startIndex..., in: code)
        result.addAttribute(.font, value: NSFont.monospacedSystemFont(ofSize: SG.chatBody - 1, weight: .regular), range: fullRange)

        // 注释（必须最先处理，避免注释内容被其他规则染色）
        applyColor(to: result, code: code, pattern: #"(//[^\n]*|#[^\n]*|/\*[\s\S]*?\*/)"#,
                   color: SG.Syntax.comment(dark: isDark))

        // 字符串
        applyColor(to: result, code: code, pattern: #"("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`[^`]*`)"#,
                   color: SG.Syntax.string(dark: isDark))

        // 数字
        applyColor(to: result, code: code, pattern: #"\b\d+(?:\.\d+)?\b"#,
                   color: SG.Syntax.number(dark: isDark))

        // 关键字
        let kws = keywords(for: language)
        if !kws.isEmpty {
            let kw = kws.map { NSRegularExpression.escapedPattern(for: $0) }.joined(separator: "|")
            applyColor(to: result, code: code, pattern: #"\b(?:"# + kw + #")\b"#,
                       color: SG.Syntax.keyword(dark: isDark))
        }

        // 函数调用
        applyColor(to: result, code: code, pattern: #"\b([a-zA-Z_]\w*)\s*(?=\()"#,
                   color: SG.Syntax.function_(dark: isDark))

        return result
    }

    private static func applyColor(to str: NSMutableAttributedString, code: String, pattern: String, color: NSColor) {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return }
        let fullRange = NSRange(code.startIndex..., in: code)
        let matches = regex.matches(in: code, range: fullRange)
        for match in matches {
            str.addAttribute(.foregroundColor, value: color, range: match.range)
        }
    }

    private static func keywords(for language: String) -> [String] {
        switch language {
        case "swift":
            return ["func", "var", "let", "if", "else", "for", "while", "return", "class", "struct",
                    "enum", "protocol", "import", "guard", "switch", "case", "default", "break", "continue",
                    "true", "false", "nil", "self", "super", "init", "deinit", "get", "set", "async", "await",
                    "throws", "try", "catch", "throw", "in", "where", "extension", "typealias", "override"]
        case "python", "py":
            return ["def", "class", "if", "elif", "else", "for", "while", "return", "import", "from",
                    "as", "with", "try", "except", "finally", "raise", "lambda", "and", "or", "not",
                    "True", "False", "None", "in", "is", "pass", "break", "continue", "async", "await"]
        case "javascript", "js", "typescript", "ts":
            return ["function", "const", "let", "var", "if", "else", "for", "while", "return", "class",
                    "import", "export", "default", "async", "await", "try", "catch", "throw", "new",
                    "true", "false", "null", "undefined", "typeof", "instanceof", "this", "of", "in"]
        case "bash", "sh", "shell", "zsh":
            return ["if", "then", "else", "elif", "fi", "for", "do", "done", "while", "case", "esac",
                    "function", "return", "local", "echo", "export", "source"]
        case "go":
            return ["func", "var", "const", "type", "if", "else", "for", "range", "return", "package",
                    "import", "struct", "interface", "map", "chan", "go", "defer", "select", "switch",
                    "case", "default", "break", "continue", "true", "false", "nil"]
        default:
            return []
        }
    }
}
