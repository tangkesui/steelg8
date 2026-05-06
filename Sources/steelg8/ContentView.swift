import AppKit
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var appController: AppController
    @AppStorage("experimental.nativeChat") private var nativeChat = true

    var body: some View {
        Group {
            if nativeChat {
                NativeChatView()
            } else {
                ChatWebView()
                    .frame(minWidth: 960, minHeight: 640)
            }
        }
        .background(
            WindowConfigurator { window in
                appController.configureMainWindow(window)
            }
        )
    }
}
