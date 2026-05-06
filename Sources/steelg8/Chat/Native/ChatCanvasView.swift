import SwiftUI

// MARK: - ChatCanvasView

/// Canvas 面板：左侧 Markdown 源码，右侧实时预览。
struct ChatCanvasView: View {
    @ObservedObject var vm: ChatViewModel

    var body: some View {
        HSplitView {
            // 左：源码编辑
            TextEditor(text: $vm.canvasContent)
                .font(.system(.body, design: .monospaced))
                .frame(minWidth: 240)

            // 右：Markdown 预览
            ScrollView {
                MarkdownView(markdown: vm.canvasContent)
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(minWidth: 240)
        }
        // toolbar 控制统一在 NativeChatView 管理，此处不重复添加
    }
}
