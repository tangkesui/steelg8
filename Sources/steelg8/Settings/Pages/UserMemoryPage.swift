import Foundation
import SwiftUI

/// 「User Memory（L2 画像）」编辑页。
struct UserMemoryPage: View {
    var body: some View {
        MarkdownEditorPage(
            title: "User Memory（L2 画像）",
            subtitle: "记你的偏好、口吻、长期事实。每次对话都会拼进 system prompt。",
            fileURL: Self.userMdURL,
            stubProvider: { Self.userMdStub() }
        )
    }

    static var userMdURL: URL {
        KernelConfig.userConfigDirectoryURL.appending(path: "user.md")
    }

    private static func userMdStub() -> String {
        """
        # steelg8 用户画像（L2）

        > 这个文件会在每次对话拼进 system prompt。随时手动编辑。

        ## 基本

        （空）

        ## 写作口吻与偏好

        （空）
        """
    }
}
