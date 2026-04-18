import AppKit
import Foundation

/// Swift 侧的"项目选择器"——走系统 NSOpenPanel 拿到文件夹路径，
/// 再 POST 到 Python kernel 的 /project/open。
@MainActor
enum ProjectPicker {

    struct PickError: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    /// 打开系统目录选择面板，让用户选一个文件夹。选完后通知 kernel 索引。
    static func pickAndOpen() async -> Result<OpenedProject, PickError> {
        let panel = NSOpenPanel()
        panel.title = "选一个项目文件夹"
        panel.message = "steelg8 会索引文件夹里的 .md / .txt 供对话引用。"
        panel.prompt = "打开"
        panel.allowsMultipleSelection = false
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = false

        NSApp.activate(ignoringOtherApps: true)
        let response = await panel.beginSheetSafe()
        guard response == .OK, let url = panel.url else {
            return .failure(PickError(message: "已取消"))
        }

        return await openProject(path: url.path)
    }

    /// 直接调后端打开某个路径（用于菜单命令之外的入口）。
    static func openProject(path: String) async -> Result<OpenedProject, PickError> {
        var req = URLRequest(url: URL(string: "http://127.0.0.1:8765/project/open")!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 10
        req.httpBody = try? JSONEncoder().encode(["path": path, "rebuild": "true"])
        // 直接构一个匿名 dict，JSONEncoder 处理 [String: String]
        // 注意 rebuild 前端用 bool，这里我们改成手动 JSON：
        let body: [String: Any] = ["path": path, "rebuild": true]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)

        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse else {
                return .failure(PickError(message: "无响应"))
            }
            if !(200..<300).contains(http.statusCode) {
                let msg = String(data: data, encoding: .utf8) ?? "\(http.statusCode)"
                return .failure(PickError(message: "后端拒绝：\(msg)"))
            }
            let decoded = try JSONDecoder().decode(OpenedProject.self, from: data)
            return .success(decoded)
        } catch {
            return .failure(PickError(message: "请求失败：\(error.localizedDescription)"))
        }
    }
}

struct OpenedProject: Codable {
    let id: Int
    let path: String
    let name: String
    let chunkCount: Int
}

private extension NSOpenPanel {
    /// `NSOpenPanel.begin(completionHandler:)` 包装成 async。
    func beginSheetSafe() async -> NSApplication.ModalResponse {
        await withCheckedContinuation { cont in
            self.begin { resp in
                cont.resume(returning: resp)
            }
        }
    }
}
