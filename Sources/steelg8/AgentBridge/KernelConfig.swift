import Darwin
import Foundation

/// Single source of truth for the local Python kernel.
///
/// Keep all runtime paths and localhost URLs here so the app behaves the same
/// in development (`swift run`) and after `bundle.sh` copies resources into
/// `steelg8.app/Contents/Resources`.
enum KernelConfig {
    static let defaultPort = 8765

    static let authToken: String = {
        if let raw = ProcessInfo.processInfo.environment["STEELG8_AUTH_TOKEN"],
           !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return raw
        }
        return "\(UUID().uuidString)-\(UUID().uuidString)"
    }()

    static let port: Int = {
        guard
            let raw = ProcessInfo.processInfo.environment["STEELG8_PORT"],
            let parsed = Int(raw),
            (1...65_535).contains(parsed)
        else {
            return findAvailableLoopbackPort() ?? defaultPort
        }
        return parsed
    }()

    static var baseURL: URL {
        URL(string: "http://127.0.0.1:\(port)")!
    }

    static func url(path: String) -> URL {
        // 拆 path?query：appending(path:) 会把 ? 当成路径字符 percent-encode，
        // 那样 kernel 看到的就是 /logs%3Flimit=...，路由表 miss 返回 404。
        let trimmed = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let parts = trimmed.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
        let pathOnly = String(parts[0])
        let result = baseURL.appending(path: pathOnly)
        if parts.count == 2 {
            var components = URLComponents(url: result, resolvingAgainstBaseURL: false)!
            components.percentEncodedQuery = String(parts[1])
            return components.url!
        }
        return result
    }

    static func authorize(_ request: inout URLRequest) {
        request.setValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization")
    }

    static var userConfigDirectoryURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appending(path: ".steelg8")
    }

    static var soulFileURL: URL {
        userConfigDirectoryURL.appending(path: "soul.md")
    }

    static var appRootURL: URL {
        if let bundledRoot = bundledResourcesRootURL,
           FileManager.default.fileExists(atPath: bundledRoot.appending(path: "Python/server.py").path) {
            return bundledRoot
        }
        return developmentRootURL
    }

    static var serverScriptURL: URL {
        appRootURL.appending(path: "Python/server.py")
    }

    static var venvPythonURL: URL {
        appRootURL.appending(path: ".venv/bin/python3")
    }

    static var soulTemplateURL: URL {
        appRootURL.appending(path: "prompts/soul.md")
    }

    static var webIndexURL: URL? {
        existingResource("Web/chat/index.html")
    }

    static func existingResource(_ relativePath: String) -> URL? {
        for candidate in resourceCandidates(for: relativePath) {
            if FileManager.default.fileExists(atPath: candidate.path) {
                return candidate
            }
        }
        return nil
    }

    static func resourceCandidates(for relativePath: String) -> [URL] {
        let cleaned = relativePath.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        var out: [URL] = []
        if let bundledResourcesRootURL {
            out.append(bundledResourcesRootURL.appending(path: cleaned))
        }
        out.append(developmentRootURL.appending(path: cleaned))

        var seen = Set<String>()
        return out.filter { url in
            let path = url.standardizedFileURL.path
            if seen.contains(path) {
                return false
            }
            seen.insert(path)
            return true
        }
    }

    static var webBootstrapScript: String {
        """
        window.STEELG8_KERNEL = Object.freeze({
          port: \(port),
          baseURL: \(jsonStringLiteral(baseURL.absoluteString)),
          authToken: \(jsonStringLiteral(authToken))
        });
        """
    }

    private static func jsonStringLiteral(_ value: String) -> String {
        guard
            let data = try? JSONEncoder().encode(value),
            let encoded = String(data: data, encoding: .utf8)
        else {
            return "\"\""
        }
        return encoded
    }

    private static var bundledResourcesRootURL: URL? {
        Bundle.main.resourceURL?.standardizedFileURL
    }

    private static var developmentRootURL: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // AgentBridge/
            .deletingLastPathComponent()   // steelg8/
            .deletingLastPathComponent()   // Sources/
            .deletingLastPathComponent()   // repo root
            .standardizedFileURL
    }

    private static func findAvailableLoopbackPort() -> Int? {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }

        var reuse: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))

        var address = sockaddr_in(
            sin_len: UInt8(MemoryLayout<sockaddr_in>.size),
            sin_family: sa_family_t(AF_INET),
            sin_port: 0,
            sin_addr: in_addr(s_addr: inet_addr("127.0.0.1")),
            sin_zero: (0, 0, 0, 0, 0, 0, 0, 0)
        )

        let bindResult = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPointer in
                bind(fd, sockPointer, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else { return nil }

        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPointer in
                getsockname(fd, sockPointer, &length)
            }
        }
        guard nameResult == 0 else { return nil }

        let port = Int(UInt16(bigEndian: address.sin_port))
        return (1...65_535).contains(port) ? port : nil
    }
}
