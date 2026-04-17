import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var appController: AppController

    var body: some View {
        TabView {
            WorkbenchView()
                .tabItem {
                    Label("工作台", systemImage: "sparkles.rectangle.stack")
                }

            OCRView()
                .tabItem {
                    Label("OCR", systemImage: "doc.text.viewfinder")
                }
        }
        .frame(minWidth: 960, minHeight: 640)
    }
}

private struct WorkbenchView: View {
    @EnvironmentObject private var appController: AppController

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("steelg8")
                        .font(.system(size: 32, weight: .bold, design: .rounded))
                    Text("Phase 0 骨架已经起好：菜单栏、OCR、Python 本地内核、soul 提示词入口都在这儿。")
                        .foregroundStyle(.secondary)
                }

                HStack(alignment: .top, spacing: 16) {
                    GroupBox("运行状态") {
                        VStack(alignment: .leading, spacing: 10) {
                            StatusRow(label: "本地内核", value: appController.runtimeStatus)
                            StatusRow(label: "当前模型", value: appController.activeModel)
                            StatusRow(label: "soul 文件", value: appController.soulFilePath)

                            if let errorMessage = appController.lastErrorMessage {
                                Text(errorMessage)
                                    .font(.footnote)
                                    .foregroundStyle(.red)
                            }

                            HStack {
                                Button("测试本地 Agent") {
                                    Task {
                                        await appController.sendHelloToAgent()
                                    }
                                }
                                .buttonStyle(.borderedProminent)
                                .disabled(appController.isAgentBusy)

                                Button("打开 soul.md") {
                                    appController.openSoulFile()
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    GroupBox("热键底座") {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(HotkeyRegistry.items) { item in
                                HStack {
                                    Text(item.shortcut)
                                        .font(.system(.body, design: .monospaced))
                                    Text(item.title)
                                    Spacer()
                                    Text(item.status)
                                        .foregroundStyle(item.isImplemented ? Color.green : Color.secondary)
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }

                GroupBox("最近一次 Agent 回复") {
                    VStack(alignment: .leading, spacing: 12) {
                        if appController.lastAgentResponse.isEmpty {
                            Text("还没有调用过本地 Agent。点击上面的“测试本地 Agent”，就能验证 Swift -> Python -> /chat 的链路。")
                                .foregroundStyle(.secondary)
                        } else {
                            Text(appController.lastAgentResponse)
                                .textSelection(.enabled)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                GroupBox("下一步") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("1. 接入真 provider：复制 config/providers.example.json 到 ~/.steelg8/providers.json，然后在 shell 里 export KIMI_API_KEY / DEEPSEEK_API_KEY / QWEN_API_KEY / OPENROUTER_API_KEY 任意一家。")
                        Text("2. 启动后通过 GET /providers 查看就绪状态，对话时传 model 字段路由到对应 provider。")
                        Text("3. Phase 1：用 WKWebView 接入 Chat / Scratch / Canvas。")
                        Text("4. Phase 2：接项目记忆 + Office docx/xlsx/pptx 模板填充。")
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(24)
        }
    }
}

private struct StatusRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .top) {
            Text(label)
                .foregroundStyle(.secondary)
                .frame(width: 72, alignment: .leading)
            Text(value)
                .textSelection(.enabled)
        }
    }
}
