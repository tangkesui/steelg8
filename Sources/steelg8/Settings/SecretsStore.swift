import Foundation
import Security

// API Key 存储：
// - 生产签名（anchor apple generic）→ macOS Keychain
// - 自签名 / 开发环境 → ~/.steelg8/secrets.json（Python 端已支持的格式）
//
// secrets.json 格式：{"keys": {"provider_id": "api_key_value"}}
// Python providers.py 按此格式优先读取（api_key_secret 字段）

@MainActor
final class SecretsStore {
    static let shared = SecretsStore()

    private let keychainService = "com.tangkesui.steelg8.apikeys"

    // MARK: - 读

    func readAll() -> [String: String] {
        if isProperlySignedBuild {
            return readAllFromKeychain()
        } else {
            return readSecretsJSON()
        }
    }

    func read(providerID: String) -> String {
        readAll()[providerID] ?? ""
    }

    // MARK: - 写

    func writeAll(_ secrets: [String: String]) throws {
        if isProperlySignedBuild {
            // 先清掉已有的，再写入
            for (providerID, value) in secrets {
                try writeToKeychain(providerID: providerID, value: value)
            }
        } else {
            try writeSecretsJSON(secrets)
        }
    }

    func write(providerID: String, value: String) throws {
        var all = readAll()
        all[providerID] = value.isEmpty ? nil : value
        try writeAll(all)
    }

    // MARK: - 签名检测

    // 检查是否由 Apple 开发者证书签名（非自签名 --sign -）
    private var isProperlySignedBuild: Bool {
        var code: SecStaticCode?
        let url = Bundle.main.bundleURL as CFURL
        guard SecStaticCodeCreateWithPath(url, [], &code) == errSecSuccess, let code else {
            return false
        }
        var req: SecRequirement?
        guard SecRequirementCreateWithString(
            "anchor apple generic" as CFString, [], &req
        ) == errSecSuccess, let req else {
            return false
        }
        return SecStaticCodeCheckValidity(code, [], req) == errSecSuccess
    }

    // MARK: - Keychain 后端

    private func readAllFromKeychain() -> [String: String] {
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: keychainService,
            kSecMatchLimit: kSecMatchLimitAll,
            kSecReturnAttributes: true,
            kSecReturnData: true,
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let items = result as? [[CFString: Any]] else {
            return [:]
        }
        var out: [String: String] = [:]
        for item in items {
            guard
                let account = item[kSecAttrAccount] as? String,
                let data = item[kSecValueData] as? Data,
                let value = String(data: data, encoding: .utf8),
                !value.isEmpty
            else { continue }
            out[account] = value
        }
        return out
    }

    private func writeToKeychain(providerID: String, value: String) throws {
        let data = value.data(using: .utf8) ?? Data()

        // 先尝试更新
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: keychainService,
            kSecAttrAccount: providerID,
        ]
        if value.isEmpty {
            // 删除
            SecItemDelete(query as CFDictionary)
            return
        }
        let update: [CFString: Any] = [kSecValueData: data]
        let updateStatus = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if updateStatus == errSecSuccess { return }

        // 不存在则新增
        var addQuery = query
        addQuery[kSecValueData] = data
        addQuery[kSecAttrLabel] = "steelg8 API Key – \(providerID)"
        let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
        if addStatus != errSecSuccess && addStatus != errSecDuplicateItem {
            throw SecretsStoreError.keychainError(addStatus)
        }
    }

    // MARK: - secrets.json 后端

    private var secretsFileURL: URL {
        KernelConfig.userConfigDirectoryURL.appending(path: "secrets.json")
    }

    private func readSecretsJSON() -> [String: String] {
        let url = secretsFileURL
        guard
            FileManager.default.fileExists(atPath: url.path),
            let data = try? Data(contentsOf: url),
            let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let keys = raw["keys"] as? [String: String]
        else { return [:] }
        return keys
    }

    private func writeSecretsJSON(_ secrets: [String: String]) throws {
        let url = secretsFileURL
        let dir = url.deletingLastPathComponent()

        let fm = FileManager.default
        if !fm.fileExists(atPath: dir.path) {
            try fm.createDirectory(at: dir, withIntermediateDirectories: true,
                                   attributes: [.posixPermissions: NSNumber(value: Int16(0o700))])
        }

        // 过滤空值
        let filtered = secrets.filter { !$0.value.isEmpty }
        let payload: [String: Any] = ["keys": filtered]
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url, options: [.atomic])

        // 0600：只有所有者可读可写
        try? fm.setAttributes(
            [.posixPermissions: NSNumber(value: Int16(0o600))],
            ofItemAtPath: url.path
        )
    }
}

enum SecretsStoreError: LocalizedError {
    case keychainError(OSStatus)

    var errorDescription: String? {
        switch self {
        case let .keychainError(status):
            return "Keychain 操作失败（OSStatus \(status)）"
        }
    }
}
