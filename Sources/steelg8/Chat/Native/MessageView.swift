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
        .padding(.vertical, 4)
    }

    // MARK: - User

    private var userBubble: some View {
        VStack(alignment: .trailing, spacing: 2) {
            Text(message.content)
                .font(.system(size: SG.chatBody))
                .lineSpacing(SG.chatLineSpacing)
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
                    MarkdownView(markdown: message.content, isStreaming: message.isStreaming)
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
                    .font(.system(size: SG.chatMeta))
                    .foregroundStyle(.tertiary)
            }
            if meta.promptTokens > 0 || meta.completionTokens > 0 {
                Text("↑\(meta.promptTokens) ↓\(meta.completionTokens)")
                    .font(.system(size: SG.chatMeta).monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
            if meta.costUsd > 0 {
                Text(String(format: "¥%.4f", meta.costUsd * 7.25))
                    .font(.system(size: SG.chatMeta).monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
            if message.ragCount > 0 {
                Text("RAG \(message.ragCount)")
                    .font(.system(size: SG.chatMeta))
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

    private var isError: Bool {
        guard let r = tc.result else { return false }
        if let flag = r["is_error"] as? Bool, flag { return true }
        return r["error"] is String
    }

    private var resultDetail: String? {
        guard let r = tc.result else { return nil }
        if let err = r["error"] as? String { return err }
        if let text = r["text"] as? String, !text.isEmpty { return text }
        return nil
    }

    var body: some View {
        HStack(spacing: 6) {
            if tc.isRunning {
                ProgressView().scaleEffect(0.5).frame(width: 14, height: 14)
            } else if isError {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: SG.chatTool))
                    .foregroundStyle(SG.danger)
            } else {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: SG.chatTool))
                    .foregroundStyle(SG.success(colorScheme))
            }
            Text(tc.name)
                .font(.system(size: SG.chatTool))
                .foregroundStyle(isError ? .primary : .secondary)
            if let detail = resultDetail {
                Text("→ \(detail.prefix(80))")
                    .font(.system(size: SG.chatTool))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(isError ? SG.danger.opacity(0.08) : SG.pillBg(colorScheme))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}
