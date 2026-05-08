import AppKit
import SwiftUI

final class SteelG8AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        // 启动期就把外观偏好套上，避免一闪系统模式再切到用户选择
        let prefs = AppPreferencesStore.shared.loadOrDefaults()
        (AppAppearance(rawValue: prefs.appearance) ?? AppPreferencesStore.defaultAppearance).apply()
        AppController.shared.setup()
        DispatchQueue.main.async {
            AppController.shared.showMainWindow()
        }
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        AppController.shared.showMainWindow()
        return true
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        AppController.shared.ensureStatusBarVisible()
    }
}

@main
struct SteelG8App: App {
    @NSApplicationDelegateAdaptor(SteelG8AppDelegate.self) private var appDelegate
    @StateObject private var appController = AppController.shared

    var body: some Scene {
        WindowGroup("steelg8", id: "main") {
            ContentView()
                .environmentObject(appController)
                .task {
                    appController.setup()
                }
        }
        .defaultSize(width: 1080, height: 700)
        .windowResizability(.contentMinSize)

        // macOS Settings scene：自动接 ⌘, 快捷键 + 菜单栏"设置…"
        Settings {
            SettingsHostView()
        }
    }
}
