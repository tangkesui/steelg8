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
- 没登记的 model → 返回 None（不再用 provider 平均档撒谎）。catalog 把 pricing 置 null，UI 显示"—"
- `:free` 后缀的 OpenRouter 模型一律按 (0, 0) 处理

历史：
- 旧版本对未知 model 返 PROVIDER_DEFAULT[provider] 平均档，但这导致百炼整片错价
  （bailian 全用 0.11/0.33 兜底）。2026-05-08 改成"未知就返 None"，由 catalog
  refresh 把 pricing_source=fallback + null 写进 catalog；用户在「模型管理」页
  可手填把 source 升到 verified。
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
    # 注：Gemini 1.5 系列已被 OpenRouter 下架（2025Q4），仅保留 ref 不再推荐

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
    "kimi-k2.5":               Price(0.83, 3.33),   # ¥6/M input, ¥24/M output (2026 旗舰)
    "kimi-k2-0905-preview":    Price(0.56, 2.22),   # ¥4/M / ¥16/M
    "kimi-thinking-preview":   Price(4.17, 4.17),
    "moonshot-v1-8k":          Price(1.67, 1.67),   # ¥12/M
    "moonshot-v1-32k":         Price(3.33, 3.33),   # ¥24/M
    "moonshot-v1-128k":        Price(8.33, 8.33),   # ¥60/M

    # ---- Qwen 百炼直连（¥/M 换算到 USD） ----
    "qwen-turbo":     Price(0.042, 0.125),  # ¥0.3/M / ¥0.9/M
    "qwen-plus":      Price(0.11, 0.33),    # ¥0.8/M / ¥2.4/M
    "qwen-max":       Price(0.33, 1.33),    # ¥2.4/M / ¥9.6/M
    "qwen3-max":      Price(0.33, 1.33),
    "qwen-long":      Price(0.069, 0.11),   # 长上下文特价

    # ---- Qwen 3.5 / 3.6 系列（百炼，2026 上线，¥→USD@7.2）----
    # 估价：旗舰按 max 档、中档按 plus 档、轻量按 turbo 档；带日期版本走 base id 同价
    "qwen3.5-plus":            Price(0.14, 0.42),
    "qwen3.6-max-preview":     Price(0.42, 1.67),
    "qwen3.6-flash":           Price(0.06, 0.17),
    "qwen3.6-27b":             Price(0.14, 0.42),
    "qwen3.6-35b-a3b":         Price(0.21, 0.83),

    # ---- Kimi 新版本 ----
    "kimi-k2.6":               Price(0.83, 3.33),

    # ---- DeepSeek v4（百炼代理 / 直连同价）----
    "deepseek-v4-flash":  Price(0.069, 0.28),
    "deepseek-v4-pro":    Price(0.28, 1.10),

    # ---- Embedding / Rerank（input-only，output_per_1m=0；2026-01 训练数据时点）----
    # 百炼 text-embedding-v1/v2/v3/v4：¥0.7/Mtok input ÷ 7.2 ≈ $0.097
    "text-embedding-v1":  Price(0.097, 0.0),
    "text-embedding-v2":  Price(0.097, 0.0),
    "text-embedding-v3":  Price(0.097, 0.0),
    "text-embedding-v4":  Price(0.097, 0.0),
    # 百炼 rerank：¥0.8/Mtok ≈ $0.111
    "gte-rerank":     Price(0.111, 0.0),
    "gte-rerank-v2":  Price(0.111, 0.0),
    "qwen3-rerank":   Price(0.111, 0.0),
    # OpenAI embedding（platform.openai.com pricing）
    "text-embedding-3-small": Price(0.02, 0.0),
    "text-embedding-3-large": Price(0.13, 0.0),
    "text-embedding-ada-002": Price(0.10, 0.0),
}


def _strip_date_suffix(model: str) -> str:
    """去掉模型 id 末尾的 -YYYY-MM-DD 日期版本号，便于命中 base id。
    例：'qwen3.5-plus-2026-04-20' → 'qwen3.5-plus'
    """
    parts = model.rsplit("-", 3)
    if (
        len(parts) == 4
        and len(parts[1]) == 4 and parts[1].isdigit()
        and len(parts[2]) == 2 and parts[2].isdigit()
        and len(parts[3]) == 2 and parts[3].isdigit()
    ):
        return parts[0]
    return model


def lookup(model: str, provider: str = "") -> Optional[Price]:  # noqa: UP007
    """按 model → 去日期后缀 → 去 :tag 后缀 顺序查价。
    没命中返回 None（不再用 provider 平均档撒谎）；调用方按 None 处理。

    `:free` 后缀一律返 (0, 0)。
    `provider` 形参保留以兼容旧调用，但**不再**用作兜底。
    """
    if not model:
        return None

    # OpenRouter 的 :free 系列
    if model.endswith(":free"):
        return Price(0.0, 0.0)

    # 直接命中
    if model in PRICING:
        return PRICING[model]

    # bailian 代理形态（catalog 里偶尔出现）：'kimi/kimi-k2.6' → 取斜杠后段试
    if "/" in model:
        tail = model.rsplit("/", 1)[1]
        if tail in PRICING:
            return PRICING[tail]

    # 剥离 ":xxx" 再试（OpenRouter 偶有 ':thinking' 这种版本后缀）
    if ":" in model:
        base = model.rsplit(":", 1)[0]
        if base in PRICING:
            return PRICING[base]

    # 剥离日期版本号
    base = _strip_date_suffix(model)
    if base != model and base in PRICING:
        return PRICING[base]

    return None


def cost_usd(
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """计费路径。命中静态表用对应价；未命中按 0 计（不撒谎，宁可 underbill 也不 overbill）。"""
    p = lookup(model, provider)
    if p is None:
        return 0.0
    return p.cost(prompt_tokens, completion_tokens)


# 简易 USD→CNY 常量（UI 显示双币种用，不做实时汇率）
USD_TO_CNY = 7.2


def cny(usd: float) -> float:
    return usd * USD_TO_CNY
