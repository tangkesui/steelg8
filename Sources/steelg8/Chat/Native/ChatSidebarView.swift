import SwiftUI

// MARK: - ChatSidebarView

struct ChatSidebarView: View {
    @ObservedObject var vm: ChatViewModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(spacing: 0) {
            projectSection
            Divider()
            scratchSection
        }
        .frame(width: 220)
        .background(SG.sidebarBg(colorScheme))
    }

    // MARK: - Projects

    private var projectSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionHeader("项目") {
                Button { Task { await openProject() } } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.plain)
                .help("打开项目目录")
            }

            if let status = vm.projectStatus {
                projectStatusBadge(status)
                    .padding(.horizontal, 8)
                    .padding(.bottom, 4)
            }

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 1) {
                    ForEach(vm.projects) { proj in
                        ProjectRow(proj: proj, vm: vm)
                    }
                }
                .padding(.vertical, 4)
            }
        }
        .frame(maxHeight: .infinity)
    }

    private func projectStatusBadge(_ status: ProjectStatus) -> some View {
        let color: Color = status.state == "idle" ? .green
                         : status.state == "running" ? .orange : .red
        let label = status.state == "idle" ? "就绪"
                  : status.state == "running" ? "索引中…" : "出错"
        return HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(label).font(.caption2).foregroundStyle(.secondary)
            if let cnt = status.count {
                Text("·\(cnt) 块").font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }

    @MainActor
    private func openProject() async {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard await panel.begin() == .OK, let url = panel.url else { return }
        do {
            try await ChatAPI().openProject(path: url.path)
            await vm.loadProjects()
        } catch {}
    }

    // MARK: - Scratch

    private var scratchSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("便签")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 8)
                .padding(.top, 6)

            TextEditor(text: $vm.scratchText)
                .font(.system(size: 11.5, design: .monospaced))
                .frame(minHeight: 120, idealHeight: 160, maxHeight: 220)
                .padding(4)
                .onChange(of: vm.scratchText) { vm.scheduleScratchSave() }
        }
        .padding(.bottom, 6)
    }

    // MARK: - Header helper

    private func sectionHeader<T: View>(_ title: String, @ViewBuilder trailing: () -> T) -> some View {
        HStack {
            Text(title)
                .font(.system(size: 10.5, weight: .semibold))
                .tracking(0.5)
                .foregroundStyle(.secondary)
            Spacer()
            trailing()
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
    }
}

// MARK: - ProjectRow

private struct ProjectRow: View {
    let proj: ProjectItem
    @ObservedObject var vm: ChatViewModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: proj.active ? "folder.fill" : "folder")
                .font(.system(size: 11))
                .foregroundStyle(proj.active ? Color.accentColor : Color.secondary)
            Text(proj.name)
                .font(.system(size: 12))
                .lineLimit(1)
            Spacer()
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .frame(minHeight: 26)
        .background(proj.active ? Color.accentColor.opacity(0.12) : Color.clear)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .contentShape(Rectangle())
        .onTapGesture {
            if !proj.active { Task { await vm.activateProject(proj) } }
        }
        .contextMenu {
            if proj.active {
                Button("重新索引") { Task { await vm.reindexProject() } }
                Divider()
                Button("关闭项目") { Task { await vm.closeProject() } }
            }
            Button("删除", role: .destructive) { Task { await vm.deleteProject(proj) } }
        }
    }
}

// MARK: - ConversationRow

private struct ConversationRow: View {
    let conv: ConversationItem
    let isActive: Bool
    let onSelect: () -> Void
    let onRename: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "bubble.left")
                .font(.system(size: 10))
                .foregroundStyle(isActive ? Color.accentColor : Color.secondary)
            Text(conv.title ?? "新对话")
                .font(.system(size: 12))
                .lineLimit(1)
                .foregroundStyle(isActive ? .primary : .secondary)
            Spacer()
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .frame(minHeight: 26)
        .background(isActive ? Color.accentColor.opacity(0.12) : Color.clear)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
        .contextMenu {
            Button("重命名", action: onRename)
            Divider()
            Button("删除", role: .destructive, action: onDelete)
        }
    }
}

// MARK: - RenameSheet

private struct RenameSheet: View {
    let title: String
    @Binding var text: String
    let onConfirm: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 16) {
            Text(title).font(.headline)
            TextField("标题", text: $text)
                .textFieldStyle(.roundedBorder)
                .frame(width: 260)
            HStack(spacing: 12) {
                Button("取消") { dismiss() }
                Button("确认") { onConfirm(); dismiss() }
                    .buttonStyle(.borderedProminent)
                    .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(24)
    }
}
