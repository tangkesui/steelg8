import Foundation

struct CatalogModel: Identifiable, Decodable, Equatable {
    struct Pricing: Decodable, Equatable {
        let input: Double?
        let output: Double?
    }

    let id: String
    let selected: Bool
    let pricingPerMToken: Pricing?

    init(id: String, selected: Bool, pricingPerMToken: Pricing?) {
        self.id = id
        self.selected = selected
        self.pricingPerMToken = pricingPerMToken
    }

    enum CodingKeys: String, CodingKey {
        case id
        case selected
        case pricingPerMToken = "pricing_per_mtoken"
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

    private struct ReloadResponse: Decodable {
        let ok: Bool
    }
}
