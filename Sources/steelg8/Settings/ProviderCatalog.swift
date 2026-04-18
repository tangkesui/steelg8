import Foundation

/// steelg8 内置"供应商市场"：国内外主流 LLM 提供商的基础信息，
/// 给 Settings 的「+ 添加供应商」按钮做下拉用。
///
/// 每条 PresetProvider 带着 base_url、env 变量名、推荐模型、注册链接，
/// 用户挑完只需要粘 key 就能用。
///
/// 更新策略：随时手动改。价格 / model id 变了就来这里同步。
enum ProviderCatalog {

    struct Preset: Identifiable, Equatable {
        let id: String        // 内部 key（与 providers.json 的键对齐）
        let name: String      // 显示名
        let baseURL: String
        let apiKeyEnv: String
        let signupURL: String
        let blurb: String     // 一句话说明
        let defaultModels: [String]  // 建议第一次加上的 model id
    }

    /// 默认推荐顺序：国内便宜的放前面
    static let all: [Preset] = [
        .init(
            id: "bailian",
            name: "阿里百炼（Qwen / DashScope）",
            baseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1",
            apiKeyEnv: "BAILIAN_API_KEY",
            signupURL: "https://bailian.console.aliyun.com",
            blurb: "Qwen 全家桶 + embedding + rerank。500K ~ 1M 免费额度，¥0.7/M 起。",
            defaultModels: ["qwen-plus", "qwen-max", "text-embedding-v3"]
        ),
        .init(
            id: "deepseek",
            name: "DeepSeek",
            baseURL: "https://api.deepseek.com",
            apiKeyEnv: "DEEPSEEK_API_KEY",
            signupURL: "https://platform.deepseek.com",
            blurb: "推理便宜好用，deepseek-chat ¥0.5/M，reasoner 偏推理。",
            defaultModels: ["deepseek-chat", "deepseek-reasoner"]
        ),
        .init(
            id: "kimi",
            name: "Kimi / Moonshot",
            baseURL: "https://api.moonshot.cn/v1",
            apiKeyEnv: "KIMI_API_KEY",
            signupURL: "https://platform.moonshot.cn",
            blurb: "中文文案主力，长上下文。Coding Plan 附 API 额度。",
            defaultModels: ["kimi-k2-0905-preview", "moonshot-v1-32k", "moonshot-v1-128k"]
        ),
        .init(
            id: "zhipu",
            name: "智谱 GLM",
            baseURL: "https://open.bigmodel.cn/api/paas/v4",
            apiKeyEnv: "ZHIPU_API_KEY",
            signupURL: "https://open.bigmodel.cn",
            blurb: "GLM-4.6，中文底子扎实，免费额度 + 按量。",
            defaultModels: ["glm-4.6", "glm-4-plus", "glm-4-flash"]
        ),
        .init(
            id: "doubao",
            name: "字节豆包（火山引擎）",
            baseURL: "https://ark.cn-beijing.volces.com/api/v3",
            apiKeyEnv: "DOUBAO_API_KEY",
            signupURL: "https://www.volcengine.com/product/ark",
            blurb: "豆包系列；需要在火山方舟创建推理接入点，拿 endpoint id 当 model。",
            defaultModels: ["doubao-seed-1-6-250615", "doubao-seed-1-6-flash-250615"]
        ),
        .init(
            id: "stepfun",
            name: "阶跃星辰",
            baseURL: "https://api.stepfun.com/v1",
            apiKeyEnv: "STEPFUN_API_KEY",
            signupURL: "https://platform.stepfun.com",
            blurb: "step-2 质量稳，有多模态 step-1v。",
            defaultModels: ["step-2-16k", "step-1v-8k"]
        ),
        .init(
            id: "yi",
            name: "零一万物（01.AI / Yi）",
            baseURL: "https://api.lingyiwanwu.com/v1",
            apiKeyEnv: "YI_API_KEY",
            signupURL: "https://platform.lingyiwanwu.com",
            blurb: "yi-lightning 极便宜，yi-large 旗舰。",
            defaultModels: ["yi-lightning", "yi-large"]
        ),
        .init(
            id: "minimax",
            name: "MiniMax",
            baseURL: "https://api.minimax.chat/v1",
            apiKeyEnv: "MINIMAX_API_KEY",
            signupURL: "https://api.minimax.chat",
            blurb: "MiniMax-M2 新旗舰，支持长上下文 + 语音。",
            defaultModels: ["MiniMax-M2", "abab7-chat-preview"]
        ),
        .init(
            id: "siliconflow",
            name: "硅基流动（SiliconFlow）",
            baseURL: "https://api.siliconflow.cn/v1",
            apiKeyEnv: "SILICONFLOW_API_KEY",
            signupURL: "https://cloud.siliconflow.cn",
            blurb: "开源模型聚合，Qwen / DeepSeek / Llama / bge 都能按量调。",
            defaultModels: ["Qwen/Qwen3-32B", "deepseek-ai/DeepSeek-V3"]
        ),
        .init(
            id: "openrouter",
            name: "OpenRouter（国际聚合）",
            baseURL: "https://openrouter.ai/api/v1",
            apiKeyEnv: "OPENROUTER_API_KEY",
            signupURL: "https://openrouter.ai",
            blurb: "GPT/Claude/Gemini/Grok 一把梭，美元按量付费。",
            defaultModels: ["google/gemini-2.5-flash-lite", "anthropic/claude-sonnet-4"]
        ),
    ]

    static func preset(by id: String) -> Preset? {
        all.first { $0.id == id.lowercased() }
    }
}
