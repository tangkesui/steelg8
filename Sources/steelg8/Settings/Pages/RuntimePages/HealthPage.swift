import SwiftUI

// 12.7：体检页，对接 /diagnostics/doctor
struct HealthPage: View {
    @StateObject private var vm = HealthViewModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let err = vm.error {
                    Label(err, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.red)
                        .frame(maxWidth: .infinity)
                } else if let data = vm.data {
                    overallBadge(data.level)
                    Divider()
                    ForEach(data.checks) { check in
                        checkRow(check)
                    }
                    if !data.issues.isEmpty {
                        Divider()
                        Text("问题摘要").font(.headline).padding(.top, 4)
                        ForEach(data.issues) { issue in
                            HStack(alignment: .top, spacing: 8) {
                                levelIcon(issue.level)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(issue.check).font(.caption).foregroundStyle(.secondary)
                                    Text(issue.message).font(.body)
                                }
                            }
                        }
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

    private func overallBadge(_ level: String) -> some View {
        HStack(spacing: 8) {
            levelIcon(level)
            Text(levelText(level)).font(.headline)
            Spacer()
        }
        .padding(10)
        .background(levelColor(level).opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func checkRow(_ check: DiagCheck) -> some View {
        HStack(alignment: .top, spacing: 10) {
            levelIcon(check.level)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(localizedCheckName(check.name)).font(.headline)
                Text(check.message).font(.body).foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(.vertical, 4)
    }

    private func levelIcon(_ level: String) -> some View {
        Image(systemName: levelSymbol(level))
            .foregroundStyle(levelColor(level))
    }

    private func levelSymbol(_ level: String) -> String {
        switch level {
        case "ok": return "checkmark.circle.fill"
        case "warn": return "exclamationmark.triangle.fill"
        default: return "xmark.circle.fill"
        }
    }

    private func levelColor(_ level: String) -> Color {
        switch level {
        case "ok": return .green
        case "warn": return .orange
        default: return .red
        }
    }

    private func levelText(_ level: String) -> String {
        switch level {
        case "ok": return "一切正常"
        case "warn": return "有警告"
        default: return "发现错误"
        }
    }

    private func localizedCheckName(_ name: String) -> String {
        switch name {
        case "kernel": return "内核"
        case "providers": return "供应商"
        case "embedding": return "向量嵌入"
        case "documentDependencies": return "文档解析依赖"
        case "ragStore": return "RAG 数据库"
        case "activeProject": return "活跃项目"
        case "logs": return "日志"
        default: return name
        }
    }
}

@MainActor
final class HealthViewModel: ObservableObject {
    @Published var data: DoctorResponse?
    @Published var isLoading = false
    @Published var error: String?
    @Published var fetchedAt: String?

    private let api = RuntimeAPI()

    func load() async {
        isLoading = true
        error = nil
        do {
            data = try await api.diagnosticsDoctor()
            let fmt = DateFormatter()
            fmt.dateFormat = "HH:mm:ss"
            fetchedAt = fmt.string(from: Date())
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}
