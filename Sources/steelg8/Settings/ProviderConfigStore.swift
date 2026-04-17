import Foundation

/// 对应 ~/.steelg8/providers.json 的一条 provider 配置。
/// 与 Python 端 `providers.py` 的 JSON schema 对齐：
/// - api_key 为空时，Python 端会回退去读 api_key_env
/// - 两者都为空则 provider 视为未就绪
/// 给 SettingsView 用的视图模型。不参与 JSON 编解码（JSON 层用 ProviderConfigPayload）。
struct ProviderEntry: Identifiable, Equatable {
    var name: String          // "kimi" / "deepseek" / "qwen" / "openrouter" …
    var baseURL: String
    var apiKeyEnv: String
    var apiKey: String        // Settings UI 直接写入的明文 key（可空）
    var models: [String]

    var id: String { name }

    /// 本地判断是否可用，决策仅供 UI 显示；Python 端有自己的权威判断。
    var isConfigured: Bool {
        !baseURL.isEmpty && !apiKey.isEmpty
    }
}

/// 顶层配置结构。
struct ProviderConfigPayload: Codable {
    var defaultModel: String
    var providers: [String: ProviderValue]

    struct ProviderValue: Codable {
        var baseURL: String
        var apiKeyEnv: String
        var apiKey: String
        var models: [String]

        enum CodingKeys: String, CodingKey {
            case baseURL = "base_url"
            case apiKeyEnv = "api_key_env"
            case apiKey = "api_key"
            case models
        }
    }

    enum CodingKeys: String, CodingKey {
        case defaultModel = "default_model"
        case providers
    }
}

enum ProviderConfigStoreError: LocalizedError {
    case cannotCreateDirectory(String)
    case cannotWriteFile(String)
    case cannotReadFile(String)

    var errorDescription: String? {
        switch self {
        case let .cannotCreateDirectory(msg): return "无法创建 ~/.steelg8 目录：\(msg)"
        case let .cannotWriteFile(msg): return "无法写入 providers.json：\(msg)"
        case let .cannotReadFile(msg): return "无法读取 providers.json：\(msg)"
        }
    }
}

/// 负责 ~/.steelg8/providers.json 的 CRUD。
/// 所有写操作都会把文件权限夹成 0600（-rw-------）。
@MainActor
final class ProviderConfigStore {

    static let shared = ProviderConfigStore()

    private let fileManager = FileManager.default

    var configDirectoryURL: URL {
        fileManager.homeDirectoryForCurrentUser.appending(path: ".steelg8")
    }

    var configFileURL: URL {
        configDirectoryURL.appending(path: "providers.json")
    }

    var exampleTemplateURL: URL {
        appRootURL
            .appending(path: "config")
            .appending(path: "providers.example.json")
    }

    // MARK: - 读

    /// 读取现有配置；若文件不存在，则以 example 模板为起点返回一份可编辑副本。
    func load() throws -> (entries: [ProviderEntry], defaultModel: String) {
        let payload = try loadPayload()
        let entries = payload.providers
            .sorted { $0.key < $1.key }
            .map { (name, value) in
                ProviderEntry(
                    name: name,
                    baseURL: value.baseURL,
                    apiKeyEnv: value.apiKeyEnv,
                    apiKey: value.apiKey,
                    models: value.models
                )
            }
        return (entries, payload.defaultModel)
    }

    // MARK: - 写

    /// 将 Settings UI 的 entries + defaultModel 写回到 ~/.steelg8/providers.json，
    /// 同时把权限修到 0600。
    func save(entries: [ProviderEntry], defaultModel: String) throws {
        try ensureDirectoryExists()

        var providers: [String: ProviderConfigPayload.ProviderValue] = [:]
        for entry in entries {
            providers[entry.name] = .init(
                baseURL: entry.baseURL,
                apiKeyEnv: entry.apiKeyEnv,
                apiKey: entry.apiKey,
                models: entry.models
            )
        }

        let payload = ProviderConfigPayload(
            defaultModel: defaultModel,
            providers: providers
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data: Data
        do {
            data = try encoder.encode(payload)
        } catch {
            throw ProviderConfigStoreError.cannotWriteFile(error.localizedDescription)
        }

        do {
            try data.write(to: configFileURL, options: [.atomic])
        } catch {
            throw ProviderConfigStoreError.cannotWriteFile(error.localizedDescription)
        }

        // 0600：只有文件所有者可读可写
        try? fileManager.setAttributes(
            [.posixPermissions: NSNumber(value: Int16(0o600))],
            ofItemAtPath: configFileURL.path
        )
    }

    // MARK: - 内部

    private func loadPayload() throws -> ProviderConfigPayload {
        let decoder = JSONDecoder()

        if fileManager.fileExists(atPath: configFileURL.path) {
            do {
                let data = try Data(contentsOf: configFileURL)
                return try decoder.decode(ProviderConfigPayload.self, from: data)
            } catch {
                throw ProviderConfigStoreError.cannotReadFile(error.localizedDescription)
            }
        }

        // 回退到仓库自带的 example 模板；失败则给一个空配置
        if fileManager.fileExists(atPath: exampleTemplateURL.path) {
            if let data = try? Data(contentsOf: exampleTemplateURL),
               let payload = try? decoder.decode(ProviderConfigPayload.self, from: data) {
                return payload
            }
        }

        return ProviderConfigPayload(defaultModel: "", providers: [:])
    }

    private func ensureDirectoryExists() throws {
        if fileManager.fileExists(atPath: configDirectoryURL.path) {
            return
        }
        do {
            try fileManager.createDirectory(
                at: configDirectoryURL,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: NSNumber(value: Int16(0o700))]
            )
        } catch {
            throw ProviderConfigStoreError.cannotCreateDirectory(error.localizedDescription)
        }
    }

    private var appRootURL: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // Settings/
            .deletingLastPathComponent()   // steelg8/
            .deletingLastPathComponent()   // Sources/
            .deletingLastPathComponent()   // 仓库根
    }
}
