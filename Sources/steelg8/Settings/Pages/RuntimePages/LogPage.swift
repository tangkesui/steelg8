import SwiftUI

// 12.7：日志页，对接 GET /logs
struct LogPage: View {
    @StateObject private var vm = LogViewModel()

    var body: some View {
        VStack(spacing: 0) {
            filterBar
            Divider()
            Group {
                if let err = vm.error {
                    ContentUnavailableView(
                        "加载失败",
                        systemImage: "exclamationmark.triangle",
                        description: Text(err)
                    )
                } else if vm.items.isEmpty && !vm.isLoading {
                    ContentUnavailableView(
                        "暂无日志",
                        systemImage: "doc.text",
                        description: Text("最近 \(vm.days) 天内没有符合条件的日志。")
                    )
                } else {
                    logList
                }
            }
        }
        .task { await vm.load() }
    }

    private var filterBar: some View {
        HStack(spacing: 12) {
            Picker("级别", selection: $vm.levelFilter) {
                Text("全部").tag("")
                Text("warn+").tag("warn")
                Text("error").tag("error")
                Text("info+").tag("info")
                Text("debug+").tag("debug")
            }
            .labelsHidden()
            .frame(width: 90)

            Picker("天数", selection: $vm.days) {
                Text("今日").tag(1)
                Text("2 天").tag(2)
                Text("7 天").tag(7)
            }
            .labelsHidden()
            .frame(width: 70)

            if vm.isLoading { ProgressView().controlSize(.small) }

            Spacer()
            Text("\(vm.items.count) 条").font(.caption).foregroundStyle(.secondary)
            Button { Task { await vm.load() } } label: {
                Label("刷新", systemImage: "arrow.clockwise")
            }
            .disabled(vm.isLoading)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color(nsColor: .windowBackgroundColor))
        .onChange(of: vm.levelFilter) { _, _ in Task { await vm.load() } }
        .onChange(of: vm.days) { _, _ in Task { await vm.load() } }
    }

    private var logList: some View {
        Table(vm.items) {
            TableColumn("时间") { item in
                Text(shortTime(item.ts))
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            .width(70)

            TableColumn("级别") { item in
                Text(item.level)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(levelColor(item.level))
            }
            .width(50)

            TableColumn("事件") { item in
                Text(item.event)
                    .font(.system(.caption, design: .monospaced))
            }
            .width(min: 140, ideal: 200)

            TableColumn("消息") { item in
                Text(item.message ?? "")
                    .font(.system(.body))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
    }

    private func shortTime(_ ts: String) -> String {
        // ts 格式：2026-05-06T12:34:56.789+00:00  或  ISO8601
        // 只取 HH:mm:ss 部分
        if ts.count >= 19 {
            let start = ts.index(ts.startIndex, offsetBy: 11)
            let end = ts.index(ts.startIndex, offsetBy: 19)
            return String(ts[start..<end])
        }
        return ts
    }

    private func levelColor(_ level: String) -> Color {
        switch level {
        case "error": return .red
        case "warn": return .orange
        case "debug": return .purple
        default: return .secondary
        }
    }
}

@MainActor
final class LogViewModel: ObservableObject {
    @Published var items: [LogItem] = []
    @Published var isLoading = false
    @Published var error: String?
    @Published var levelFilter = ""
    @Published var days = 2

    private let api = RuntimeAPI()

    func load() async {
        isLoading = true
        error = nil
        do {
            let resp = try await api.logs(
                limit: 500,
                days: days,
                level: levelFilter.isEmpty ? nil : levelFilter
            )
            items = resp.items
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}
