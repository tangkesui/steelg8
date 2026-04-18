import Foundation

/// 每家 provider 的"常用模型"建议清单。Settings UI 的「添加模型」按钮按 provider
/// 名匹配到 ModelCatalog.suggestions(for:)，然后把这些候选做成下拉让用户一键追加。
///
/// 原则：
/// - 宁缺毋滥：只列真正在跑、厂商官方推荐的 id
/// - OpenRouter 这类聚合平台按受欢迎程度给 Top 10+
/// - 表是手动维护，model id 随时间漂移时靠代码 review 更新；不做运行时拉取
enum ModelCatalog {

    /// 一条建议，point 是 model id（写进 providers.json 的原样），label 用于展示。
    struct Suggestion: Identifiable, Hashable {
        let id: String   // = modelID，保证唯一可哈希
        let modelID: String
        let label: String
        let hint: String?  // 右侧灰字，如"中文文案主力"

        init(_ modelID: String, label: String? = nil, hint: String? = nil) {
            self.id = modelID
            self.modelID = modelID
            self.label = label ?? modelID
            self.hint = hint
        }
    }

    static func suggestions(for providerName: String) -> [Suggestion] {
        switch providerName.lowercased() {
        case "kimi":
            return kimi
        case "deepseek":
            return deepseek
        case "qwen":
            return qwen
        case "openrouter":
            return openrouter
        case "zhipu", "glm":
            return zhipu
        default:
            return []
        }
    }

    // MARK: - Kimi / Moonshot

    private static let kimi: [Suggestion] = [
        .init("kimi-k2-0905-preview", hint: "旗舰，中文文案主力"),
        .init("kimi-thinking-preview", hint: "推理增强"),
        .init("moonshot-v1-128k", hint: "128K 长上下文"),
        .init("moonshot-v1-32k"),
        .init("moonshot-v1-8k", hint: "便宜")
    ]

    // MARK: - DeepSeek

    private static let deepseek: [Suggestion] = [
        .init("deepseek-chat", hint: "通用对话，极便宜"),
        .init("deepseek-reasoner", hint: "推理模型 R1")
    ]

    // MARK: - Qwen (阿里百炼)

    private static let qwen: [Suggestion] = [
        .init("qwen3-max", hint: "最新旗舰"),
        .init("qwen-max", hint: "中文文案主力"),
        .init("qwen-plus", hint: "路由分拣主力"),
        .init("qwen-turbo", hint: "批量小任务"),
        .init("qwen-long", hint: "长上下文专版")
    ]

    // MARK: - OpenRouter Top N
    //
    // 根据 https://openrouter.ai/rankings 上常见的头部模型整理。
    // 价格差异大，不在这里标；点了有需要再去 OpenRouter 官网查。

    private static let openrouter: [Suggestion] = [
        .init("anthropic/claude-sonnet-4.5", label: "Claude Sonnet 4.5", hint: "综合最强候选"),
        .init("anthropic/claude-sonnet-4", label: "Claude Sonnet 4", hint: "稳定头部"),
        .init("anthropic/claude-opus-4", label: "Claude Opus 4", hint: "旗舰，贵"),
        .init("google/gemini-2.5-pro", label: "Gemini 2.5 Pro", hint: "1M token 长上下文"),
        .init("google/gemini-2.5-flash", label: "Gemini 2.5 Flash", hint: "便宜快"),
        .init("openai/gpt-4o", label: "GPT-4o", hint: "英文 + 工具调用"),
        .init("openai/gpt-4o-mini", label: "GPT-4o mini", hint: "便宜"),
        .init("openai/o1", label: "OpenAI o1", hint: "深度推理"),
        .init("x-ai/grok-4", label: "Grok 4", hint: "xAI 旗舰"),
        .init("deepseek/deepseek-v3", label: "DeepSeek V3", hint: "海外节点访问"),
        .init("deepseek/deepseek-r1", label: "DeepSeek R1", hint: "推理模型"),
        .init("meta-llama/llama-3.3-70b-instruct", label: "Llama 3.3 70B", hint: "开源旗舰"),
        .init("qwen/qwen3-max", label: "Qwen3 Max (海外)", hint: "阿里海外版"),
        .init("moonshotai/kimi-k2", label: "Kimi K2 (海外)", hint: "月之暗面海外版"),
        .init("mistralai/mistral-large-2411", label: "Mistral Large", hint: "欧洲"),
        .init("nousresearch/hermes-4-70b", label: "Hermes 4 70B", hint: "开源指令微调")
    ]

    // MARK: - 智谱 GLM（预留，用户若加 provider 名 "zhipu" / "glm" 即走这套）

    private static let zhipu: [Suggestion] = [
        .init("glm-4.6", hint: "最新"),
        .init("glm-4-plus"),
        .init("glm-4-flash", hint: "便宜")
    ]
}
