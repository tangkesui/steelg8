import Foundation

/// 对应 ~/.steelg8/providers.json 的一条 provider 配置。
/// 与 Python 端 `providers.py` 的 JSON schema 对齐：
/// - api_key 为空时，Python 端会回退去读 api_key_env
/// - 两者都为空则 provider 视为未就绪
/// 一条模型 row，用稳定 UUID 做 SwiftUI ForEach 的 id。
/// 避免"按 index 做 id + Binding"在删除时越界崩溃。
struct ModelRow: Identifiable, Equatable {
    let id: UUID
    var value: String

    init(_ value: String, id: UUID = UUID()) {
        self.id = id
        self.value = value
    }
}

/// 给 SettingsView 用的视图模型。不参与 JSON 编解码（JSON 层用 ProviderConfigPayload）。
struct ProviderEntry: Identifiable, Equatable {
    var name: String          // "kimi" / "deepseek" / "qwen" / "openrouter" …
    var baseURL: String
    var apiKeyEnv: String
    var apiKey: String        // Settings UI 直接写入的明文 key（可空）
    var modelRows: [ModelRow]

    var id: String { name }

    init(
        name: String,
        baseURL: String,
        apiKeyEnv: String,
        apiKey: String,
        models: [String]
    ) {
        self.name = name
        self.baseURL = baseURL
        self.apiKeyEnv = apiKeyEnv
        self.apiKey = apiKey
        self.modelRows = models.map { ModelRow($0) }
    }

    /// 保存到 JSON 时扁平化成 [String]。
    var models: [String] {
        modelRows.map(\.value)
    }

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

        init(baseURL: String, apiKeyEnv: String, apiKey: String, models: [String]) {
            self.baseURL = baseURL
            self.apiKeyEnv = apiKeyEnv
            self.apiKey = apiKey
            self.models = models
        }

        // 宽松解码：api_key / api_key_env / models 任一缺失都回退默认值，
        // 这样 example 模板（没写 api_key）也能正常加载。
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            self.baseURL   = try c.decodeIfPresent(String.self, forKey: .baseURL) ?? ""
            self.apiKeyEnv = try c.decodeIfPresent(String.self, forKey: .apiKeyEnv) ?? ""
            self.apiKey    = try c.decodeIfPresent(String.self, forKey: .apiKey) ?? ""
            self.models    = try c.decodeIfPresent([String].self, forKey: .models) ?? []
        }
    }

    enum CodingKeys: String, CodingKey {
        case defaultModel = "default_model"
        case providers
    }

    init(defaultModel: String, providers: [String: ProviderValue]) {
        self.defaultModel = defaultModel
        self.providers = providers
    }

    // 同样宽松解码顶层字段，兼容 example 里多余的 $schema_note。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.defaultModel = try c.decodeIfPresent(String.self, forKey: .defaultModel) ?? ""
        self.providers = try c.decodeIfPresent([String: ProviderValue].self, forKey: .providers) ?? [:]
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

    /// 按优先级列出可能的 example 模板路径：
    /// 1) 打包后 .app 的 Resources/config/providers.example.json
    /// 2) 开发源树下的 config/providers.example.json
    var templateCandidates: [URL] {
        var out: [URL] = []
        if let bundled = Bundle.main.url(
            forResource: "providers.example",
            withExtension: "json",
            subdirectory: "config"
        ) {
            out.append(bundled)
        }
        out.append(
            appRootURL
                .appendingPathComponent("config")
                .appendingPathComponent("providers.example.json")
        )
        return out
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

        // 回退到仓库自带 / .app 内置的 example 模板
        for template in templateCandidates where fileManager.fileExists(atPath: template.path) {
            do {
                let data = try Data(contentsOf: template)
                return try decoder.decode(ProviderConfigPayload.self, from: data)
            } catch {
                NSLog("steelg8 settings: example 模板解码失败 \(template.path): \(error)")
                continue
            }
        }

        NSLog("steelg8 settings: 所有模板都失败，返回空配置")
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
