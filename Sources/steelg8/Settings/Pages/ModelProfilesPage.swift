import SwiftUI

// 12.6：只读展示 model_catalog.json 里的定价数据（供应商 / 模型 ID / 输入输出单价）
struct ModelProfilesPage: View {
    @StateObject private var vm = ModelProfilesViewModel()

    var body: some View {
        Group {
            if vm.isLoading {
                ProgressView("加载中…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if vm.rows.isEmpty {
                ContentUnavailableView(
                    "暂无定价数据",
                    systemImage: "tablecells",
                    description: Text("在「供应商」页刷新 catalog 后将在此展示定价信息。")
                )
            } else {
                Table(vm.rows) {
                    TableColumn("供应商") { row in
                        Text(row.providerID)
                            .font(.system(.body, design: .monospaced))
                    }
                    .width(min: 80, ideal: 110)

                    TableColumn("模型 ID") { row in
                        Text(row.modelID)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                    }
                    .width(min: 180, ideal: 320)

                    TableColumn("输入 $/MTok") { row in
                        Text(row.inputText)
                            .font(.system(.body, design: .monospaced))
                            .foregroundStyle(row.inputPerMToken != nil ? .primary : .secondary)
                    }
                    .width(min: 100, ideal: 120)

                    TableColumn("输出 $/MTok") { row in
                        Text(row.outputText)
                            .font(.system(.body, design: .monospaced))
                            .foregroundStyle(row.outputPerMToken != nil ? .primary : .secondary)
                    }
                    .width(min: 100, ideal: 120)

                    TableColumn("已选") { row in
                        Image(systemName: row.selected ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(row.selected ? Color.accentColor : .secondary)
                    }
                    .width(44)
                }
            }
        }
        .onAppear { vm.load() }
        .toolbar {
            ToolbarItem(placement: .automatic) {
                Button { vm.load() } label: {
                    Label("刷新", systemImage: "arrow.clockwise")
                }
                .disabled(vm.isLoading)
            }
        }
    }
}

struct ModelProfileRow: Identifiable {
    let id: String
    let providerID: String
    let modelID: String
    let selected: Bool
    let inputPerMToken: Double?
    let outputPerMToken: Double?

    var inputText: String {
        inputPerMToken.map { String(format: "%.4f", $0) } ?? "—"
    }
    var outputText: String {
        outputPerMToken.map { String(format: "%.4f", $0) } ?? "—"
    }
}

@MainActor
final class ModelProfilesViewModel: ObservableObject {
    @Published var rows: [ModelProfileRow] = []
    @Published var isLoading = false

    func load() {
        isLoading = true
        defer { isLoading = false }

        let url = KernelConfig.userConfigDirectoryURL.appending(path: "model_catalog.json")
        guard
            FileManager.default.fileExists(atPath: url.path),
            let data = try? Data(contentsOf: url),
            let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let providersRaw = raw["providers"] as? [String: Any]
        else {
            rows = []
            return
        }

        var result: [ModelProfileRow] = []
        for (providerID, providerRaw) in providersRaw.sorted(by: { $0.key < $1.key }) {
            guard
                let providerDict = providerRaw as? [String: Any],
                let modelsRaw = providerDict["models"] as? [[String: Any]]
            else { continue }
            for item in modelsRaw {
                guard let modelID = item["id"] as? String else { continue }
                let selected = item["selected"] as? Bool ?? true
                var inputPerMToken: Double? = nil
                var outputPerMToken: Double? = nil
                if let pricing = item["pricing_per_mtoken"] as? [String: Any] {
                    inputPerMToken = pricing["input"] as? Double
                    outputPerMToken = pricing["output"] as? Double
                }
                result.append(ModelProfileRow(
                    id: "\(providerID):\(modelID)",
                    providerID: providerID,
                    modelID: modelID,
                    selected: selected,
                    inputPerMToken: inputPerMToken,
                    outputPerMToken: outputPerMToken
                ))
            }
        }
        rows = result
    }
}
