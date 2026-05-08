import Foundation

extension Notification.Name {
    /// providers.json 落盘后广播；主聊天窗口监听此事件刷新模型列表/默认模型。
    static let providerConfigDidChange = Notification.Name("steelg8.providerConfigDidChange")
}

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

/// 给供应商设置页用的视图模型。不参与 JSON 编解码（JSON 层用 ProviderConfigPayload）。
struct ProviderEntry: Identifiable, Equatable {
    var name: String          // "kimi" / "deepseek" / "qwen" / "openrouter" …
    var displayName: String
    var baseURL: String
    var apiKeyEnv: String
    var apiKey: String        // Settings UI 直接写入的明文 key（可空）
    var kind: String
    var modelRows: [ModelRow]

    var id: String { name }

    init(
        name: String,
        displayName: String? = nil,
        baseURL: String,
        apiKeyEnv: String,
        apiKey: String,
        kind: String = "openai-compatible",
        models: [String]
    ) {
        self.name = name
        self.displayName = displayName ?? name
        self.baseURL = baseURL
        self.apiKeyEnv = apiKeyEnv
        self.apiKey = apiKey
        self.kind = kind
        self.modelRows = models.map { ModelRow($0) }
    }

    /// 保存到 JSON 时扁平化成 [String]。
    var models: [String] {
        modelRows.map(\.value)
    }

    /// 本地判断是否可用，决策仅供 UI 显示；Python 端有自己的权威判断。
    var isConfigured: Bool {
        if kind == "local-runtime" || kind == "tool" {
            return !baseURL.isEmpty
        }
        return !baseURL.isEmpty && (!apiKey.isEmpty || !apiKeyEnv.isEmpty)
    }
}

/// 顶层配置结构。
struct ProviderConfigPayload: Codable {
    var version: Int
    var defaultProvider: String
    var defaultModel: String
    var providers: [ProviderValue]

    struct ProviderValue: Codable {
        var id: String
        var name: String
        var baseURL: String
        var apiKeyEnv: String
        var apiKey: String
        var kind: String
        var models: [String]

        enum CodingKeys: String, CodingKey {
            case id
            case name
            case baseURL = "base_url"
            case apiKeyEnv = "api_key_env"
            case apiKey = "api_key"
            case kind
            case models
        }

        init(
            id: String,
            name: String,
            baseURL: String,
            apiKeyEnv: String,
            apiKey: String,
            kind: String,
            models: [String]
        ) {
            self.id = id
            self.name = name
            self.baseURL = baseURL
            self.apiKeyEnv = apiKeyEnv
            self.apiKey = apiKey
            self.kind = kind
            self.models = models
        }

        // 宽松解码：api_key / api_key_env / models 任一缺失都回退默认值，
        // 这样 example 模板（没写 api_key）也能正常加载。
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            self.id        = try c.decodeIfPresent(String.self, forKey: .id) ?? ""
            self.name      = try c.decodeIfPresent(String.self, forKey: .name) ?? self.id
            self.baseURL   = try c.decodeIfPresent(String.self, forKey: .baseURL) ?? ""
            self.apiKeyEnv = try c.decodeIfPresent(String.self, forKey: .apiKeyEnv) ?? ""
            self.apiKey    = try c.decodeIfPresent(String.self, forKey: .apiKey) ?? ""
            self.kind      = try c.decodeIfPresent(String.self, forKey: .kind) ?? "openai-compatible"
            self.models    = try c.decodeIfPresent([String].self, forKey: .models) ?? []
        }
    }

    enum CodingKeys: String, CodingKey {
        case version
        case defaultProvider = "default_provider"
        case defaultModel = "default_model"
        case providers
    }

    init(
        version: Int = 2,
        defaultProvider: String = "",
        defaultModel: String,
        providers: [ProviderValue]
    ) {
        self.version = version
        self.defaultProvider = defaultProvider
        self.defaultModel = defaultModel
        self.providers = providers
    }

    // 同样宽松解码顶层字段，兼容 example 里多余的 $schema_note。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.version = try c.decodeIfPresent(Int.self, forKey: .version) ?? 1
        self.defaultProvider = try c.decodeIfPresent(String.self, forKey: .defaultProvider) ?? ""
        self.defaultModel = try c.decodeIfPresent(String.self, forKey: .defaultModel) ?? ""
        if let list = try? c.decodeIfPresent([ProviderValue].self, forKey: .providers) {
            self.providers = list
        } else {
            let legacy = try c.decodeIfPresent([String: ProviderValue].self, forKey: .providers) ?? [:]
            self.providers = legacy
                .sorted { $0.key < $1.key }
                .map { key, value in
                    var copy = value
                    copy.id = key
                    if copy.name.isEmpty { copy.name = key }
                    return copy
                }
        }
    }
}

struct ProviderConfigValidation {
    var errors: [String] = []
    var warnings: [String] = []

    var isValid: Bool {
        errors.isEmpty
    }

    var shortSummary: String {
        if let first = errors.first {
            return first
        }
        if let first = warnings.first {
            return first
        }
        return "配置看起来没问题"
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
        KernelConfig.userConfigDirectoryURL
    }

    var configFileURL: URL {
        configDirectoryURL.appending(path: "providers.json")
    }

    /// 按优先级列出可能的 example 模板路径：
    /// 1) 打包后 .app 的 Resources/config/providers.example.json
    /// 2) 开发源树下的 config/providers.example.json
    var templateCandidates: [URL] {
        KernelConfig.resourceCandidates(for: "config/providers.example.json")
    }

    // MARK: - 读

    /// 读取现有配置；若文件不存在，则以 example 模板为起点返回一份可编辑副本。
    func load() throws -> (entries: [ProviderEntry], defaultModel: String) {
        let payload = try loadPayload()
        let catalogModels = loadCatalogModels()
        let secrets = SecretsStore.shared.readAll()
        let entries = payload.providers
            .sorted { $0.id < $1.id }
            .map { value in
                // SecretsStore 优先（secrets.json / Keychain），兼容迁移前 providers.json 里的 api_key
                let apiKey = secrets[value.id] ?? (value.apiKey.isEmpty ? "" : value.apiKey)
                return ProviderEntry(
                    name: value.id,
                    displayName: value.name,
                    baseURL: value.baseURL,
                    apiKeyEnv: value.apiKeyEnv,
                    apiKey: apiKey,
                    kind: value.kind,
                    models: catalogModels[value.id] ?? value.models
                )
            }
        return (entries, payload.defaultModel)
    }

    // MARK: - 写

    /// 将 Settings UI 的 entries + defaultModel 写回到 ~/.steelg8/providers.json（0644）。
    /// api_key 单独写入 SecretsStore（secrets.json 0600 或 Keychain），不再落 providers.json。
    func save(entries: [ProviderEntry], defaultModel: String) throws {
        try ensureDirectoryExists()

        // 先把 api_key 写到安全存储
        let normalized = normalizedEntries(entries)
        var secrets: [String: String] = [:]
        for entry in normalized {
            secrets[entry.name] = entry.apiKey
        }
        try SecretsStore.shared.writeAll(secrets)

        // providers.json 不再包含 api_key
        let providers = normalized.map { entry in
            ProviderConfigPayload.ProviderValue(
                id: entry.name,
                name: entry.displayName.isEmpty ? entry.name : entry.displayName,
                baseURL: entry.baseURL,
                apiKeyEnv: entry.apiKeyEnv,
                apiKey: "",   // 已迁移到 SecretsStore
                kind: entry.kind.isEmpty ? "openai-compatible" : entry.kind,
                models: []
            )
        }

        let payload = ProviderConfigPayload(
            version: 2,
            defaultProvider: providers.first?.id ?? "",
            defaultModel: defaultModel.trimmingCharacters(in: .whitespacesAndNewlines),
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

        // 0644：providers.json 不含密钥，可被同组读取
        try? fileManager.setAttributes(
            [.posixPermissions: NSNumber(value: Int16(0o644))],
            ofItemAtPath: configFileURL.path
        )

        // 通知主窗口刷新（默认模型 / 可选模型列表）
        NotificationCenter.default.post(name: .providerConfigDidChange, object: nil)
    }

    /// 只覆盖 entries，preserve 现有 default_model；用于"供应商与模型"页（不再持有默认模型）。
    func saveEntriesPreservingDefault(_ entries: [ProviderEntry]) throws {
        let current = (try? loadPayload()) ?? ProviderConfigPayload(defaultModel: "", providers: [])
        try save(entries: entries, defaultModel: current.defaultModel)
    }

    /// 只覆盖 default_model，preserve 现有 entries；用于"基础"页。
    func saveDefaultModelPreservingEntries(_ defaultModel: String) throws {
        // 必须走 load()，让 SecretsStore / catalog 合并进 ProviderEntry。
        // 直接读 providers.json 会把 api_key 视为空，保存默认模型时清空 secrets.json。
        let entries = (try? load().entries) ?? []
        try save(entries: entries, defaultModel: defaultModel)
    }

    /// 只校验 entries 部分（不要求 default_model）。基础页用 validateDefaultModel。
    func validateEntries(_ entries: [ProviderEntry]) -> ProviderConfigValidation {
        validate(entries: entries, defaultModel: "")
    }

    func validate(entries: [ProviderEntry], defaultModel: String) -> ProviderConfigValidation {
        var result = ProviderConfigValidation()
        let normalized = normalizedEntries(entries, deduplicateModels: false)
        let toolProviders: Set<String> = ["tavily"]

        var providerNames = Set<String>()
        var modelOwners: [String: [String]] = [:]

        for entry in normalized {
            let providerKey = entry.name.lowercased()
            if entry.name.isEmpty {
                result.errors.append("Provider 名不能为空。")
                continue
            }
            if providerNames.contains(providerKey) {
                result.errors.append("Provider 名重复：\(entry.name)")
            }
            providerNames.insert(providerKey)

            guard
                let components = URLComponents(string: entry.baseURL),
                let scheme = components.scheme?.lowercased(),
                ["http", "https"].contains(scheme),
                components.host != nil
            else {
                result.errors.append("\(entry.name) 的 Base URL 必须是 http(s) URL。")
                continue
            }

            if entry.kind != "local-runtime" && entry.kind != "tool" && entry.apiKey.isEmpty && entry.apiKeyEnv.isEmpty {
                result.warnings.append("\(entry.name) 没有 API Key 或 Env 变量名，保存后不会就绪。")
            }
            if !entry.apiKeyEnv.isEmpty && !isShellEnvName(entry.apiKeyEnv) {
                result.warnings.append("\(entry.name) 的 Env 变量名不太像 shell 变量：\(entry.apiKeyEnv)")
            }
            if entry.models.isEmpty && entry.kind != "local-runtime" && !toolProviders.contains(providerKey) {
                result.warnings.append("\(entry.name) 没有模型，LLM 路由不会选中它。")
            }

            var localModels = Set<String>()
            for model in entry.models {
                if localModels.contains(model) {
                    result.warnings.append("\(entry.name) 里模型重复：\(model)")
                }
                localModels.insert(model)
                modelOwners[model, default: []].append(entry.name)
            }
        }

        for (model, owners) in modelOwners where owners.count > 1 {
            result.warnings.append("模型 \(model) 同时出现在 \(owners.joined(separator: "、"))。")
        }

        let trimmedDefault = defaultModel.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedDefault.isEmpty {
            if isUtilityModel(trimmedDefault) {
                result.errors.append("默认模型不能是 embedding / rerank 工具模型：\(trimmedDefault)")
            } else if modelOwners[trimmedDefault] == nil {
                result.errors.append("默认模型 \(trimmedDefault) 不在任何 Provider 的模型列表里。")
            }
        }

        return result
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
        return ProviderConfigPayload(defaultModel: "", providers: [])
    }

    private func loadCatalogModels() -> [String: [String]] {
        let userCatalog = KernelConfig.userConfigDirectoryURL.appending(path: "model_catalog.json")
        var candidates = [userCatalog]
        candidates.append(contentsOf: KernelConfig.resourceCandidates(for: "config/model_catalog.example.json"))

        for candidate in candidates where fileManager.fileExists(atPath: candidate.path) {
            guard
                let data = try? Data(contentsOf: candidate),
                let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                let providers = raw["providers"] as? [String: Any]
            else {
                continue
            }
            var out: [String: [String]] = [:]
            for (providerID, providerRaw) in providers {
                guard
                    let provider = providerRaw as? [String: Any],
                    let models = provider["models"] as? [[String: Any]]
                else {
                    continue
                }
                let selected = models.compactMap { item -> String? in
                    if (item["selected"] as? Bool) == false {
                        return nil
                    }
                    return item["id"] as? String
                }
                out[providerID] = selected
            }
            return out
        }
        return [:]
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

    private func normalizedEntries(
        _ entries: [ProviderEntry],
        deduplicateModels: Bool = true
    ) -> [ProviderEntry] {
        entries.compactMap { entry in
            let name = entry.name.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty else { return nil }
            var seenModels = Set<String>()
            let models = entry.models
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
                .filter { model in
                    guard deduplicateModels else { return true }
                    if seenModels.contains(model) {
                        return false
                    }
                    seenModels.insert(model)
                    return true
                }
            return ProviderEntry(
                name: name,
                displayName: entry.displayName.trimmingCharacters(in: .whitespacesAndNewlines),
                baseURL: entry.baseURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/")),
                apiKeyEnv: entry.apiKeyEnv.trimmingCharacters(in: .whitespacesAndNewlines),
                apiKey: entry.apiKey.trimmingCharacters(in: .whitespacesAndNewlines),
                kind: entry.kind.trimmingCharacters(in: .whitespacesAndNewlines),
                models: models
            )
        }
    }

    private func isShellEnvName(_ value: String) -> Bool {
        let pattern = #"^[A-Za-z_][A-Za-z0-9_]*$"#
        return value.range(of: pattern, options: .regularExpression) != nil
    }

    private func isUtilityModel(_ value: String) -> Bool {
        let lowered = value.lowercased()
        return lowered.contains("embedding") || lowered.contains("rerank")
    }
}
