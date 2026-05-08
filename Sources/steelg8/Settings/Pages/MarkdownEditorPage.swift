import SwiftUI

/// 共享的 Markdown 文件编辑页：读 / 编辑 / 保存 / 还原 / 用外部编辑器打开。
/// 给 SoulPage / UserMemoryPage 复用。
struct MarkdownEditorPage: View {

    let title: String
    let subtitle: String
    let fileURL: URL
    /// 文件不存在时使用的初始内容；返回字符串。nil 表示不要预填、由用户从空白开始。
    let stubProvider: (() -> String)?

    @State private var content: String = ""
    @State private var loadedSnapshot: String = ""
    @State private var statusMessage: String?
    @State private var statusIsError: Bool = false
    @State private var hasLoaded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.title3.bold())
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(fileURL.path)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            .padding(.horizontal, 16)
            .padding(.top, 16)

            TextEditor(text: $content)
                .font(.body.monospaced())
                .padding(.horizontal, 16)
                .padding(.top, 8)
        }
        .safeAreaInset(edge: .bottom) {
            footerBar
        }
        .task {
            if !hasLoaded {
                hasLoaded = true
                load()
            }
        }
    }

    private var footerBar: some View {
        HStack(spacing: 12) {
            Button {
                NSWorkspace.shared.open(fileURL)
            } label: {
                Label("用外部编辑器打开", systemImage: "arrow.up.right.square")
            }
            .buttonStyle(.borderless)

            if let status = statusMessage {
                Text(status)
                    .font(.caption)
                    .foregroundStyle(statusIsError ? .red : .secondary)
            }
            Spacer()
            Button("默认") {
                content = loadedSnapshot
                statusMessage = nil
                statusIsError = false
            }
            .keyboardShortcut("r", modifiers: [.command])
            .disabled(content == loadedSnapshot)

            Button("保存") {
                save()
            }
            .buttonStyle(.borderedProminent)
            .keyboardShortcut("s", modifiers: [.command])
            .disabled(content == loadedSnapshot)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.bar)
    }

    private func load() {
        let fm = FileManager.default
        let dir = fileURL.deletingLastPathComponent()
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)

        if !fm.fileExists(atPath: fileURL.path) {
            if let stubProvider {
                let stub = stubProvider()
                try? stub.write(to: fileURL, atomically: true, encoding: .utf8)
                content = stub
                loadedSnapshot = stub
                statusMessage = "文件不存在，已用初始模板创建。"
                statusIsError = false
            } else {
                content = ""
                loadedSnapshot = ""
            }
            return
        }

        do {
            let raw = try String(contentsOf: fileURL, encoding: .utf8)
            content = raw
            loadedSnapshot = raw
            statusMessage = nil
            statusIsError = false
        } catch {
            statusMessage = "读取失败：\(error.localizedDescription)"
            statusIsError = true
        }
    }

    private func save() {
        do {
            try content.write(to: fileURL, atomically: true, encoding: .utf8)
            loadedSnapshot = content
            statusMessage = "已保存到 \(fileURL.lastPathComponent)。"
            statusIsError = false
        } catch {
            statusMessage = "保存失败：\(error.localizedDescription)"
            statusIsError = true
        }
    }
}
