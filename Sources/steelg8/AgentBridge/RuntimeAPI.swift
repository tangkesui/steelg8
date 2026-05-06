import Foundation

// 运行状态页共用的 HTTP 客户端 + 响应结构体
struct RuntimeAPI {

    // MARK: - Usage

    func usageSummary() async throws -> UsageSummaryResponse {
        try await get("usage/summary")
    }

    // MARK: - Diagnostics

    func diagnosticsDoctor() async throws -> DoctorResponse {
        try await get("diagnostics/doctor")
    }

    func diagnosticsIndex() async throws -> IndexResponse {
        try await get("diagnostics/index")
    }

    func diagnosticsRAGDebug(query: String, topK: Int = 5) async throws -> RAGDebugResponse {
        struct Body: Encodable {
            let query: String
            let topK: Int
            enum CodingKeys: String, CodingKey { case query, topK }
        }
        return try await post("diagnostics/rag-debug", body: Body(query: query, topK: topK))
    }

    // MARK: - Logs

    func logs(limit: Int = 200, days: Int = 2, level: String? = nil) async throws -> LogsResponse {
        var path = "logs?limit=\(limit)&days=\(days)"
        if let level { path += "&level=\(level)" }
        return try await get(path)
    }

    // MARK: - Private HTTP

    private func get<T: Decodable>(_ path: String) async throws -> T {
        var req = URLRequest(url: KernelConfig.url(path: path))
        req.httpMethod = "GET"
        req.timeoutInterval = 20
        KernelConfig.authorize(&req)
        return try await run(req)
    }

    private func post<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        var req = URLRequest(url: KernelConfig.url(path: path))
        req.httpMethod = "POST"
        req.timeoutInterval = 30
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        KernelConfig.authorize(&req)
        req.httpBody = try JSONEncoder().encode(body)
        return try await run(req)
    }

    private func run<T: Decodable>(_ req: URLRequest) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard (200..<300).contains(http.statusCode) else {
            throw RuntimeAPIError.badStatus(http.statusCode)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

enum RuntimeAPIError: LocalizedError {
    case badStatus(Int)
    var errorDescription: String? {
        if case .badStatus(let code) = self { return "Kernel 返回 HTTP \(code)" }
        return nil
    }
}

// MARK: - Usage 响应结构

struct UsageSummaryResponse: Decodable {
    let session: UsageAgg
    let today: UsageAgg
    let total: UsageAgg
    let sessionBreakdown: [UsageBreakdown]
    let usdToCny: Double
}

struct UsageAgg: Decodable {
    let prompt: Int
    let completion: Int
    let total: Int
    let costUSD: Double
    let calls: Int
    enum CodingKeys: String, CodingKey {
        case prompt, completion, total, calls
        case costUSD = "cost_usd"
    }
}

struct UsageBreakdown: Decodable, Identifiable {
    var id: String { "\(provider):\(model)" }
    let model: String
    let provider: String
    let prompt: Int
    let completion: Int
    let costUSD: Double
    let calls: Int
    enum CodingKeys: String, CodingKey {
        case model, provider, prompt, completion, calls
        case costUSD = "cost_usd"
    }
}

// MARK: - Doctor 响应结构

struct DoctorResponse: Decodable {
    let ok: Bool
    let level: String
    let checks: [DiagCheck]
    let issues: [DoctorIssue]
}

struct DiagCheck: Decodable, Identifiable {
    var id: String { name }
    let name: String
    let level: String
    let ok: Bool
    let message: String
}

struct DoctorIssue: Decodable, Identifiable {
    var id: String { check }
    let level: String
    let check: String
    let message: String
}

// MARK: - Index 响应结构

struct IndexResponse: Decodable {
    let ok: Bool
    let level: String
    let activeProject: ActiveProjectInfo?
    let message: String?
    let manifest: IndexManifest?
}

struct ActiveProjectInfo: Decodable {
    let path: String
    let name: String?
    let chunkCount: Int?
    let embedModel: String?
}

struct IndexManifest: Decodable {
    let count: Int
    let totalChunks: Int
    let staleFiles: [String]
    let missingManifestFiles: [String]
    let items: [IndexItem]?
}

struct IndexItem: Decodable, Identifiable {
    var id: String { relPath }
    let relPath: String
    let chunkCount: Int
    let exists: Bool
}

// MARK: - RAG Debug 响应结构

struct RAGDebugResponse: Decodable {
    let ok: Bool
    let query: String
    let activeProject: String?
    let finalHits: [RAGHit]

    enum CodingKeys: String, CodingKey {
        case ok, query
        case activeProject
        case finalHits = "final"
    }
}

struct RAGHit: Decodable, Identifiable {
    var id: String { "\(relPath):\(chunkIdx)" }
    let relPath: String
    let chunkIdx: Int
    let score: Double
    let retrieval: String?
    let text: String?
}

// MARK: - Logs 响应结构

struct LogsResponse: Decodable {
    let items: [LogItem]
}

struct LogItem: Decodable, Identifiable {
    var id: String { "\(ts):\(event)" }
    let ts: String
    let level: String
    let event: String
    let message: String?
    let convId: Int?

    enum CodingKeys: String, CodingKey {
        case ts, level, event, message
        case convId = "conv_id"
    }
}
