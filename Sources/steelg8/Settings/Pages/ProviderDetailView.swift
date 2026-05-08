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
    @Binding var newModelText: String
    let isRefreshing: Bool
    let isSaving: Bool
    let statusMessage: String?
    let statusIsError: Bool
    let onSetField: (ProviderDetailField, String) -> Void
    let onAddManualModel: () -> Void
    let onRefreshCatalog: () -> Void
    let onUseEnv: () -> Void
    let onDelete: () -> Void
    let onSave: () -> Void
    let onReload: () -> Void

    var body: some View {
        VStack(spacing: 0) {
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
            Divider()
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
        GroupBox("模型 catalog（共 \(catalogModels.count) 个）") {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Button {
                        onRefreshCatalog()
                    } label: {
                        if isRefreshing {
                            ProgressView().controlSize(.small)
                        } else {
                            Label("刷新模型清单", systemImage: "arrow.clockwise")
                        }
                    }
                    .disabled(isRefreshing)
                    Spacer()
                }

                if catalogModels.isEmpty {
                    Text("还没有 catalog——点击上方「刷新模型清单」从上游 /v1/models 拉取。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, minHeight: 60, alignment: .center)
                        .padding(.vertical, 12)
                        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 6))
                } else {
                    catalogList
                }

                Divider()

                HStack {
                    TextField("手动输入 model id", text: $newModelText)
                        .textFieldStyle(.roundedBorder)
                    Button {
                        onAddManualModel()
                    } label: {
                        Label("添加到 catalog", systemImage: "plus.circle")
                    }
                }
            }
            .padding(.vertical, 8)
        }
    }

    private var catalogList: some View {
        LazyVStack(alignment: .leading, spacing: 4) {
            ForEach(catalogModels.sorted(by: { $0.id < $1.id }), id: \.id) { model in
                HStack(spacing: 8) {
                    Image(systemName: model.selected ? "checkmark.circle.fill" : "circle")
                        .font(.system(size: 11))
                        .foregroundStyle(model.selected ? .green : .secondary.opacity(0.5))
                        .help(model.selected ? "已勾选（在「模型管理」中）" : "未勾选")
                    Text(model.id)
                        .font(.system(size: 12, design: .monospaced))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer(minLength: 8)
                    if let pricing = model.pricingPerMToken,
                       let inputPrice = pricing.input {
                        Text(String(format: "$%.3f / Mtok", inputPrice))
                            .font(.system(size: 10.5, design: .monospaced))
                            .foregroundStyle(.tertiary)
                    }
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(.quaternary.opacity(0.15), in: RoundedRectangle(cornerRadius: 4))
            }
        }
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
            Button("默认", action: onReload)
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

    private func copyToPasteboard(_ value: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
    }
}
