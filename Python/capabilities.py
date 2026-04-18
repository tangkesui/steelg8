"""
steelg8 模型能力画像（model capability profiles）
-------------------------------------------------

按产品设计方案 §6.5 把每个模型按维度打标签，供 router 在路由时挑选。

维度说明：
- chinese_writing / english_writing：1~5 星，文案质量
- reasoning：1~5 星，推理能力（拆解/规划/事实核查）
- context_tokens：最大上下文窗口（token）
- cost_tier：成本档位 "free" / "cheap" / "mid" / "high"
- tool_use：是否擅长稳定的 tool/function calling
- latency_tier：延迟档位 "fast" / "normal" / "slow"
- tags：自由标签，router 可做启发式匹配（routing/writing/batch/reasoning/...）

每个模型还挂一个 provider 名（与 providers.json 的 key 对齐），router
拿到模型名后先来这里查画像、再去 ProviderRegistry 取 base_url/api_key。

注意：画像是"先验知识"，只给路由做决策依据；实际能力随时间会变，
所以把表格单独拎出来方便手动迭代。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


CostTier = Literal["free", "cheap", "mid", "high"]
LatencyTier = Literal["fast", "normal", "slow"]


@dataclass(frozen=True)
class ModelProfile:
    model: str
    provider: str
    chinese_writing: int = 3
    english_writing: int = 3
    reasoning: int = 3
    context_tokens: int = 32_000
    cost_tier: CostTier = "mid"
    tool_use: bool = True
    latency_tier: LatencyTier = "normal"
    tags: tuple[str, ...] = field(default_factory=tuple)

    def matches_tag(self, tag: str) -> bool:
        return tag in self.tags


# 按设计方案 §6.5 的画像表落到代码。星级全部用 1~5 的整型方便排序。
# 备注：Claude 系列按 ADR-010 明确不内置，这里不登记。
PROFILES: tuple[ModelProfile, ...] = (
    # --- Kimi（2026 旗舰是 k2.5）---
    ModelProfile(
        model="kimi-k2.5",
        provider="kimi",
        chinese_writing=5,
        english_writing=4,
        reasoning=5,
        context_tokens=256_000,
        cost_tier="mid",
        tool_use=True,
        tags=("writing", "chinese", "long-form", "reasoning", "multimodal", "agent"),
    ),
    ModelProfile(
        model="kimi-k2",
        provider="kimi",
        chinese_writing=5,
        english_writing=3,
        reasoning=4,
        context_tokens=128_000,
        cost_tier="mid",
        tags=("writing", "chinese", "long-form"),
    ),
    ModelProfile(
        model="moonshot-v1-128k",
        provider="kimi",
        chinese_writing=4,
        english_writing=3,
        reasoning=4,
        context_tokens=128_000,
        cost_tier="mid",
        tags=("writing", "long-context"),
    ),
    ModelProfile(
        model="moonshot-v1-32k",
        provider="kimi",
        chinese_writing=4,
        english_writing=3,
        reasoning=3,
        context_tokens=32_000,
        cost_tier="cheap",
        tags=("writing",),
    ),
    ModelProfile(
        model="moonshot-v1-8k",
        provider="kimi",
        chinese_writing=4,
        english_writing=3,
        reasoning=3,
        context_tokens=8_000,
        cost_tier="cheap",
        tags=("writing", "cheap"),
    ),
    # --- Qwen ---
    ModelProfile(
        model="qwen-max",
        provider="bailian",
        chinese_writing=4,
        english_writing=3,
        reasoning=4,
        context_tokens=32_000,
        cost_tier="mid",
        tags=("writing", "official-doc", "chinese"),
    ),
    ModelProfile(
        model="qwen-plus",
        provider="bailian",
        chinese_writing=3,
        english_writing=3,
        reasoning=3,
        context_tokens=128_000,
        cost_tier="cheap",
        latency_tier="fast",
        tags=("routing", "batch", "cheap"),
    ),
    ModelProfile(
        model="qwen-turbo",
        provider="bailian",
        chinese_writing=3,
        english_writing=2,
        reasoning=2,
        context_tokens=32_000,
        cost_tier="cheap",
        latency_tier="fast",
        tags=("routing", "batch"),
    ),
    # --- DeepSeek ---
    ModelProfile(
        model="deepseek-chat",
        provider="deepseek",
        chinese_writing=3,
        english_writing=3,
        reasoning=3,
        context_tokens=64_000,
        cost_tier="cheap",
        latency_tier="fast",
        tags=("routing", "batch", "cheap"),
    ),
    ModelProfile(
        model="deepseek-reasoner",
        provider="deepseek",
        chinese_writing=3,
        english_writing=3,
        reasoning=5,
        context_tokens=64_000,
        cost_tier="mid",
        latency_tier="slow",
        tags=("reasoning", "planning"),
    ),
    # --- OpenRouter（模型比武/国际模型）
    # 注意：OpenRouter 接收的 model id 不带 "openrouter/" 前缀，直接是
    # "anthropic/claude-sonnet-4" / "google/gemini-2.5-pro" 这种 vendor/model 形式
    ModelProfile(
        model="anthropic/claude-sonnet-4",
        provider="openrouter",
        chinese_writing=4,
        english_writing=5,
        reasoning=5,
        context_tokens=200_000,
        cost_tier="high",
        tags=("writing", "reasoning", "english"),
    ),
    ModelProfile(
        model="google/gemini-2.5-pro",
        provider="openrouter",
        chinese_writing=4,
        english_writing=4,
        reasoning=4,
        context_tokens=1_000_000,
        cost_tier="mid",
        tags=("long-context", "multimodal"),
    ),
    ModelProfile(
        model="openai/gpt-4o",
        provider="openrouter",
        chinese_writing=4,
        english_writing=5,
        reasoning=4,
        context_tokens=128_000,
        cost_tier="high",
        tags=("writing", "english", "tool-use"),
    ),
    ModelProfile(
        model="x-ai/grok-2-1212",
        provider="openrouter",
        chinese_writing=3,
        english_writing=4,
        reasoning=3,
        context_tokens=128_000,
        cost_tier="mid",
        tags=("english",),
    ),
)


_BY_MODEL: dict[str, ModelProfile] = {p.model: p for p in PROFILES}


def get(model: str) -> ModelProfile | None:
    """按精确模型名查画像。找不到返回 None（router 会走启发式兜底）。"""
    return _BY_MODEL.get(model)


def by_provider(provider: str) -> list[ModelProfile]:
    return [p for p in PROFILES if p.provider == provider]


def all_profiles() -> list[ModelProfile]:
    return list(PROFILES)


def cheapest_with_tag(tag: str) -> ModelProfile | None:
    """找带某个 tag 的最便宜模型，成本档排序：free < cheap < mid < high。"""
    cost_order = {"free": 0, "cheap": 1, "mid": 2, "high": 3}
    candidates = [p for p in PROFILES if p.matches_tag(tag)]
    if not candidates:
        return None
    candidates.sort(key=lambda p: cost_order.get(p.cost_tier, 99))
    return candidates[0]


def best_for_tag(tag: str, dimension: str = "chinese_writing") -> ModelProfile | None:
    """按某个维度（默认中文文案质量）找 tag 下评分最高的模型。"""
    candidates = [p for p in PROFILES if p.matches_tag(tag)]
    if not candidates:
        return None
    candidates.sort(key=lambda p: getattr(p, dimension, 0), reverse=True)
    return candidates[0]
