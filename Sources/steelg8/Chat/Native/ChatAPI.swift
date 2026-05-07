import Foundation

// MARK: - 响应数据结构

struct ProjectItem: Identifiable, Decodable, Equatable {
    let id: Int
    let name: String
    let path: String
    let active: Bool
}

struct ProjectStatus: Decodable {
    let state: String   // "idle" | "running" | "error"
    let error: String?
    let count: Int?
}

struct ConversationItem: Identifiable, Decodable, Equatable {
    let id: Int
    let title: String?
    let summaryTokens: Int?
}

struct HistoryMessage: Decodable {
    let role: String
    let content: String
    let compressed: Bool?
}

struct ProjectConversationResponse: Decodable {
    let conversation: ConversationItem?
    let messages: [HistoryMessage]
}

struct ProviderModels: Decodable {
    let models: [String]?
    let providers: [ProviderSummary]?

    var selectableModels: [String] {
        var out: [String] = []
        var seen = Set<String>()

        for model in models ?? [] {
            if seen.insert(model).inserted {
                out.append(model)
            }
        }

        for provider in providers ?? [] {
            for model in provider.models ?? [] {
                let canonical = "\(provider.name)/\(model)"
                if seen.insert(canonical).inserted {
                    out.append(canonical)
                }
            }
        }

        return out
    }
}

struct ProviderSummary: Decodable {
    let name: String
    let models: [String]?
}

struct HealthStatus: Decodable {
    let status: String
    let authRequired: Bool
    let authenticated: Bool

    enum CodingKeys: String, CodingKey {
        case status
        case authRequired = "authRequired"
        case authenticated
    }
}

// MARK: - API 客户端

struct ChatAPI {
    // MARK: - Projects

    func listProjects() async throws -> [ProjectItem] {
        let resp: ItemsWrapper<ProjectItem> = try await get("projects")
        return resp.items
    }

    func projectStatus() async throws -> ProjectStatus {
        try await get("project/status")
    }

    func projectConversation() async throws -> ProjectConversationResponse {
        try await get("project/conversation")
    }

    func activateProject(_ id: Int) async throws {
        let _: OkResponse = try await post("projects/\(id)/activate", body: EmptyBody())
    }

    func deleteProject(_ id: Int) async throws {
        let _: OkResponse = try await delete("projects/\(id)")
    }

    func renameProject(_ id: Int, name: String) async throws {
        let _: OkResponse = try await post("projects/\(id)/rename", body: NameBody(name: name))
    }

    func reindexProject() async throws {
        let _: OkResponse = try await post("project/reindex", body: EmptyBody())
    }

    func openProject(path: String) async throws {
        let _: OkResponse = try await post("project/open", body: PathBody(path: path))
    }

    func closeProject() async throws {
        let _: OkResponse = try await post("project/close", body: EmptyBody())
    }

    // MARK: - Conversations

    func listConversations() async throws -> [ConversationItem] {
        let resp: ItemsWrapper<ConversationItem> = try await get("conversations")
        return resp.items
    }

    func loadMessages(_ id: Int) async throws -> [HistoryMessage] {
        struct Resp: Decodable { let messages: [HistoryMessage] }
        let resp: Resp = try await get("conversations/\(id)/messages")
        return resp.messages
    }

    func deleteConversation(_ id: Int) async throws {
        let _: OkResponse = try await delete("conversations/\(id)")
    }

    func renameConversation(_ id: Int, title: String) async throws {
        let _: OkResponse = try await post("conversations/\(id)/rename", body: TitleBody(title: title))
    }

    // MARK: - Scratch

    func loadScratch() async throws -> String {
        struct Resp: Decodable { let text: String }
        let resp: Resp = try await get("scratch/note")
        return resp.text
    }

    func saveScratch(_ text: String) async throws {
        struct Body: Encodable { let text: String }
        let _: OkResponse = try await post("scratch/note", body: Body(text: text))
    }

    // MARK: - Providers

    func listModels() async throws -> [String] {
        let resp: ProviderModels = try await get("providers")
        return resp.selectableModels
    }

    // MARK: - Health

    func health() async throws -> HealthStatus {
        var req = URLRequest(url: KernelConfig.url(path: "health"))
        req.httpMethod = "GET"
        req.timeoutInterval = 5
        KernelConfig.authorize(&req)
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode(HealthStatus.self, from: data)
    }

    // MARK: - Chat stream request builder

    func chatStreamRequest(message: String, model: String?, conversationId: Int?) throws -> URLRequest {
        struct Body: Encodable {
            let message: String
            let model: String?
            let conversationId: Int?
            let stream: Bool
        }
        var req = URLRequest(url: KernelConfig.url(path: "chat/stream"))
        req.httpMethod = "POST"
        req.timeoutInterval = 300
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        KernelConfig.authorize(&req)
        req.httpBody = try JSONEncoder().encode(Body(
            message: message,
            model: model.flatMap { $0.isEmpty ? nil : $0 },
            conversationId: conversationId,
            stream: true
        ))
        return req
    }

    // MARK: - Private helpers

    private func get<T: Decodable>(_ path: String) async throws -> T {
        var req = URLRequest(url: KernelConfig.url(path: path))
        req.httpMethod = "GET"
        req.timeoutInterval = 10
        KernelConfig.authorize(&req)
        return try await run(req)
    }

    private func post<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        var req = URLRequest(url: KernelConfig.url(path: path))
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        KernelConfig.authorize(&req)
        req.httpBody = try JSONEncoder().encode(body)
        return try await run(req)
    }

    private func delete<T: Decodable>(_ path: String) async throws -> T {
        var req = URLRequest(url: KernelConfig.url(path: path))
        req.httpMethod = "DELETE"
        req.timeoutInterval = 10
        KernelConfig.authorize(&req)
        return try await run(req)
    }

    private func run<T: Decodable>(_ req: URLRequest) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard (200..<300).contains(http.statusCode) else {
            throw ChatAPIError.badStatus(http.statusCode)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

enum ChatAPIError: LocalizedError {
    case badStatus(Int)
    var errorDescription: String? {
        if case .badStatus(let c) = self { return "HTTP \(c)" }
        return nil
    }
}

// MARK: - 辅助类型

private struct ItemsWrapper<T: Decodable>: Decodable {
    let items: [T]
}
private struct OkResponse: Decodable { let ok: Bool? }
private struct EmptyBody: Encodable {}
private struct NameBody: Encodable { let name: String }
private struct TitleBody: Encodable { let title: String }
private struct PathBody: Encodable { let path: String }
