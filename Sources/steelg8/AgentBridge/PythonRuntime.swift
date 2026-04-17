import Foundation

enum PythonRuntimeError: LocalizedError {
    case serverScriptMissing(String)
    case soulTemplateMissing(String)
    case failedToStart(String)
    case healthcheckTimedOut(URL)

    var errorDescription: String? {
        switch self {
        case let .serverScriptMissing(path):
            return "找不到 Python 服务脚本：\(path)"
        case let .soulTemplateMissing(path):
            return "找不到 soul 模板：\(path)"
        case let .failedToStart(reason):
            return "Python 内核启动失败：\(reason)"
        case let .healthcheckTimedOut(url):
            return "Python 内核没有按时响应健康检查：\(url.absoluteString)"
        }
    }
}

@MainActor
final class PythonRuntime {
    static let defaultPort = 8765

    let baseURL = URL(string: "http://127.0.0.1:\(defaultPort)")!

    var soulFileURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appending(path: ".steelg8")
            .appending(path: "soul.md")
    }

    private let fileManager = FileManager.default
    private var process: Process?
    private let stdoutPipe = Pipe()
    private let stderrPipe = Pipe()

    func startIfNeeded() async throws {
        if await isHealthy() {
            return
        }

        if let process, process.isRunning {
            try await waitUntilHealthy()
            return
        }

        let scriptURL = appRootURL
            .appending(path: "Python")
            .appending(path: "server.py")

        guard fileManager.fileExists(atPath: scriptURL.path) else {
            throw PythonRuntimeError.serverScriptMissing(scriptURL.path)
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3", scriptURL.path, "--port", "\(Self.defaultPort)"]
        process.currentDirectoryURL = appRootURL
        process.environment = ProcessInfo.processInfo.environment.merging([
            "PYTHONUNBUFFERED": "1",
            "STEELG8_APP_ROOT": appRootURL.path,
            "STEELG8_SOUL_PATH": soulFileURL.path
        ]) { _, newValue in
            newValue
        }
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        stdoutPipe.fileHandleForReading.readabilityHandler = makeReadabilityHandler(prefix: "python")
        stderrPipe.fileHandleForReading.readabilityHandler = makeReadabilityHandler(prefix: "python stderr")

        process.terminationHandler = { process in
            Task { @MainActor in
                NSLog("steelg8: Python runtime exited with status \(process.terminationStatus)")
            }
        }

        do {
            try process.run()
        } catch {
            throw PythonRuntimeError.failedToStart(error.localizedDescription)
        }

        self.process = process
        try await waitUntilHealthy()
    }

    func stop() {
        stdoutPipe.fileHandleForReading.readabilityHandler = nil
        stderrPipe.fileHandleForReading.readabilityHandler = nil

        guard let process else { return }
        if process.isRunning {
            process.terminate()
        }
        self.process = nil
    }

    func bootstrapSoulFileIfNeeded() throws {
        let configDirectory = soulFileURL.deletingLastPathComponent()
        try fileManager.createDirectory(
            at: configDirectory,
            withIntermediateDirectories: true,
            attributes: nil
        )

        if fileManager.fileExists(atPath: soulFileURL.path) {
            return
        }

        let soulTemplateURL = appRootURL
            .appending(path: "prompts")
            .appending(path: "soul.md")

        guard fileManager.fileExists(atPath: soulTemplateURL.path) else {
            throw PythonRuntimeError.soulTemplateMissing(soulTemplateURL.path)
        }

        let contents = try String(contentsOf: soulTemplateURL, encoding: .utf8)
        try contents.write(to: soulFileURL, atomically: true, encoding: .utf8)
    }

    private var appRootURL: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    private func waitUntilHealthy() async throws {
        for _ in 0..<20 {
            if await isHealthy() {
                return
            }

            try await Task.sleep(for: .milliseconds(250))
        }

        throw PythonRuntimeError.healthcheckTimedOut(baseURL.appending(path: "health"))
    }

    private func isHealthy() async -> Bool {
        var request = URLRequest(url: baseURL.appending(path: "health"))
        request.timeoutInterval = 1

        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                return false
            }

            return httpResponse.statusCode == 200
        } catch {
            return false
        }
    }

    private func makeReadabilityHandler(prefix: String) -> @Sendable (FileHandle) -> Void {
        { handle in
            let data = handle.availableData
            guard !data.isEmpty,
                  let chunk = String(data: data, encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines),
                  !chunk.isEmpty
            else {
                return
            }

            NSLog("steelg8: \(prefix): \(chunk)")
        }
    }
}
