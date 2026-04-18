import Foundation

/// 把一条文本推到 Apple 备忘录。
///
/// 走 /usr/bin/osascript，用环境变量传 title/body/folder，避免 AppleScript
/// 里字符串转义炸掉（引号、换行、中文都安全）。
///
/// 首次调用会触发 macOS 的"自动化权限"弹窗——用户需要授权 steelg8 控制
/// 备忘录。Info.plist 里有 NSAppleEventsUsageDescription 说明原因。
///
/// 如果目标文件夹不存在就创建。默认目标文件夹是 "steelg8"。
enum NotesBridge {

    enum NotesError: LocalizedError {
        case automationNotPermitted
        case scriptFailed(String)
        case notesNotRunning

        var errorDescription: String? {
            switch self {
            case .automationNotPermitted:
                return "没有 Apple 备忘录的自动化权限。请去 系统设置 → 隐私与安全性 → 自动化 → steelg8 里勾上 Notes。"
            case let .scriptFailed(msg):
                return "AppleScript 执行失败：\(msg)"
            case .notesNotRunning:
                return "Apple 备忘录没运行，已尝试启动"
            }
        }
    }

    private static let script = """
    on run
        set t to system attribute "SG8_TITLE"
        set b to system attribute "SG8_BODY"
        set f to system attribute "SG8_FOLDER"
        tell application "Notes"
            if not running then launch
            try
                set tgt to folder f
            on error
                set tgt to make new folder with properties {name:f}
            end try
            make new note at tgt with properties {name:t, body:b}
        end tell
    end run
    """

    /// 异步执行；返回笔记创建结果。
    static func addNote(folder: String, title: String, body: String) async -> Result<Void, NotesError> {
        let folderName = folder.isEmpty ? "steelg8" : folder
        let titleText = title.isEmpty ? "steelg8 捕获" : title
        // body 里的换行要转成 AppleScript 认的 return / linefeed
        // 直接用 <br> 让 Notes 渲染成多段
        let htmlBody = body
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
            .replacingOccurrences(of: "\n", with: "<br>")
        // Notes.body 期望 HTML 富文本，首行加一个 <div><b>标题</b></div> 更直观
        let safeTitle = titleText
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
        let finalBody = "<div><b>\(safeTitle)</b></div><div>\(htmlBody)</div>"

        return await Task.detached(priority: .userInitiated) { () -> Result<Void, NotesError> in
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
            proc.arguments = ["-e", script]
            var env = ProcessInfo.processInfo.environment
            env["SG8_TITLE"] = titleText
            env["SG8_BODY"] = finalBody
            env["SG8_FOLDER"] = folderName
            proc.environment = env

            let errPipe = Pipe()
            proc.standardError = errPipe
            proc.standardOutput = Pipe()

            do {
                try proc.run()
                proc.waitUntilExit()
            } catch {
                return .failure(.scriptFailed(error.localizedDescription))
            }

            if proc.terminationStatus == 0 {
                return .success(())
            }
            let stderrData = errPipe.fileHandleForReading.readDataToEndOfFile()
            let stderrText = String(data: stderrData, encoding: .utf8) ?? ""
            if stderrText.contains("-1743") || stderrText.lowercased().contains("not allow") {
                return .failure(.automationNotPermitted)
            }
            return .failure(.scriptFailed(stderrText.trimmingCharacters(in: .whitespacesAndNewlines)))
        }.value
    }
}
