"""
模型计费表（USD per 1M tokens）
--------------------------------

单位：USD / 1M token，(input, output) 元组。

来源优先级：
- OpenRouter 的 /api/v1/models 公开页面为准
- 直连 provider 的官方定价（DeepSeek/Kimi/Qwen 官方文档）
- 人民币价按 1 USD ≈ 7.2 CNY 换算

注意：
- 价格会变，表过期时手动改这里即可；不做运行时拉取（稳定、可审计）
- OpenRouter 会在 response body 里带 `usage` 和有些模型带 `cost` 字段。
  本表作为兜底；若 response 里有 usage 金额，优先用 response 的
- 没登记的 model → 回退到 provider 默认档（在 provider_default 里）
- `:free` 后缀的 OpenRouter 模型一律按 (0, 0) 处理
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Price:
    input_per_1m: float   # USD / 1M input tokens
    output_per_1m: float  # USD / 1M output tokens

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """给定 token 数，返回 USD 成本（浮点，后续 UI 自己格式化）。"""
        return (
            prompt_tokens * self.input_per_1m / 1_000_000
            + completion_tokens * self.output_per_1m / 1_000_000
        )


# 数值都是 USD 每百万 token，(input, output)
# 按 provider 分块，便于维护
PRICING: dict[str, Price] = {
    # ---- OpenRouter / Anthropic ----
    "anthropic/claude-sonnet-4.5":  Price(3.00, 15.00),
    "anthropic/claude-sonnet-4":    Price(3.00, 15.00),
    "anthropic/claude-opus-4":      Price(15.00, 75.00),
    "anthropic/claude-3.5-sonnet":  Price(3.00, 15.00),
    "anthropic/claude-3.5-haiku":   Price(0.80, 4.00),

    # ---- OpenRouter / Google ----
    "google/gemini-2.5-pro":        Price(1.25, 10.00),
    "google/gemini-2.5-flash":      Price(0.30, 2.50),
    "google/gemini-2.5-flash-lite": Price(0.10, 0.40),
    "google/gemini-flash-1.5":      Price(0.075, 0.30),
    "google/gemini-flash-1.5-8b":   Price(0.0375, 0.15),

    # ---- OpenRouter / OpenAI ----
    "openai/gpt-4o":                Price(2.50, 10.00),
    "openai/gpt-4o-mini":           Price(0.15, 0.60),
    "openai/o1":                    Price(15.00, 60.00),
    "openai/o1-mini":               Price(3.00, 12.00),

    # ---- OpenRouter / xAI ----
    "x-ai/grok-4":                  Price(5.00, 15.00),
    "x-ai/grok-2-1212":             Price(2.00, 10.00),

    # ---- OpenRouter / DeepSeek 海外节点 ----
    "deepseek/deepseek-v3":         Price(0.28, 0.88),
    "deepseek/deepseek-r1":         Price(0.55, 2.19),
    "deepseek/deepseek-chat":       Price(0.28, 0.88),

    # ---- OpenRouter / Meta ----
    "meta-llama/llama-3.3-70b-instruct": Price(0.13, 0.40),

    # ---- OpenRouter / Qwen 海外 ----
    "qwen/qwen3-max":               Price(1.40, 5.60),
    "qwen/qwen-plus":               Price(0.40, 1.20),

    # ---- OpenRouter / 其它 ----
    "moonshotai/kimi-k2":           Price(0.60, 2.50),
    "mistralai/mistral-large-2411": Price(2.00, 6.00),
    "nousresearch/hermes-4-70b":    Price(0.80, 2.80),

    # ---- DeepSeek 官方直连（USD 换算，官方价格按月有折扣，这里取标牌）----
    "deepseek-chat":      Price(0.27, 1.10),
    "deepseek-reasoner":  Price(0.55, 2.19),

    # ---- Kimi 官方直连（¥/M 换算到 USD，1 USD≈7.2 CNY） ----
    "moonshot-v1-8k":          Price(1.67, 1.67),   # ¥12/M
    "moonshot-v1-32k":         Price(3.33, 3.33),   # ¥24/M
    "moonshot-v1-128k":        Price(8.33, 8.33),   # ¥60/M
    "kimi-k2-0905-preview":    Price(0.56, 2.22),   # ¥4/M / ¥16/M
    "kimi-thinking-preview":   Price(4.17, 4.17),

    # ---- Qwen 百炼直连（¥/M 换算到 USD） ----
    "qwen-turbo":     Price(0.042, 0.125),  # ¥0.3/M / ¥0.9/M
    "qwen-plus":      Price(0.11, 0.33),    # ¥0.8/M / ¥2.4/M
    "qwen-max":       Price(0.33, 1.33),    # ¥2.4/M / ¥9.6/M
    "qwen3-max":      Price(0.33, 1.33),
    "qwen-long":      Price(0.069, 0.11),   # 长上下文特价
}

# provider 级兜底档（model 未命中时用）
PROVIDER_DEFAULT: dict[str, Price] = {
    "openrouter": Price(1.00, 3.00),   # 偏保守中档估价
    "kimi":       Price(1.67, 1.67),
    "deepseek":   Price(0.27, 1.10),
    "qwen":       Price(0.11, 0.33),
}


def lookup(model: str, provider: str = "") -> Price:
    """按 model → provider 兜底的顺序查价。未命中返回 0 价（免费/未知）。

    `:free` 后缀一律 0。
    """
    if not model:
        return PROVIDER_DEFAULT.get(provider, Price(0.0, 0.0))

    # OpenRouter 的 :free 系列
    if model.endswith(":free"):
        return Price(0.0, 0.0)

    # 直接命中
    if model in PRICING:
        return PRICING[model]

    # 有些 openrouter 会带版本后缀，比如 "anthropic/claude-sonnet-4.5:thinking"
    # 剥离 ":xxx" 再试一次
    if ":" in model:
        base = model.rsplit(":", 1)[0]
        if base in PRICING:
            return PRICING[base]

    return PROVIDER_DEFAULT.get(provider, Price(0.0, 0.0))


def cost_usd(
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    return lookup(model, provider).cost(prompt_tokens, completion_tokens)


# 简易 USD→CNY 常量（UI 显示双币种用，不做实时汇率）
USD_TO_CNY = 7.2


def cny(usd: float) -> float:
    return usd * USD_TO_CNY
