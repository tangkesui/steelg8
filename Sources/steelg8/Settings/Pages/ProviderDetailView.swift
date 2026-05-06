import AppKit
import SwiftUI

enum ProviderDetailField {
    case displayName
    case baseURL
    case apiKey
    case apiKeyEnv
}

struct ProviderDetailView: View {
    let entry: ProviderEntry
    let catalogModels: [CatalogModel]
    let selectedIDs: Set<String>
    @Binding var newModelText: String
    let isRefreshing: Bool
    let isSaving: Bool
    let statusMessage: String?
    let statusIsError: Bool
    let onSetField: (ProviderDetailField, String) -> Void
    let onToggleModel: (String, Bool) -> Void
    let onAddManualModel: () -> Void
    let onRefreshCatalog: () -> Void
    let onApplyRecommended: () -> Void
    let onUseEnv: () -> Void
    let onDelete: () -> Void
    let onSave: () -> Void
    let onReload: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                basicsSection
                if !isToolProvider {
                    modelsSection
                }
                if let preset = ProviderCatalog.preset(by: entry.name),
                   preset.kind == .localRuntime {
                    localRuntimeSection(preset)
                }
            }
            .padding(20)
        }
        .safeAreaInset(edge: .bottom) {
            footerBar
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(entry.displayName.isEmpty ? entry.name : entry.displayName)
                .font(.title3.bold())
            HStack(spacing: 8) {
                Text(entry.name)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(kindLabel)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let preset = ProviderCatalog.preset(by: entry.name) {
                Text(preset.blurb)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.top, 2)
            }
        }
    }

    private var basicsSection: some View {
        GroupBox("基础") {
            VStack(alignment: .leading, spacing: 12) {
                labeledRow("ID") {
                    Text(entry.name)
                        .font(.body.monospaced())
                        .foregroundStyle(.secondary)
                }
                labeledRow("显示名") {
                    TextField("Provider name", text: fieldBinding(.displayName, fallback: entry.displayName))
                        .textFieldStyle(.roundedBorder)
                }
                labeledRow("Base URL") {
                    TextField("https://api.example.com/v1", text: fieldBinding(.baseURL, fallback: entry.baseURL))
                        .textFieldStyle(.roundedBorder)
                }
                if !isLocalRuntime {
                    labeledRow("API Key") {
                        SecureField("sk-...", text: fieldBinding(.apiKey, fallback: entry.apiKey))
                            .textFieldStyle(.roundedBorder)
                    }
                    labeledRow("Env 变量名") {
                        HStack {
                            TextField("KIMI_API_KEY", text: fieldBinding(.apiKeyEnv, fallback: entry.apiKeyEnv))
                                .textFieldStyle(.roundedBorder)
                            Button("用外部环境变量", action: onUseEnv)
                                .disabled((ProviderCatalog.preset(by: entry.name)?.apiKeyEnv ?? "").isEmpty)
                        }
                    }
                }
            }
            .padding(.vertical, 8)
        }
    }

    private var modelsSection: some View {
        GroupBox("模型") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Button {
                        onRefreshCatalog()
                    } label: {
                        if isRefreshing {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Label("刷新 catalog", systemImage: "arrow.clockwise")
                        }
                    }
                    .disabled(isRefreshing)

                    Button("应用推荐清单", action: onApplyRecommended)
                        .disabled(RecommendedModelsClient.forProvider(entry.name).isEmpty)

                    Spacer()
                }

                if catalogModels.isEmpty {
                    Text("暂无模型。可以刷新 catalog，或手动添加一个 model id。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    VStack(spacing: 0) {
                        ForEach(catalogModels) { model in
                            modelRow(model)
                            Divider()
                        }
                    }
                }

                HStack {
                    TextField("手动输入 model id", text: $newModelText)
                        .textFieldStyle(.roundedBorder)
                    Button {
                        onAddManualModel()
                    } label: {
                        Label("添加", systemImage: "plus.circle")
                    }
                }
            }
            .padding(.vertical, 8)
        }
    }

    private func modelRow(_ model: CatalogModel) -> some View {
        HStack(spacing: 10) {
            Toggle(
                "",
                isOn: Binding(
                    get: { selectedIDs.contains(model.id) },
                    set: { onToggleModel(model.id, $0) }
                )
            )
            .labelsHidden()
            Text(model.id)
                .font(.body.monospaced())
                .lineLimit(1)
            Spacer()
            Text(pricingText(model.pricingPerMToken))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 7)
    }

    private func localRuntimeSection(_ preset: ProviderCatalog.Preset) -> some View {
        GroupBox("本地 Runtime") {
            VStack(alignment: .leading, spacing: 10) {
                Text(preset.commandSnippet ?? "")
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 6))

                HStack {
                    Button("复制") {
                        copyToPasteboard(preset.commandSnippet ?? "")
                    }
                    Button("打开 Terminal") {
                        NSWorkspace.shared.openApplication(
                            at: URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app"),
                            configuration: NSWorkspace.OpenConfiguration()
                        )
                    }
                    Spacer()
                }
            }
            .padding(.vertical, 8)
        }
    }

    private var footerBar: some View {
        HStack(spacing: 12) {
            Button(role: .destructive, action: onDelete) {
                Label("删除 provider", systemImage: "trash")
            }
            Spacer()
            if let statusMessage {
                Text(statusMessage)
                    .font(.caption)
                    .foregroundStyle(statusIsError ? .red : .secondary)
                    .lineLimit(2)
            }
            Button("还原", action: onReload)
            Button("保存并热加载", action: onSave)
                .buttonStyle(.borderedProminent)
                .disabled(isSaving)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.bar)
    }

    private func labeledRow<Content: View>(
        _ label: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 90, alignment: .trailing)
            content()
        }
    }

    private func fieldBinding(_ field: ProviderDetailField, fallback: String) -> Binding<String> {
        Binding(
            get: { fallback },
            set: { onSetField(field, $0) }
        )
    }

    private var isLocalRuntime: Bool {
        entry.kind == "local-runtime"
    }

    private var isToolProvider: Bool {
        entry.kind == "tool"
    }

    private var kindLabel: String {
        switch entry.kind {
        case "local-runtime": return "本地 Runtime"
        case "tool": return "工具 Provider"
        default: return "云端模型 Provider"
        }
    }

    private func pricingText(_ pricing: CatalogModel.Pricing?) -> String {
        guard let pricing else { return "未知" }
        let input = pricing.input.map { String(format: "$%.2f", $0) } ?? "未知"
        let output = pricing.output.map { String(format: "$%.2f", $0) } ?? "未知"
        return "\(input) / \(output) per Mtok"
    }

    private func copyToPasteboard(_ value: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
    }
}
