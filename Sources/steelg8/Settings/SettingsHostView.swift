import AppKit
import SwiftUI

/// 设置窗口的容器壳：左侧 NavigationSplitView 分组 + 右侧子页。Phase 12.2 引入。
struct SettingsHostView: View {

    @State private var selection: SettingsSection = .general

    var body: some View {
        ZStack(alignment: .top) {
            NavigationSplitView {
                sidebar
                    .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 220)
            } detail: {
                detail(for: selection)
                    .frame(minWidth: 680, minHeight: 420)
            }

            WindowDragArea()
                .frame(height: 36)
                .frame(maxWidth: .infinity)
                .allowsHitTesting(true)
        }
        .frame(minWidth: 900, minHeight: 520)
        .background(
            WindowConfigurator { window in
                window.isMovable = true
                window.isMovableByWindowBackground = true
                window.minSize = NSSize(width: 900, height: 520)
                window.title = "steelg8 Settings"
            }
        )
    }

    private var sidebar: some View {
        List(selection: $selection) {
            ForEach(SettingsSection.Group.allCases) { group in
                Section(group.title) {
                    ForEach(group.sections) { section in
                        Label(section.title, systemImage: section.systemImage)
                            .tag(section)
                    }
                }
            }
        }
        .listStyle(.sidebar)
    }

    @ViewBuilder
    private func detail(for section: SettingsSection) -> some View {
        switch section {
        case .general:        GeneralPage()
        case .topbar:         TopbarPage()
        case .soul:           SoulPage()
        case .userMemory:     UserMemoryPage()
        case .providers:      ProvidersPage()
        case .modelProfiles:  ModelProfilesPage()
        case .runtimeCost:    CostPage()
        case .runtimeHealth:  HealthPage()
        case .runtimeIndex:   IndexPage()
        case .runtimeRAG:     RAGPage()
        case .runtimeLog:     LogPage()
        }
    }
}

#Preview {
    SettingsHostView()
}
