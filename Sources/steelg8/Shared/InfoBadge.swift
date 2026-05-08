import SwiftUI

/// 设置项右侧的「ⓘ」小图标。点击弹出窗口级浮层（SwiftUI .popover，
/// 走 NSPopover 实现），不会被父 view 边界遮挡；固定宽 260pt、
/// 高度随文字增长。点其它地方或 ESC 自动关闭。
struct InfoBadge: View {
    let text: String
    @State private var show = false

    var body: some View {
        Button {
            show.toggle()
        } label: {
            Image(systemName: "info.circle")
                .foregroundStyle(show ? Color.primary : Color.secondary)
                .imageScale(.small)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .popover(isPresented: $show, arrowEdge: .trailing) {
            Text(text)
                .font(.system(size: 11.5))
                .lineSpacing(2)
                .foregroundStyle(.primary)
                .multilineTextAlignment(.leading)
                .padding(.horizontal, 11)
                .padding(.vertical, 8)
                .frame(width: 260, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
