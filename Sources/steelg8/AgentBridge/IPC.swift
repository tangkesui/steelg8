import Foundation

struct AgentChatRequest: Encodable {
    let message: String
    let model: String?
}

struct AgentChatResponse: Decodable {
    let content: String
    let model: String
    let source: String
    let soulSummary: String?
}

@MainActor
final class AgentBridge {
    private let runtime: PythonRuntime

    init(runtime: PythonRuntime) {
        self.runtime = runtime
    }

    func chat(message: String, model: String?) async throws -> AgentChatResponse {
        try runtime.bootstrapSoulFileIfNeeded()
        try await runtime.startIfNeeded()

        let endpoint = runtime.baseURL.appending(path: "chat")
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 15
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(
            AgentChatRequest(message: message, model: model)
        )

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse,
              (200..<300).contains(httpResponse.statusCode) else {
            throw URLError(.badServerResponse)
        }

        return try JSONDecoder().decode(AgentChatResponse.self, from: data)
    }
}
