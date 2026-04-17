import SwiftUI

@main
struct SteelG8App: App {
    @StateObject private var appController = AppController.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(appController)
                .task {
                    appController.setup()
                }
        }
        .defaultSize(width: 1080, height: 700)
        .windowResizability(.contentMinSize)
    }
}
