import Foundation

struct AppPreferences {
    var compressionTriggerRatio: Double
    var logLevel: String
}

enum AppLogLevel: String, CaseIterable, Identifiable {
    case debug
    case info
    case warn
    case error

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .debug: return "Debug（最详细，调试用）"
        case .info: return "Info（默认）"
        case .warn: return "Warn（只看警告以上）"
        case .error: return "Error（只看错误）"
        }
    }
}

enum AppPreferencesStoreError: LocalizedError {
    case cannotCreateDirectory(String)
    case cannotReadFile(String)
    case cannotWriteFile(String)

    var errorDescription: String? {
        switch self {
        case let .cannotCreateDirectory(msg):
            return "无法创建 ~/.steelg8 目录：\(msg)"
        case let .cannotReadFile(msg):
            return "无法读取 preferences.json：\(msg)"
        case let .cannotWriteFile(msg):
            return "无法写入 preferences.json：\(msg)"
        }
    }
}

@MainActor
final class AppPreferencesStore {
    static let shared = AppPreferencesStore()
    static let defaultCompressionTriggerRatio = 0.60
    static let defaultLogLevel = AppLogLevel.info

    private let fileManager = FileManager.default

    var preferencesFileURL: URL {
        KernelConfig.userConfigDirectoryURL.appending(path: "preferences.json")
    }

    func loadOrDefaults() -> AppPreferences {
        (try? load()) ?? AppPreferences(
            compressionTriggerRatio: Self.defaultCompressionTriggerRatio,
            logLevel: Self.defaultLogLevel.rawValue
        )
    }

    func load() throws -> AppPreferences {
        let raw = try loadRaw()
        return AppPreferences(
            compressionTriggerRatio: normalizedCompressionRatio(
                raw["compression_trigger_ratio"]
            ),
            logLevel: normalizedLogLevel(raw["log_level"])
        )
    }

    @discardableResult
    func save(compressionTriggerRatio: Double, logLevel: String) throws -> AppPreferences {
        try ensureDirectoryExists()

        var raw = try loadRaw()
        let normalizedRatio = normalizedCompressionRatio(compressionTriggerRatio)
        let normalizedLevel = normalizedLogLevel(logLevel)
        raw["compression_trigger_ratio"] = normalizedRatio
        raw["log_level"] = normalizedLevel

        let data: Data
        do {
            data = try JSONSerialization.data(
                withJSONObject: raw,
                options: [.prettyPrinted, .sortedKeys]
            )
        } catch {
            throw AppPreferencesStoreError.cannotWriteFile(error.localizedDescription)
        }

        do {
            try data.write(to: preferencesFileURL, options: [.atomic])
        } catch {
            throw AppPreferencesStoreError.cannotWriteFile(error.localizedDescription)
        }

        try? fileManager.setAttributes(
            [.posixPermissions: NSNumber(value: Int16(0o600))],
            ofItemAtPath: preferencesFileURL.path
        )

        return AppPreferences(
            compressionTriggerRatio: normalizedRatio,
            logLevel: normalizedLevel
        )
    }

    private func ensureDirectoryExists() throws {
        let dir = KernelConfig.userConfigDirectoryURL
        do {
            try fileManager.createDirectory(
                at: dir,
                withIntermediateDirectories: true
            )
        } catch {
            throw AppPreferencesStoreError.cannotCreateDirectory(error.localizedDescription)
        }
    }

    private func loadRaw() throws -> [String: Any] {
        let url = preferencesFileURL
        guard fileManager.fileExists(atPath: url.path) else {
            return [:]
        }

        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            throw AppPreferencesStoreError.cannotReadFile(error.localizedDescription)
        }

        do {
            let obj = try JSONSerialization.jsonObject(with: data)
            return obj as? [String: Any] ?? [:]
        } catch {
            throw AppPreferencesStoreError.cannotReadFile(error.localizedDescription)
        }
    }

    private func normalizedLogLevel(_ value: Any?) -> String {
        let valid = AppLogLevel.allCases.map(\.rawValue)
        if let raw = value as? String {
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            if valid.contains(trimmed) { return trimmed }
        }
        return Self.defaultLogLevel.rawValue
    }

    private func normalizedCompressionRatio(_ value: Any?) -> Double {
        let parsed: Double?
        if let value = value as? Double {
            parsed = value
        } else if let value = value as? Int {
            parsed = Double(value)
        } else if let value = value as? String {
            parsed = Double(value.trimmingCharacters(in: .whitespacesAndNewlines))
        } else {
            parsed = nil
        }

        var ratio = parsed ?? Self.defaultCompressionTriggerRatio
        if ratio > 1 {
            ratio = ratio / 100
        }
        return min(0.90, max(0.50, ratio))
    }
}
