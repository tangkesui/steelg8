"""
推荐模型清单（Phase 12.4）
--------------------------

仓库提供的"初始草稿"，覆盖 `providers.example.json` 里 4 个内置 provider。

用途：
- 12.5 供应商页"应用推荐"按钮的数据源
- catalog 文件不存在 / 为空时的初始 fallback（仅由 UI 主动触发，**不**在 catalog_refresh
  里自动 seeding，避免覆盖用户已 unselected 的模型）

挑选原则（每个 provider 3-4 条，足够日常 + 留扩展余地，用户可自行勾选）：
- 旗舰（chat / reasoning）+ 性价比 + 长上下文 + 工具档
- 不放小众或刚发布未经过验证的型号
- model id 与 provider 直连接口实际可用的 id 一致；已交叉对照 `pricing.py`

字段保持纯字符串列表；不要塞对象 / 别名 / 描述（描述放在注释，便于后续手动改）。
"""

from __future__ import annotations


RECOMMENDED: dict[str, list[str]] = {
    # Moonshot 直连。
    # - kimi-k2-thinking：当前主力 reasoning 档（与 model_catalog.example 对齐）
    # - kimi-k2-0905-preview：性价比通用
    # - moonshot-v1-128k：长上下文兜底
    "kimi": [
        "kimi-k2-thinking",
        "kimi-k2-0905-preview",
        "moonshot-v1-128k",
    ],
    # DeepSeek 官方直连。
    # - deepseek-chat：通用 / 工具调用
    # - deepseek-reasoner：reasoning（含 reasoning_content）
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
    ],
    # 阿里百炼（OpenAI 兼容模式）。注意 provider id 是 "bailian" 不是 "qwen"。
    # - qwen-plus：性价比通用
    # - qwen-max：旗舰
    # - qwen-long：长上下文特价
    "bailian": [
        "qwen-plus",
        "qwen-max",
        "qwen-long",
    ],
    # OpenRouter 海外聚合。挑跨厂商的不同档位，避免清单全是单家。
    # - anthropic/claude-sonnet-4.5：旗舰中档（写作 + 工具）
    # - google/gemini-2.5-flash：廉价快档
    # - openai/gpt-4o-mini：兼容性 + 工具调用
    # - deepseek/deepseek-v3：性价比通用兜底
    "openrouter": [
        "anthropic/claude-sonnet-4.5",
        "google/gemini-2.5-flash",
        "openai/gpt-4o-mini",
        "deepseek/deepseek-v3",
    ],
}


def for_provider(provider_id: str) -> list[str]:
    """返回 provider 的推荐模型 id 列表；未知 provider 返回空列表。"""
    return list(RECOMMENDED.get(provider_id, []))


def known_providers() -> list[str]:
    """已收录推荐清单的 provider id（按字母序）。"""
    return sorted(RECOMMENDED.keys())
