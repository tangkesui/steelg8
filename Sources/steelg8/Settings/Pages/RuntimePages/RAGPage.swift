import SwiftUI

// 12.7：RAG 调试页，对接 POST /diagnostics/rag-debug
struct RAGPage: View {
    @StateObject private var vm = RAGViewModel()
    @State private var query = ""

    var body: some View {
        VStack(spacing: 0) {
            queryBar
            Divider()
            resultArea
        }
    }

    private var queryBar: some View {
        HStack(spacing: 8) {
            TextField("输入问题，查看向量召回结果…", text: $query)
                .textFieldStyle(.roundedBorder)
                .onSubmit { Task { await vm.search(query: query) } }
            Button {
                Task { await vm.search(query: query) }
            } label: {
                Text("搜索")
            }
            .disabled(query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || vm.isLoading)

            if vm.isLoading {
                ProgressView().controlSize(.small)
            }
        }
        .padding(12)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    @ViewBuilder
    private var resultArea: some View {
        if let err = vm.error {
            ContentUnavailableView(
                "搜索失败",
                systemImage: "exclamationmark.triangle",
                description: Text(err)
            )
        } else if let data = vm.data {
            if data.finalHits.isEmpty {
                ContentUnavailableView(
                    "没有命中",
                    systemImage: "doc.text.magnifyingglass",
                    description: Text("没有与 \"\(data.query)\" 相关的索引内容。")
                )
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        Text("命中 \(data.finalHits.count) 条（按相关性排序）")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 16)
                            .padding(.top, 12)
                        ForEach(data.finalHits) { hit in
                            hitCard(hit)
                        }
                    }
                    .padding(.bottom, 12)
                }
            }
        } else {
            ContentUnavailableView(
                "RAG 调试",
                systemImage: "doc.text.magnifyingglass",
                description: Text("输入问题后点击搜索，查看向量检索命中的文档片段。\n需要先激活一个项目并完成索引。")
            )
        }
    }

    private func hitCard(_ hit: RAGHit) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(hit.relPath)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                Text("#\(hit.chunkIdx)")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.tertiary)
                Spacer()
                Text(String(format: "%.4f", hit.score))
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.blue)
                if let r = hit.retrieval {
                    Text(r).font(.caption2).foregroundStyle(.purple)
                        .padding(.horizontal, 4)
                        .background(Color.purple.opacity(0.1))
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                }
            }
            if let text = hit.text, !text.isEmpty {
                Text(text)
                    .font(.system(.caption))
                    .foregroundStyle(.primary)
                    .lineLimit(5)
            }
        }
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .padding(.horizontal, 12)
    }
}

@MainActor
final class RAGViewModel: ObservableObject {
    @Published var data: RAGDebugResponse?
    @Published var isLoading = false
    @Published var error: String?

    private let api = RuntimeAPI()

    func search(query: String) async {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return }
        isLoading = true
        error = nil
        data = nil
        do {
            data = try await api.diagnosticsRAGDebug(query: q)
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}
