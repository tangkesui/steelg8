import SwiftUI

// MARK: - ChatSidebarView

struct ChatSidebarView: View {
    @ObservedObject var vm: ChatViewModel
    @Binding var showNewProject: Bool
    @Binding var showSearch: Bool
    @Binding var showModelConfig: Bool
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(spacing: 0) {
            sidebarActionBar
            Divider()
            projectSection
            Divider()
            scratchSection
        }
        .frame(width: 220)
        .background(SG.sidebarBg(colorScheme))
    }

    // MARK: - 功能按钮栏（竖向列表，图标+文字）

    private var sidebarActionBar: some View {
        VStack(spacing: 0) {
            navRow(icon: "folder.badge.plus", label: "新建项目") { showNewProject = true }
            navRow(icon: "magnifyingglass",   label: "搜索")     { showSearch = true }
            navRow(icon: "slider.horizontal.3", label: "模型选择") { showModelConfig = true }
        }
        .padding(.vertical, 4)
    }

    private func navRow(icon: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .font(.system(size: 14))
                    .frame(width: 18)
                Text(label)
                    .font(.system(size: 14))
                Spacer()
            }
            .foregroundStyle(Color.secondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    // MARK: - Projects

    private var projectSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            sectionHeader("项目") { EmptyView() }

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


    // MARK: - Scratch

    private var scratchSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("便签")
                .font(.system(size: 10.5, weight: .semibold))
                .tracking(0.5)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 10)
                .padding(.top, 8)

            ScratchTextEditor(text: $vm.scratchText, colorScheme: colorScheme)
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .frame(minHeight: 160, maxHeight: .infinity)
                .padding(.horizontal, 8)
                .padding(.bottom, 8)
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
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Image(systemName: "folder")
                    .font(.system(size: 11))
                    .foregroundStyle(Color.secondary)
                Text(proj.name)
                    .font(.system(size: 12.5, weight: proj.active ? .medium : .regular))
                    .lineLimit(1)
                Spacer()
            }
            if let count = proj.chunkCount {
                HStack(spacing: 4) {
                    // 状态点：仅当该项目处于索引中时显示橙色，否则根据 chunk 数判断
                    let isIndexing = proj.active && vm.projectStatus?.state == "running"
                    Circle()
                        .fill(isIndexing ? Color.orange : (count > 0 ? Color.green : Color.secondary.opacity(0.4)))
                        .frame(width: 5, height: 5)
                    Text(isIndexing ? "索引中…" : "\(count) 块")
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                }
                .padding(.leading, 17)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .frame(minHeight: 26)
        .background(proj.active ? SG.sidebarSelected(colorScheme) : Color.clear)
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
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "bubble.left")
                .font(.system(size: 10))
                .foregroundStyle(Color.secondary)
            Text(conv.title ?? "新对话")
                .font(.system(size: 12.5, weight: isActive ? .medium : .regular))
                .lineLimit(1)
                .foregroundStyle(isActive ? .primary : .secondary)
            Spacer()
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .frame(minHeight: 26)
        .background(isActive ? SG.sidebarSelected(colorScheme) : Color.clear)
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

// MARK: - ScratchTextEditor（隐藏滚动条）

private struct ScratchTextEditor: NSViewRepresentable {
    @Binding var text: String
    let colorScheme: ColorScheme

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.scrollerStyle = .overlay
        scroll.borderType = .noBorder
        scroll.backgroundColor = .clear

        let tv = NSTextView()
        tv.isEditable = true
        tv.isRichText = false
        tv.allowsUndo = true
        tv.font = .monospacedSystemFont(ofSize: 14, weight: .regular)
        tv.textColor = .labelColor
        tv.backgroundColor = .clear
        tv.textContainerInset = NSSize(width: 6, height: 6)
        tv.isVerticallyResizable = true
        tv.isHorizontallyResizable = false
        tv.autoresizingMask = [.width]
        tv.textContainer?.widthTracksTextView = true
        tv.delegate = context.coordinator
        context.coordinator.textView = tv

        scroll.documentView = tv
        return scroll
    }

    func updateNSView(_ nsView: NSScrollView, context: Context) {
        guard let tv = nsView.documentView as? NSTextView else { return }
        let bg: NSColor = colorScheme == .dark
            ? NSColor.white.withAlphaComponent(0.06)
            : .white
        nsView.backgroundColor = bg
        tv.backgroundColor = bg
        if tv.string != text { tv.string = text }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        @Binding var text: String
        weak var textView: NSTextView?
        init(text: Binding<String>) { _text = text }
        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            text = tv.string
        }
    }
}

// MARK: - NewProjectPanel

struct NewProjectPanel: View {
    @Binding var isPresented: Bool
    let onConfirm: (String, String, Bool) -> Void

    @State private var projectName = ""
    @State private var projectPath = ""
    @State private var enableRAG = true

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("新建项目")
                .font(.system(size: 14, weight: .semibold))

            VStack(alignment: .leading, spacing: 8) {
                label("项目名称")
                TextField("留空则使用目录名", text: $projectName)
                    .textFieldStyle(.roundedBorder)
            }

            VStack(alignment: .leading, spacing: 8) {
                label("项目路径")
                HStack(spacing: 6) {
                    TextField("选择目录…", text: $projectPath)
                        .textFieldStyle(.roundedBorder)
                    Button("选择…") { pickDirectory() }
                }
            }

            Toggle("启用文档索引 (RAG)", isOn: $enableRAG)
                .toggleStyle(.switch)

            HStack {
                Spacer()
                Button("取消") { isPresented = false }
                    .keyboardShortcut(.cancelAction)
                Button("确认") {
                    onConfirm(projectPath, projectName, enableRAG)
                    isPresented = false
                }
                .buttonStyle(.bordered)
                .disabled(projectPath.isEmpty)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 320)
    }

    private func label(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.secondary)
    }

    @MainActor
    private func pickDirectory() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            projectPath = url.path
            if projectName.isEmpty {
                projectName = url.lastPathComponent
            }
        }
    }
}
