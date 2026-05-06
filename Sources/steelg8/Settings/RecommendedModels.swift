import Foundation

/// 与 Python/recommended_models.py 手动同步。
/// 12.5 先保留 Swift 端常量，后续如 drift 明显再改成 kernel GET 端点。
enum RecommendedModelsClient {
    private static let recommended: [String: [String]] = [
        "kimi": [
            "kimi-k2-thinking",
            "kimi-k2-0905-preview",
            "moonshot-v1-128k",
        ],
        "deepseek": [
            "deepseek-chat",
            "deepseek-reasoner",
        ],
        "bailian": [
            "qwen-plus",
            "qwen-max",
            "qwen-long",
        ],
        "openrouter": [
            "anthropic/claude-sonnet-4.5",
            "google/gemini-2.5-flash",
            "openai/gpt-4o-mini",
            "deepseek/deepseek-v3",
        ],
    ]

    static func forProvider(_ providerID: String) -> [String] {
        recommended[providerID] ?? []
    }
}
