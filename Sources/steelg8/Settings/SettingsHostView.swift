import AppKit
import SwiftUI

struct SettingsHostView: View {
    @State private var selection: SettingsSection = .general
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            detail(for: selection)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: 860, minHeight: 500)
    }

    // MARK: - 自定义 Sidebar

    private var sidebar: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(SettingsSection.Group.allCases) { group in
                    sectionHeader(group.title)
                    ForEach(group.sections) { section in
                        sidebarRow(section)
                    }
                    Spacer().frame(height: 8)
                }
            }
            .padding(.vertical, 8)
        }
        .frame(width: 190)
        .background(SG.sidebarBg(colorScheme))
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 10.5, weight: .semibold))
            .tracking(0.5)
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
            .padding(.top, 8)
            .padding(.bottom, 2)
    }

    private func sidebarRow(_ section: SettingsSection) -> some View {
        let isSelected = selection == section
        return Button { selection = section } label: {
            HStack(spacing: 6) {
                Image(systemName: section.systemImage)
                    .font(.system(size: 11.5))
                    .frame(width: 16)
                    .foregroundStyle(isSelected ? .primary : Color.secondary)
                Text(section.title)
                    .font(.system(size: 12.5, weight: isSelected ? .medium : .regular))
                    .foregroundStyle(isSelected ? .primary : .primary)
                    .lineLimit(1)
                Spacer()
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .frame(minHeight: 26)
            .background(
                isSelected ? SG.sidebarSelected(colorScheme) : Color.clear
            )
            .clipShape(RoundedRectangle(cornerRadius: 5))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 6)
    }

    // MARK: - Detail

    @ViewBuilder
    private func detail(for section: SettingsSection) -> some View {
        switch section {
        case .general:         GeneralPage()
        case .topbar:          TopbarPage()
        case .soul:            SoulPage()
        case .userMemory:      UserMemoryPage()
        case .providersAdmin:  ProvidersPage()
        case .modelAdmin:      ModelManagementPage()
        case .router:          RouterPage()
        case .rag:             RAGManagementPage()
        case .runtimeCost:     CostPage()
        case .runtimeHealth:   HealthPage()
        case .runtimeIndex:    IndexPage()
        case .runtimeRAG:      RAGPage()
        case .runtimeLog:      LogPage()
        }
    }
}
