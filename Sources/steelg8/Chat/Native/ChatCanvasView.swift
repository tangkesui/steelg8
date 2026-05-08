import SwiftUI

// MARK: - ChatCanvasView

struct ChatCanvasView: View {
    @ObservedObject var vm: ChatViewModel
    @State private var tab: Tab = .preview
    @Environment(\.colorScheme) private var colorScheme

    enum Tab { case edit, preview }

    var body: some View {
        VStack(spacing: 0) {
            // Tab bar
            HStack(spacing: 0) {
                tabButton("预览", tag: .preview)
                tabButton("编辑", tag: .edit)
                Spacer()
            }
            .padding(.horizontal, 12)
            .frame(height: 36)
            .background(SG.chrome(colorScheme))

            Divider()

            // Content
            Group {
                if vm.canvasContent.isEmpty {
                    emptyState
                } else if tab == .preview {
                    ScrollView {
                        MarkdownView(markdown: vm.canvasContent)
                            .padding(20)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                } else {
                    TextEditor(text: $vm.canvasContent)
                        .font(.system(.body, design: .monospaced))
                        .scrollContentBackground(.hidden)
                        .background(SG.bg(colorScheme))
                        .padding(4)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(SG.bg(colorScheme))
        }
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "doc.plaintext")
                .font(.system(size: 32))
                .foregroundStyle(.tertiary)
            Text("Canvas 为空")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.secondary)
            Text("对话中提到「写入 Canvas」\n或在编辑栏直接输入")
                .font(.system(size: 11.5))
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func tabButton(_ label: String, tag: Tab) -> some View {
        Button { tab = tag } label: {
            Text(label)
                .font(.system(size: 12, weight: tab == tag ? .semibold : .regular))
                .foregroundStyle(tab == tag ? .primary : .secondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(
                    tab == tag
                        ? RoundedRectangle(cornerRadius: 5).fill(SG.sidebarSelected(colorScheme))
                        : nil
                )
        }
        .buttonStyle(.plain)
    }
}
