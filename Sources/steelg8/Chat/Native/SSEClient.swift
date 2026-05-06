import Foundation

// SSE 事件类型
enum SSEEventType: String {
    case conversation
    case meta
    case rag
    case toolStart = "tool_start"
    case toolResult = "tool_result"
    case delta
    case usage
    case error
    case done
}

struct SSEEvent {
    let type: SSEEventType
    let raw: [String: Any]

    var conversationId: Int? { raw["conversationId"] as? Int }
    var content: String? { raw["content"] as? String }
    var full: String? { raw["full"] as? String }
    var errorMessage: String? { raw["error"] as? String }
    var decision: [String: Any]? { raw["decision"] as? [String: Any] }
    var usageDict: [String: Any]? { raw["usage"] as? [String: Any] }
    var costUsd: Double? { raw["costUsd"] as? Double }
    var ragHits: [[String: Any]]? { raw["hits"] as? [[String: Any]] }
    var toolId: String? { raw["id"] as? String }
    var toolName: String? { raw["name"] as? String }
    var toolArgs: [String: Any]? { raw["args"] as? [String: Any] }
    var toolResultDict: [String: Any]? { raw["result"] as? [String: Any] }
    var source: String? { raw["source"] as? String }
}

enum SSEClientError: LocalizedError {
    case badStatus(Int)

    var errorDescription: String? {
        if case .badStatus(let code) = self { return "HTTP \(code)" }
        return nil
    }
}

struct SSEClient {
    // 返回异步事件流。调用方负责 cancel task 来停止流。
    static func stream(request: URLRequest) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (asyncBytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        continuation.finish(throwing: URLError(.badServerResponse))
                        return
                    }
                    guard (200..<300).contains(http.statusCode) else {
                        continuation.finish(throwing: SSEClientError.badStatus(http.statusCode))
                        return
                    }

                    for try await line in asyncBytes.lines {
                        if Task.isCancelled { break }
                        guard line.hasPrefix("data:") else { continue }
                        let data = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        guard !data.isEmpty,
                              let jsonData = data.data(using: .utf8),
                              let obj = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
                              let typeStr = obj["type"] as? String,
                              let type = SSEEventType(rawValue: typeStr)
                        else { continue }

                        continuation.yield(SSEEvent(type: type, raw: obj))
                        if type == .done { break }
                    }
                    continuation.finish()
                } catch {
                    if Task.isCancelled {
                        continuation.finish()
                    } else {
                        continuation.finish(throwing: error)
                    }
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
