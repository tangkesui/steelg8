import SwiftUI

// MARK: - NativeChatView

/// 原生 Chat 主视图。布局：左侧边栏 | 中间消息区 + 输入框 | 右侧 Canvas（可选）。
/// toolbar 用自定义 HStack（不用 SwiftUI .toolbar，避免 items 泄漏到 Settings 窗口）。
/// 窗口拖动靠 AppController.configureMainWindow 里的 isMovableByWindowBackground = true。
struct NativeChatView: View {
    @StateObject private var vm = ChatViewModel()
    @State private var inputText = ""

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
                    Divider()
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

            // 模型选择
            Picker("", selection: $vm.selectedModel) {
                Text("默认模型").tag("")
                ForEach(vm.availableModels, id: \.self) { m in
                    Text(m).tag(m)
                }
            }
            .labelsHidden()
            .frame(maxWidth: 220)

            WindowDragArea()
                .frame(maxWidth: .infinity, minHeight: 28, maxHeight: 28)
                .contentShape(Rectangle())

            // 健康状态指示
            Circle()
                .fill(vm.isHealthy ? Color.green : Color.red)
                .frame(width: 8, height: 8)
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
        .padding(.vertical, 6)
        .background(Color(NSColor.windowBackgroundColor))
    }

    // MARK: - Composer

    private var composerBar: some View {
        HStack(alignment: .bottom, spacing: 8) {
            ComposerView(text: $inputText) {
                sendMessage()
            }
            .frame(minHeight: 60, maxHeight: 160)

            VStack(spacing: 6) {
                if vm.isSending {
                    Button {
                        vm.stopSending()
                    } label: {
                        Image(systemName: "stop.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.red)
                    .help("停止")
                } else {
                    Button {
                        sendMessage()
                    } label: {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.title2)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                     ? Color.secondary : Color.accentColor)
                    .disabled(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .help("发送 (⏎)")
                }
            }
            .padding(.bottom, 6)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color(NSColor.controlBackgroundColor))
    }

    private func sendMessage() {
        let text = inputText
        inputText = ""
        vm.send(text: text)
    }
}
