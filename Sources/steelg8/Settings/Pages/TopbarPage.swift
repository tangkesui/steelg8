import SwiftUI

// 12.8：顶栏显示设置
// Track B 完成前，此页保存偏好供将来原生顶栏读取；
// debug 抽屉与 usage-pill 已随 Phase 12.7/12.8 迁移至原生设置页。
struct TopbarPage: View {
    @State private var showCost = true
    @State private var isDirty = false
    @State private var isSaving = false
    @State private var statusMsg: String?
    @State private var statusIsError = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                GroupBox("顶栏元素（Track B 原生顶栏生效后激活）") {
                    VStack(alignment: .leading, spacing: 10) {
                        Toggle("显示费用统计", isOn: $showCost)
                            .onChange(of: showCost) { _, _ in isDirty = true }
                        Text("费用 pill 显示本次会话 / 今日累计。若顶栏空间紧张可关闭，在「运行状态 → 费用」页查看详情。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(6)
                }

                GroupBox("已迁移项目") {
                    VStack(alignment: .leading, spacing: 6) {
                        migratedItem("体检（Doctor）", "设置 → 运行状态 → 体检")
                        migratedItem("索引状态", "设置 → 运行状态 → 索引")
                        migratedItem("RAG 调试", "设置 → 运行状态 → RAG")
                        migratedItem("日志", "设置 → 运行状态 → 日志")
                        migratedItem("费用统计", "设置 → 运行状态 → 费用")
                    }
                    .padding(6)
                }
            }
            .padding(20)
        }
        .safeAreaInset(edge: .bottom) {
            footerBar
        }
        .onAppear { loadPrefs() }
    }

    private var footerBar: some View {
        HStack {
            if let msg = statusMsg {
                Text(msg)
                    .font(.caption)
                    .foregroundStyle(statusIsError ? .red : .green)
            }
            Spacer()
            Button("保存") { savePrefs() }
                .disabled(!isDirty || isSaving)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private func migratedItem(_ old: String, _ newPath: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).font(.caption)
            VStack(alignment: .leading, spacing: 1) {
                Text(old).font(.body)
                Text("→ " + newPath).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func loadPrefs() {
        let raw = loadRaw()
        showCost = raw["show_cost_in_topbar"] as? Bool ?? true
        isDirty = false
    }

    private func savePrefs() {
        isSaving = true
        var raw = loadRaw()
        raw["show_cost_in_topbar"] = showCost
        let url = KernelConfig.userConfigDirectoryURL.appending(path: "preferences.json")
        do {
            try FileManager.default.createDirectory(
                at: KernelConfig.userConfigDirectoryURL,
                withIntermediateDirectories: true
            )
            let data = try JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: url, options: [.atomic])
            statusMsg = "已保存"
            statusIsError = false
            isDirty = false
        } catch {
            statusMsg = error.localizedDescription
            statusIsError = true
        }
        isSaving = false
    }

    private func loadRaw() -> [String: Any] {
        let url = KernelConfig.userConfigDirectoryURL.appending(path: "preferences.json")
        guard
            FileManager.default.fileExists(atPath: url.path),
            let data = try? Data(contentsOf: url),
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [:] }
        return obj
    }
}
