import SwiftUI

// MARK: - MessageView

struct MessageView: View {
    let message: ChatMessage
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if message.role == .user {
                Spacer(minLength: 60)
                userBubble
            } else {
                assistantContent
                Spacer(minLength: 60)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 4)
    }

    // MARK: - User

    private var userBubble: some View {
        VStack(alignment: .trailing, spacing: 2) {
            Text(message.content)
                .textSelection(.enabled)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(SG.userBubble(colorScheme))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .frame(maxWidth: 520, alignment: .trailing)

            if message.isCompressed {
                Text("已压缩")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    // MARK: - Assistant

    private var assistantContent: some View {
        VStack(alignment: .leading, spacing: 6) {
            // 工具调用列表
            if !message.toolCalls.isEmpty {
                toolCallsView
            }

            // 正文
            if !message.content.isEmpty || message.isStreaming {
                VStack(alignment: .leading, spacing: 0) {
                    MarkdownView(markdown: message.content)
                        .textSelection(.enabled)

                    if message.isStreaming && message.content.isEmpty {
                        ProgressView()
                            .scaleEffect(0.6)
                            .padding(.vertical, 4)
                    }
                }
            }

            // 元数据 footer
            if let meta = message.meta {
                metaFooter(meta)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var toolCallsView: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(message.toolCalls) { tc in
                ToolCallRow(tc: tc)
            }
        }
    }

    private func metaFooter(_ meta: MessageMeta) -> some View {
        HStack(spacing: 8) {
            if !meta.model.isEmpty {
                Text(meta.model)
                    .font(.system(size: 10.5))
                    .foregroundStyle(.tertiary)
            }
            if meta.promptTokens > 0 || meta.completionTokens > 0 {
                Text("↑\(meta.promptTokens) ↓\(meta.completionTokens)")
                    .font(.system(size: 10.5).monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
            if meta.costUsd > 0 {
                Text(String(format: "¥%.4f", meta.costUsd * 7.25))
                    .font(.system(size: 10.5).monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
            if message.ragCount > 0 {
                Text("RAG \(message.ragCount)")
                    .font(.system(size: 10.5))
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.top, 10)
    }
}

// MARK: - ToolCallRow

private struct ToolCallRow: View {
    let tc: ToolCallInfo
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(spacing: 6) {
            if tc.isRunning {
                ProgressView().scaleEffect(0.5).frame(width: 14, height: 14)
            } else {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 11.5))
                    .foregroundStyle(SG.success(colorScheme))
            }
            Text(tc.name)
                .font(.system(size: 11.5))
                .foregroundStyle(.secondary)
            if let result = tc.result,
               let text = result["text"] as? String, !text.isEmpty {
                Text("→ \(text.prefix(80))")
                    .font(.system(size: 11.5))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(SG.pillBg(colorScheme))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}
