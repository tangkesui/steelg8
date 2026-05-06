import SwiftUI

// 12.7：费用页，对接 /usage/summary
struct CostPage: View {
    @StateObject private var vm = CostViewModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let err = vm.error {
                    errorView(err)
                } else if let data = vm.data {
                    statsCards(data)
                    if !data.sessionBreakdown.isEmpty {
                        breakdownSection(data)
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

    private func errorView(_ msg: String) -> some View {
        Label(msg, systemImage: "exclamationmark.triangle")
            .foregroundStyle(.red)
            .frame(maxWidth: .infinity)
    }

    private func statsCards(_ data: UsageSummaryResponse) -> some View {
        let cny = data.usdToCny
        return HStack(spacing: 12) {
            statCard(
                title: "本次会话",
                costCny: data.session.costUSD * cny,
                calls: data.session.calls,
                tokens: data.session.total
            )
            statCard(
                title: "今日",
                costCny: data.today.costUSD * cny,
                calls: data.today.calls,
                tokens: data.today.total
            )
            statCard(
                title: "历史累计",
                costCny: data.total.costUSD * cny,
                calls: data.total.calls,
                tokens: data.total.total
            )
        }
    }

    private func statCard(title: String, costCny: Double, calls: Int, tokens: Int) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(String(format: "¥%.4f", costCny))
                .font(.title2.monospacedDigit())
            HStack(spacing: 8) {
                Label("\(calls) 次", systemImage: "arrow.left.arrow.right")
                Label(formatTokens(tokens), systemImage: "character.cursor.ibeam")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func breakdownSection(_ data: UsageSummaryResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("本次会话模型拆分").font(.headline)
            Table(data.sessionBreakdown) {
                TableColumn("模型") { b in
                    Text(b.model).font(.system(.body, design: .monospaced))
                }
                TableColumn("Provider") { b in
                    Text(b.provider).foregroundStyle(.secondary)
                }
                TableColumn("调用") { b in
                    Text("\(b.calls)").font(.system(.body, design: .monospaced))
                }
                .width(50)
                TableColumn("Token") { b in
                    Text(formatTokens(b.prompt + b.completion))
                        .font(.system(.body, design: .monospaced))
                }
                .width(80)
                TableColumn("费用 ¥") { b in
                    Text(String(format: "%.4f", b.costUSD * data.usdToCny))
                        .font(.system(.body, design: .monospaced))
                }
                .width(80)
            }
            .frame(minHeight: 120, maxHeight: 300)
        }
    }

    private func formatTokens(_ n: Int) -> String {
        if n < 1_000 { return "\(n)" }
        if n < 1_000_000 { return String(format: "%.1fk", Double(n) / 1_000) }
        return String(format: "%.2fM", Double(n) / 1_000_000)
    }
}

@MainActor
final class CostViewModel: ObservableObject {
    @Published var data: UsageSummaryResponse?
    @Published var isLoading = false
    @Published var error: String?
    @Published var fetchedAt: String?

    private let api = RuntimeAPI()

    func load() async {
        isLoading = true
        error = nil
        do {
            data = try await api.usageSummary()
            let fmt = DateFormatter()
            fmt.dateFormat = "HH:mm:ss"
            fetchedAt = fmt.string(from: Date())
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}
