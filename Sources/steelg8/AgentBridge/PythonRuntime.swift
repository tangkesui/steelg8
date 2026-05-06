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
    var baseURL: URL {
        KernelConfig.baseURL
    }

    var soulFileURL: URL {
        KernelConfig.soulFileURL
    }

    private let fileManager = FileManager.default
    private var process: Process?
    private var stdoutPipe: Pipe?
    private var stderrPipe: Pipe?

    func startIfNeeded() async throws {
        if await isHealthy() {
            return
        }

        if let process, process.isRunning {
            try await waitUntilHealthy()
            return
        }

        let appRootURL = KernelConfig.appRootURL
        let scriptURL = KernelConfig.serverScriptURL

        guard fileManager.fileExists(atPath: scriptURL.path) else {
            throw PythonRuntimeError.serverScriptMissing(scriptURL.path)
        }

        // 优先用 .venv/bin/python3（装了 python-docx 等依赖）；
        // 没有就回退到系统 python3（只会跑 stdlib 的旧路径）
        let venvPython = KernelConfig.venvPythonURL
        let process = Process()
        if fileManager.fileExists(atPath: venvPython.path) {
            process.executableURL = venvPython
            process.arguments = [scriptURL.path, "--port", "\(KernelConfig.port)"]
        } else {
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = ["python3", scriptURL.path, "--port", "\(KernelConfig.port)"]
        }
        process.currentDirectoryURL = appRootURL
        process.environment = ProcessInfo.processInfo.environment.merging([
            "PYTHONUNBUFFERED": "1",
            "STEELG8_APP_ROOT": appRootURL.path,
            "STEELG8_PORT": "\(KernelConfig.port)",
            "STEELG8_AUTH_TOKEN": KernelConfig.authToken,
            "STEELG8_SOUL_PATH": soulFileURL.path
        ]) { _, newValue in
            newValue
        }
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        stdoutPipe.fileHandleForReading.readabilityHandler = makeReadabilityHandler(prefix: "python")
        stderrPipe.fileHandleForReading.readabilityHandler = makeReadabilityHandler(prefix: "python stderr")

        self.stdoutPipe = stdoutPipe
        self.stderrPipe = stderrPipe

        process.terminationHandler = { [weak self] process in
            Task { @MainActor in
                NSLog("steelg8: Python runtime exited with status \(process.terminationStatus)")
                self?.handleProcessTermination(process)
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
        clearPipeHandlers()

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

        let soulTemplateURL = KernelConfig.soulTemplateURL

        guard fileManager.fileExists(atPath: soulTemplateURL.path) else {
            throw PythonRuntimeError.soulTemplateMissing(soulTemplateURL.path)
        }

        let contents = try String(contentsOf: soulTemplateURL, encoding: .utf8)
        try contents.write(to: soulFileURL, atomically: true, encoding: .utf8)
    }

    private func waitUntilHealthy() async throws {
        let deadline = Date().addingTimeInterval(30)
        var delayMilliseconds = 150

        while Date() < deadline {
            if let process, !process.isRunning {
                throw PythonRuntimeError.failedToStart(
                    "进程提前退出，状态码 \(process.terminationStatus)"
                )
            }

            if await isHealthy() {
                return
            }

            try await Task.sleep(for: .milliseconds(delayMilliseconds))
            delayMilliseconds = min(Int(Double(delayMilliseconds) * 1.35), 1_000)
        }

        throw PythonRuntimeError.healthcheckTimedOut(baseURL.appending(path: "health"))
    }

    private func isHealthy() async -> Bool {
        var request = URLRequest(url: baseURL.appending(path: "health"))
        request.timeoutInterval = 1
        KernelConfig.authorize(&request)

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                return false
            }

            guard httpResponse.statusCode == 200,
                  let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else {
                return false
            }

            return payload["ok"] as? Bool == true
                && payload["authRequired"] as? Bool == true
                && payload["authenticated"] as? Bool == true
        } catch {
            return false
        }
    }

    private func handleProcessTermination(_ terminatedProcess: Process) {
        if process === terminatedProcess {
            process = nil
        }
        clearPipeHandlers()
    }

    private func clearPipeHandlers() {
        stdoutPipe?.fileHandleForReading.readabilityHandler = nil
        stderrPipe?.fileHandleForReading.readabilityHandler = nil
        stdoutPipe = nil
        stderrPipe = nil
    }

    private func makeReadabilityHandler(prefix: String) -> @Sendable (FileHandle) -> Void {
        { handle in
            let data = handle.availableData
            guard !data.isEmpty else {
                handle.readabilityHandler = nil
                return
            }

            guard let chunk = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines),
                !chunk.isEmpty
            else {
                return
            }

            NSLog("steelg8: \(prefix): \(chunk)")
        }
    }
}
