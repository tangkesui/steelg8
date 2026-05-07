import AppKit
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var appController: AppController

    var body: some View {
        NativeChatView()
            .background(
                WindowConfigurator { window in
                    appController.configureMainWindow(window)
                }
            )
    }
}
