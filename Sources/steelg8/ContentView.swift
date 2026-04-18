import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var appController: AppController

    var body: some View {
        ChatWebView()
            .frame(minWidth: 960, minHeight: 640)
        // OCR 走菜单栏 + ⌘⇧D 热键
        // 运行状态看菜单栏图标 + 对话顶部的 pill
    }
}
