import SwiftUI

// MARK: - ChatMessagesView

/// 消息列表 + 自动滚动到最新消息。
struct ChatMessagesView: View {
    @ObservedObject var vm: ChatViewModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 22) {
                    ForEach(vm.messages) { msg in
                        MessageView(message: msg)
                            .id(msg.id)
                    }

                    // 哨兵节点：始终滚动到底部
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.vertical, 16)
            }
            .background(SG.bg(colorScheme))
            .onChange(of: vm.messages.count) {
                scrollToBottom(proxy)
            }
            .onChange(of: vm.messages.last?.content) {
                scrollToBottom(proxy)
            }
            .onAppear {
                scrollToBottom(proxy, animated: false)
            }
        }
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy, animated: Bool = true) {
        if animated {
            withAnimation(.easeOut(duration: 0.15)) {
                proxy.scrollTo("bottom", anchor: .bottom)
            }
        } else {
            proxy.scrollTo("bottom", anchor: .bottom)
        }
    }
}
