import Foundation
import SwiftUI

/// 「Soul（L1 人格）」编辑页。
struct SoulPage: View {
    var body: some View {
        MarkdownEditorPage(
            title: "Soul（L1 人格）",
            subtitle: "每次对话都会拼进 system prompt 的人格底色。修改后下一条消息生效。",
            fileURL: KernelConfig.soulFileURL,
            stubProvider: { Self.soulFallbackTemplate() }
        )
    }

    /// 文件不存在且 bundled 模板也找不到时使用的最简降级模板。
    private static func soulFallbackTemplate() -> String {
        // 优先从 bundled prompts/soul.md 拷贝
        let template = KernelConfig.soulTemplateURL
        if let raw = try? String(contentsOf: template, encoding: .utf8) {
            return raw
        }
        return """
        # steelg8 soul（L1 人格）

        > 这个文件会在每次对话拼进 system prompt 的最前。

        我是 steelg8。简短直白，先给判断。

        """
    }
}
