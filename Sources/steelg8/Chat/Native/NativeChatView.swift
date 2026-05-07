import SwiftUI

// MARK: - NativeChatView

/// 原生 Chat 主视图。布局：左侧边栏 | 中间消息区 + 输入框 | 右侧 Canvas（可选）。
/// toolbar 用自定义 HStack（不用 SwiftUI .toolbar，避免 items 泄漏到 Settings 窗口）。
/// 窗口拖动靠 AppController.configureMainWindow 里的 isMovableByWindowBackground = true。
struct NativeChatView: View {
    @StateObject private var vm = ChatViewModel()
    @State private var inputText = ""
    @State private var composerHeight: CGFloat = ComposerView.minHeight
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(spacing: 0) {
            chatToolbar
            Divider()
            HStack(spacing: 0) {
                // 侧边栏
                if vm.sidebarVisible {
                    ChatSidebarView(vm: vm)
                    Divider()
                }

                // 主聊天区
                VStack(spacing: 0) {
                    ChatMessagesView(vm: vm)
                    composerBar
                }

                // Canvas 面板
                if vm.canvasVisible {
                    Divider()
                    ChatCanvasView(vm: vm)
                        .frame(minWidth: 380)
                }
            }
        }
        .frame(minWidth: 960, minHeight: 600)
        .onAppear {
            vm.canvasVisible = false
            vm.onAppear()
        }
        .onDisappear { vm.onDisappear() }
        .alert("发送出错", isPresented: .init(
            get: { vm.sendError != nil },
            set: { if !$0 { vm.sendError = nil } }
        )) {
            Button("好") { vm.sendError = nil }
        } message: {
            Text(vm.sendError ?? "")
        }
    }

    // MARK: - 自定义 Toolbar（内容区顶部 HStack，不用 SwiftUI .toolbar）

    private var chatToolbar: some View {
        HStack(spacing: 10) {
            // 侧边栏开关
            Button {
                withAnimation(.easeInOut(duration: 0.18)) {
                    vm.sidebarVisible.toggle()
                }
            } label: {
                Image(systemName: vm.sidebarVisible ? "sidebar.left" : "sidebar.squares.left")
                    .foregroundStyle(Color.secondary)
            }
            .buttonStyle(.plain)
            .help(vm.sidebarVisible ? "隐藏侧边栏" : "显示侧边栏")

            Divider().frame(height: 16)

            // 模型选择（按 provider 分组）
            Picker("", selection: $vm.selectedModel) {
                Text("默认模型").tag("")
                ForEach(groupedModels, id: \.provider) { group in
                    Section(providerLabel(group.provider)) {
                        ForEach(group.models, id: \.self) { m in
                            Text(shortModelName(m)).tag(m)
                        }
                    }
                }
            }
            .labelsHidden()
            .frame(maxWidth: 220)

            WindowDragArea()
                .frame(maxWidth: .infinity, minHeight: 28, maxHeight: 28)
                .contentShape(Rectangle())

            // 健康状态指示（8pt dot + glow ring）
            ZStack {
                let dotColor = vm.isHealthy ? SG.success(colorScheme) : SG.danger
                Circle().fill(dotColor.opacity(0.20)).frame(width: 14, height: 14)
                Circle().fill(dotColor).frame(width: 8, height: 8)
            }
            .help(vm.isHealthy ? "内核运行正常" : "内核未就绪")

            // Canvas 开关
            if vm.canvasVisible {
                Button("关闭 Canvas") {
                    vm.canvasVisible = false
                }
                .buttonStyle(.bordered)
            } else {
                Button {
                    vm.canvasVisible = true
                } label: {
                    Image(systemName: "rectangle.split.2x1")
                        .foregroundStyle(Color.secondary)
                }
                .buttonStyle(.plain)
                .help("打开 Canvas")
            }
        }
        .padding(.horizontal, 12)
        .frame(height: 40)
        .background(SG.chrome(colorScheme))
    }

    // MARK: - Composer

    private var composerBar: some View {
        let isEmpty = inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return HStack(alignment: .bottom, spacing: 8) {
            ComposerView(text: $inputText, height: $composerHeight) {
                sendMessage()
            }
            .frame(height: composerHeight)

            if vm.isSending {
                Button { vm.stopSending() } label: {
                    Image(systemName: "stop.fill")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 28, height: 28)
                        .background(SG.danger)
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .help("停止")
            } else {
                Button { sendMessage() } label: {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(isEmpty ? Color.secondary : Color.white)
                        .frame(width: 28, height: 28)
                        .background(isEmpty ? Color.secondary.opacity(0.15) : SG.sendBlue)
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .disabled(isEmpty)
                .help("发送 (⏎)")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(SG.surface(colorScheme))
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(SG.codeBorder(colorScheme), lineWidth: 1))
        )
        .padding(.horizontal, 48)
        .padding(.vertical, 10)
        .background(SG.bg(colorScheme))
    }

    private func sendMessage() {
        let text = inputText
        inputText = ""
        composerHeight = ComposerView.minHeight
        vm.send(text: text)
    }

    private struct ModelGroup { let provider: String; let models: [String] }

    private var groupedModels: [ModelGroup] {
        var result: [ModelGroup] = []
        var currentProvider = ""
        var currentModels: [String] = []
        for m in vm.availableModels {
            let provider = m.split(separator: "/", maxSplits: 1).first.map(String.init) ?? ""
            if provider != currentProvider {
                if !currentModels.isEmpty { result.append(ModelGroup(provider: currentProvider, models: currentModels)) }
                currentProvider = provider
                currentModels = []
            }
            currentModels.append(m)
        }
        if !currentModels.isEmpty { result.append(ModelGroup(provider: currentProvider, models: currentModels)) }
        return result
    }

    private func providerLabel(_ id: String) -> String {
        switch id {
        case "bailian":    return "百炼"
        case "deepseek":   return "DeepSeek"
        case "kimi":       return "Kimi"
        case "openrouter": return "OpenRouter"
        default:           return id
        }
    }

    // "provider/model" or "provider/org/model" → last path component
    private func shortModelName(_ fullId: String) -> String {
        let parts = fullId.split(separator: "/", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return fullId }
        return parts[1].split(separator: "/").last.map(String.init) ?? parts[1]
    }
}
