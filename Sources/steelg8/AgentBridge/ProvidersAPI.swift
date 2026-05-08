import Foundation

struct CatalogModel: Identifiable, Decodable, Equatable {
    struct Pricing: Decodable, Equatable {
        let input: Double?
        let output: Double?
    }

    let id: String
    let selected: Bool
    let pricingPerMToken: Pricing?
    let pricingSource: String?    // "verified" | "fallback"
    let createdAt: Int?           // UNIX ts，发布时间排序用
    let capabilities: [String]?   // ["chat", "embedding", "rerank", ...]

    init(
        id: String,
        selected: Bool,
        pricingPerMToken: Pricing?,
        pricingSource: String? = "fallback",
        createdAt: Int? = nil,
        capabilities: [String]? = nil
    ) {
        self.id = id
        self.selected = selected
        self.pricingPerMToken = pricingPerMToken
        self.pricingSource = pricingSource
        self.createdAt = createdAt
        self.capabilities = capabilities
    }

    enum CodingKeys: String, CodingKey {
        case id
        case selected
        case pricingPerMToken = "pricing_per_mtoken"
        case pricingSource = "pricing_source"
        case createdAt = "created_at"
        case capabilities
    }
}

struct CatalogReadResponse: Decodable {
    let ok: Bool
    let name: String
    let fetchedAt: String?
    let models: [CatalogModel]

    enum CodingKeys: String, CodingKey {
        case ok
        case name
        case fetchedAt = "fetched_at"
        case models
    }
}

struct CatalogRefreshResponse: Decodable {
    let ok: Bool
    let name: String
    let count: Int
    let fetchedAt: String?
    let models: [CatalogModel]

    enum CodingKeys: String, CodingKey {
        case ok
        case name
        case count
        case fetchedAt = "fetched_at"
        case models
    }
}

struct SyncModelsResponse: Decodable {
    let ok: Bool
    let name: String
    let count: Int
    let models: [String]
}

enum ProvidersAPIError: LocalizedError {
    case badStatus(Int, String)

    var errorDescription: String? {
        switch self {
        case let .badStatus(status, message):
            return "Kernel 返回 \(status)：\(message)"
        }
    }
}

struct ProvidersAPI {
    func refreshCatalog(provider: String) async throws -> CatalogRefreshResponse {
        try await send(
            path: "providers/\(provider)/catalog/refresh",
            method: "POST",
            body: EmptyBody()
        )
    }

    func readCatalog(provider: String) async throws -> CatalogReadResponse {
        try await send(path: "providers/\(provider)/catalog", method: "GET")
    }

    func updateCatalogSelection(provider: String, modelIds: [String]) async throws -> CatalogReadResponse {
        try await send(
            path: "providers/\(provider)/catalog/selection",
            method: "PUT",
            body: SelectionBody(modelIds: modelIds)
        )
    }

    func reloadProviders() async throws {
        let _: ReloadResponse = try await send(
            path: "providers/reload",
            method: "POST",
            body: EmptyBody()
        )
    }

    func syncModels(provider: String) async throws -> SyncModelsResponse {
        try await send(
            path: "providers/\(provider)/sync-models",
            method: "POST",
            body: EmptyBody()
        )
    }

    func updateCatalogPricing(
        provider: String,
        modelId: String,
        input: Double?,
        output: Double?
    ) async throws -> CatalogReadResponse {
        try await send(
            path: "providers/\(provider)/catalog/pricing",
            method: "PUT",
            body: PricingBody(modelId: modelId, input: input, output: output, reset: false)
        )
    }

    func resetCatalogPricing(provider: String, modelId: String) async throws -> CatalogReadResponse {
        try await send(
            path: "providers/\(provider)/catalog/pricing",
            method: "PUT",
            body: PricingBody(modelId: modelId, input: nil, output: nil, reset: true)
        )
    }

    func updateDefaultProvider(_ name: String) async throws -> DefaultProviderResponse {
        try await send(
            path: "providers/registry/default-provider",
            method: "PUT",
            body: DefaultProviderBody(defaultProvider: name)
        )
    }

    func updateProviderOrder(_ order: [String]) async throws -> ProviderOrderResponse {
        try await send(
            path: "providers/registry/order",
            method: "PUT",
            body: ProviderOrderBody(order: order)
        )
    }

    func routerState() async throws -> RouterStateResponse {
        try await send(path: "router/state", method: "GET")
    }

    func resolveModel(_ model: String) async throws -> ResolveModelResponse {
        try await send(
            path: "providers/resolve",
            method: "POST",
            body: ResolveBody(model: model)
        )
    }

    func toggleCapability(
        provider: String, modelId: String, capability: String, enabled: Bool
    ) async throws -> CatalogReadResponse {
        try await send(
            path: "providers/\(provider)/catalog/capability",
            method: "PUT",
            body: CapabilityBody(modelId: modelId, capability: capability, enabled: enabled)
        )
    }

    func getRagConfig() async throws -> RagConfigResponse {
        try await send(path: "rag/config", method: "GET")
    }

    func putRagConfig(_ config: RagConfigPayload) async throws -> RagConfigPutResponse {
        try await send(path: "rag/config", method: "PUT", body: config)
    }

    func testEmbedding(_ text: String) async throws -> RagTestEmbeddingResponse {
        try await send(
            path: "rag/test-embedding",
            method: "POST",
            body: TestEmbeddingBody(text: text)
        )
    }

    func ragDiagnostics() async throws -> RagDiagnosticsResponse {
        try await send(path: "rag/diagnostics", method: "GET")
    }

    private func send<Response: Decodable>(
        path: String,
        method: String
    ) async throws -> Response {
        var request = URLRequest(url: KernelConfig.url(path: path))
        request.httpMethod = method
        request.timeoutInterval = 20
        KernelConfig.authorize(&request)
        return try await run(request)
    }

    private func send<Body: Encodable, Response: Decodable>(
        path: String,
        method: String,
        body: Body
    ) async throws -> Response {
        var request = URLRequest(url: KernelConfig.url(path: path))
        request.httpMethod = method
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        KernelConfig.authorize(&request)
        request.httpBody = try JSONEncoder().encode(body)
        return try await run(request)
    }

    private func run<Response: Decodable>(_ request: URLRequest) async throws -> Response {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard (200..<300).contains(http.statusCode) else {
            let message = parseErrorMessage(data) ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw ProvidersAPIError.badStatus(http.statusCode, message)
        }
        return try JSONDecoder().decode(Response.self, from: data)
    }

    private func parseErrorMessage(_ data: Data) -> String? {
        guard
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let error = obj["error"] as? String
        else {
            return nil
        }
        return error
    }

    private struct EmptyBody: Encodable {}

    private struct SelectionBody: Encodable {
        let modelIds: [String]

        enum CodingKeys: String, CodingKey {
            case modelIds = "model_ids"
        }
    }

    private struct PricingBody: Encodable {
        let modelId: String
        let input: Double?
        let output: Double?
        let reset: Bool

        enum CodingKeys: String, CodingKey {
            case modelId = "model_id"
            case input
            case output
            case reset
        }
    }

    private struct DefaultProviderBody: Encodable {
        let defaultProvider: String

        enum CodingKeys: String, CodingKey {
            case defaultProvider = "default_provider"
        }
    }

    private struct ProviderOrderBody: Encodable {
        let order: [String]
    }

    private struct ReloadResponse: Decodable {
        let ok: Bool
    }

    private struct ResolveBody: Encodable {
        let model: String
    }

    private struct CapabilityBody: Encodable {
        let modelId: String
        let capability: String
        let enabled: Bool

        enum CodingKeys: String, CodingKey {
            case modelId = "model_id"
            case capability
            case enabled
        }
    }

    private struct TestEmbeddingBody: Encodable {
        let text: String
    }
}

// MARK: - Public payload structs (used by both API client + view models)

struct RagConfigPayload: Codable, Equatable {
    var version: Int
    var embedding: EmbeddingPayload
    var rerank: RerankPayload
    var strategy: StrategyPayload
    var backend: BackendPayload

    struct EmbeddingPayload: Codable, Equatable {
        var provider: String
        var model: String
        var dimensions: Int
        var endpointKind: String

        enum CodingKeys: String, CodingKey {
            case provider, model, dimensions
            case endpointKind = "endpoint_kind"
        }
    }
    struct RerankPayload: Codable, Equatable {
        var provider: String
        var model: String
        var endpointKind: String
        var endpointUrlOverride: String?

        enum CodingKeys: String, CodingKey {
            case provider, model
            case endpointKind = "endpoint_kind"
            case endpointUrlOverride = "endpoint_url_override"
        }
    }
    struct StrategyPayload: Codable, Equatable {
        var id: String
    }
    struct BackendPayload: Codable, Equatable {
        var id: String
    }
}

struct RagConfigResponse: Decodable {
    let ok: Bool
    let config: RagConfigPayload
    let fingerprint: String
    let providers: [RagProviderCandidate]
    let embeddingCandidates: [RagModelCandidate]
    let rerankCandidates: [RagModelCandidate]
    let strategies: [String]
    let backends: [String]

    enum CodingKeys: String, CodingKey {
        case ok, config, fingerprint, providers
        case embeddingCandidates = "embedding_candidates"
        case rerankCandidates = "rerank_candidates"
        case strategies, backends
    }
}

struct RagProviderCandidate: Decodable, Identifiable {
    var id: String
    let displayName: String
    let kind: String
    let ready: Bool
    let baseUrl: String?

    enum CodingKeys: String, CodingKey {
        case id
        case displayName
        case kind
        case ready
        case baseUrl
    }
}

struct RagModelCandidate: Decodable, Hashable, Identifiable {
    let provider: String
    let model: String
    let ready: Bool

    var id: String { "\(provider)::\(model)" }
}

struct RagConfigPutResponse: Decodable {
    let ok: Bool
    let config: RagConfigPayload
    let fingerprint: String
}

struct RagTestEmbeddingResponse: Decodable {
    let ok: Bool
    let model: String
    let dimensions: Int
    let preview: [Double]
    let elapsedMs: Int

    enum CodingKeys: String, CodingKey {
        case ok, model, dimensions, preview
        case elapsedMs = "elapsed_ms"
    }
}

struct RagDiagnosticsResponse: Decodable {
    let ok: Bool
    let embedOk: EmbedSuccessSnapshot?
    let embedErr: EmbedFailureSnapshot?
    let rerankOk: RerankSuccessSnapshot?
    let rerankErr: RerankFailureSnapshot?

    enum CodingKeys: String, CodingKey {
        case ok
        case embedOk = "embed_ok"
        case embedErr = "embed_err"
        case rerankOk = "rerank_ok"
        case rerankErr = "rerank_err"
    }

    struct EmbedSuccessSnapshot: Decodable {
        let timestamp: Double
        let provider: String
        let model: String
        let fingerprint: String
        let dimensions: Int
        let totalTexts: Int
        let latencyMs: Int
        let batchSize: Int

        enum CodingKeys: String, CodingKey {
            case timestamp, provider, model, fingerprint, dimensions
            case totalTexts = "total_texts"
            case latencyMs = "latency_ms"
            case batchSize = "batch_size"
        }

        // 旧 kernel 没这俩字段时降级到 0
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            timestamp = try c.decode(Double.self, forKey: .timestamp)
            provider = try c.decode(String.self, forKey: .provider)
            model = try c.decode(String.self, forKey: .model)
            fingerprint = try c.decode(String.self, forKey: .fingerprint)
            dimensions = try c.decode(Int.self, forKey: .dimensions)
            totalTexts = try c.decode(Int.self, forKey: .totalTexts)
            latencyMs = try c.decodeIfPresent(Int.self, forKey: .latencyMs) ?? 0
            batchSize = try c.decodeIfPresent(Int.self, forKey: .batchSize) ?? 0
        }
    }
    struct EmbedFailureSnapshot: Decodable {
        let timestamp: Double
        let provider: String
        let model: String
        let kind: String
        let message: String
    }
    struct RerankSuccessSnapshot: Decodable {
        let timestamp: Double
        let provider: String
        let model: String
        let endpointKind: String
        let docCount: Int
        let fallbackUsed: Bool
        let latencyMs: Int

        enum CodingKeys: String, CodingKey {
            case timestamp, provider, model
            case endpointKind = "endpoint_kind"
            case docCount = "doc_count"
            case fallbackUsed = "fallback_used"
            case latencyMs = "latency_ms"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            timestamp = try c.decode(Double.self, forKey: .timestamp)
            provider = try c.decode(String.self, forKey: .provider)
            model = try c.decode(String.self, forKey: .model)
            endpointKind = try c.decode(String.self, forKey: .endpointKind)
            docCount = try c.decode(Int.self, forKey: .docCount)
            fallbackUsed = try c.decode(Bool.self, forKey: .fallbackUsed)
            latencyMs = try c.decodeIfPresent(Int.self, forKey: .latencyMs) ?? 0
        }
    }
    struct RerankFailureSnapshot: Decodable {
        let timestamp: Double
        let provider: String
        let model: String
        let kind: String
        let message: String
    }
}

struct ResolveModelResponse: Decodable {
    let ok: Bool
    let provider: String
    let model: String
    let layer: String
    let reason: String
}

struct DefaultProviderResponse: Decodable {
    let ok: Bool
    let defaultProvider: String

    enum CodingKeys: String, CodingKey {
        case ok
        case defaultProvider = "default_provider"
    }
}

struct ProviderOrderResponse: Decodable {
    let ok: Bool
    let order: [String]
}

struct RouterStateResponse: Decodable {
    let ok: Bool
    let last: RouterDecision?

    struct RouterDecision: Decodable {
        let model: String
        let provider: String
        let layer: String         // "explicit" | "default" | "fallback" | "mock"
        let reason: String
        let timestamp: Double
    }
}
