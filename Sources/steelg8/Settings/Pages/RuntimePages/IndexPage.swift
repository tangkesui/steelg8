import SwiftUI

// 12.7：索引页，对接 /diagnostics/index
struct IndexPage: View {
    @StateObject private var vm = IndexViewModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let err = vm.error {
                    Label(err, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.red)
                        .frame(maxWidth: .infinity)
                } else if let data = vm.data {
                    statusBanner(data)
                    if let proj = data.activeProject {
                        projectSection(proj)
                    }
                    if let manifest = data.manifest {
                        manifestSection(manifest)
                    }
                } else {
                    ProgressView("加载中…").frame(maxWidth: .infinity)
                }
            }
            .padding(20)
        }
        .safeAreaInset(edge: .bottom) { refreshBar }
        .task { await vm.load() }
    }

    private var refreshBar: some View {
        HStack {
            if let t = vm.fetchedAt {
                Text("更新于 \(t)").font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button { Task { await vm.load() } } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }
            .disabled(vm.isLoading)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private func statusBanner(_ data: IndexResponse) -> some View {
        let color: Color = data.level == "ok" ? .green : (data.level == "warn" ? .orange : .red)
        let msg = data.message ?? (data.ok ? "索引状态正常" : "索引有问题")
        return HStack(spacing: 8) {
            Image(systemName: data.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(color)
            Text(msg).font(.headline)
        }
        .padding(10)
        .background(color.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func projectSection(_ proj: ActiveProjectInfo) -> some View {
        GroupBox("活跃项目") {
            VStack(alignment: .leading, spacing: 6) {
                if let name = proj.name {
                    LabeledContent("名称", value: name)
                }
                LabeledContent("路径") {
                    Text(proj.path)
                        .font(.system(.body, design: .monospaced))
                        .textSelection(.enabled)
                        .foregroundStyle(.secondary)
                }
                if let chunks = proj.chunkCount {
                    LabeledContent("总 chunk 数", value: "\(chunks)")
                }
                if let embed = proj.embedModel {
                    LabeledContent("嵌入模型", value: embed)
                }
            }
        }
    }

    private func manifestSection(_ m: IndexManifest) -> some View {
        GroupBox("文件清单") {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 24) {
                    statItem("已索引文件", "\(m.count)")
                    statItem("总 chunk 数", "\(m.totalChunks)")
                    statItem("过期文件", "\(m.staleFiles.count)", warn: !m.staleFiles.isEmpty)
                    statItem("缺失文件", "\(m.missingManifestFiles.count)", warn: !m.missingManifestFiles.isEmpty)
                }

                if !m.staleFiles.isEmpty {
                    Divider()
                    Text("过期文件（已删除但 manifest 未更新）").font(.caption).foregroundStyle(.secondary)
                    ForEach(m.staleFiles.prefix(10), id: \.self) { f in
                        Text("· " + f).font(.system(.caption, design: .monospaced)).foregroundStyle(.orange)
                    }
                }

                if !m.missingManifestFiles.isEmpty {
                    Divider()
                    Text("新文件（还未索引）").font(.caption).foregroundStyle(.secondary)
                    ForEach(m.missingManifestFiles.prefix(10), id: \.self) { f in
                        Text("· " + f).font(.system(.caption, design: .monospaced)).foregroundStyle(.blue)
                    }
                }

                if let items = m.items, !items.isEmpty {
                    Divider()
                    Text("已索引文件样本（前 \(items.count) 条）").font(.caption).foregroundStyle(.secondary)
                    Table(items) {
                        TableColumn("路径") { item in
                            Text(item.relPath)
                                .font(.system(.caption, design: .monospaced))
                                .textSelection(.enabled)
                        }
                        TableColumn("chunk") { item in
                            Text("\(item.chunkCount)")
                                .font(.system(.caption, design: .monospaced))
                        }
                        .width(50)
                        TableColumn("存在") { item in
                            Image(systemName: item.exists ? "checkmark.circle.fill" : "xmark.circle")
                                .foregroundStyle(item.exists ? .green : .red)
                        }
                        .width(44)
                    }
                    .frame(minHeight: 120, maxHeight: 240)
                }
            }
        }
    }

    private func statItem(_ label: String, _ value: String, warn: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value)
                .font(.title3.monospacedDigit())
                .foregroundStyle(warn ? .orange : .primary)
        }
    }
}

@MainActor
final class IndexViewModel: ObservableObject {
    @Published var data: IndexResponse?
    @Published var isLoading = false
    @Published var error: String?
    @Published var fetchedAt: String?

    private let api = RuntimeAPI()

    func load() async {
        isLoading = true
        error = nil
        do {
            data = try await api.diagnosticsIndex()
            let fmt = DateFormatter()
            fmt.dateFormat = "HH:mm:ss"
            fetchedAt = fmt.string(from: Date())
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}
