"""
steelg8 四层路由漏斗（MVP 版）
--------------------------------

产品设计方案 §6.4 · MVP 版：

  ① 规则 / 正则         （0 成本，覆盖明确意图）
  ② 语义路由 Embedding   （MVP 阶段 stub，接口留着，Phase 2 再接）
  ③ 廉价云模型分拣       （DeepSeek / Qwen-Plus，~¥1/M token）
  ④ 高能力云模型         （Kimi K2 / Qwen-Max / OpenRouter·GPT4 等）

本文件只做"决策"——根据输入的 message + 明确 hints，输出一个 RoutingDecision
（选哪个模型、为什么、途经哪一层）。实际调用上游由 server.py / agent.py 负责。

设计要点：
- 每一层都可能失败（未命中、未就绪），失败自动跳下一层
- 指定 model 的请求直接旁路到 ProviderRegistry（用户显式意图最大）
- registry.default_model 永远作为第 ③ 层失效时的 safety net
- tier（②层）标记为 not_implemented，router 会跳过；等 Phase 2 接 embedding 后启用
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import capabilities
from providers import ProviderRegistry


@dataclass
class RoutingDecision:
    model: str
    provider: str
    layer: str  # "explicit" | "rule" | "embedding" | "cheap" | "high" | "fallback" | "mock"
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "layer": self.layer,
            "reason": self.reason,
        }


# ---------- Layer 1: 规则 / 正则 ----------
#
# 极简的"关键词命中即选档"——覆盖日常的明显意图。
# 每条规则返回一个 *任务标签*，再由下面的 _pick_by_tag 去画像表里选最匹配的模型。

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # 事实核查 / 查资料 → 要求推理或 web（当前还没接 web，先挑推理强的）
    (re.compile(r"(查|搜索|最新|几年|政策|法规|新闻)", re.IGNORECASE), "reasoning"),
    # 路由 / 分拣 / 批量 / 翻译 → 便宜快的
    (re.compile(r"(路由|分类|标签|摘要|批量|翻译|trans[l]?ate)", re.IGNORECASE), "routing"),
    # 写稿 / 公文 / 汇报 / 文案 → 高质量中文模型
    (re.compile(r"(写|文案|公文|汇报|稿子|推文|文章|讲话|方案|演讲|报告)"), "writing"),
    # 推理 / 拆解 / 规划 → reasoning 强的
    (re.compile(r"(拆解|规划|思路|步骤|推理|分析)"), "reasoning"),
)


def _classify_by_rule(message: str) -> str | None:
    for pattern, tag in _RULES:
        if pattern.search(message):
            return tag
    return None


# ---------- Layer 2: 语义 Embedding（MVP 阶段 stub） ----------


def _classify_by_embedding(message: str) -> str | None:
    # Phase 2 接入云 Embedding（Jina/Qwen）后再实现：
    #   1) 把 message embedding 出来
    #   2) 与预置"任务原型向量"库做余弦相似度
    #   3) Top-1 相似度 > 阈值则返回对应 tag
    # 现在 MVP 阶段直接返回 None，让请求落到下一层。
    _ = message
    return None


# ---------- Layer 3/4 共用：按 tag 从画像表挑模型 ----------


def _pick_by_tag(tag: str, registry: ProviderRegistry, prefer_cheap: bool) -> tuple[str, str] | None:
    """在画像表里找带 tag 的模型，优先命中已就绪的 provider；返回 (model, provider)。"""
    if prefer_cheap:
        profile = capabilities.cheapest_with_tag(tag)
    else:
        profile = capabilities.best_for_tag(tag, dimension="chinese_writing")

    # 如果最优模型的 provider 没就绪，退而求其次：在该 tag 的所有候选里找 ready 的
    candidates = [p for p in capabilities.all_profiles() if p.matches_tag(tag)]
    cost_order = {"free": 0, "cheap": 1, "mid": 2, "high": 3}
    if prefer_cheap:
        candidates.sort(key=lambda p: cost_order.get(p.cost_tier, 99))
    else:
        candidates.sort(key=lambda p: -p.chinese_writing)

    # 首选：profile 优先（如果就绪）
    ordered = ([profile] if profile else []) + [c for c in candidates if c is not profile]

    for prof in ordered:
        if prof is None:
            continue
        provider = registry.providers.get(prof.provider)
        if provider and provider.is_ready():
            return prof.model, prof.provider

    return None


# ---------- 主入口 ----------


def route(
    message: str,
    registry: ProviderRegistry,
    *,
    explicit_model: str | None = None,
) -> RoutingDecision:
    """决定这条消息走哪个模型。永远返回一个 RoutingDecision（最差会是 mock）。"""

    # 0. 显式指定：用户/UI 的显式意图最高
    if explicit_model:
        resolved = registry.resolve(explicit_model)
        if resolved:
            provider, model = resolved
            return RoutingDecision(
                model=model or explicit_model,
                provider=provider.name,
                layer="explicit",
                reason=f"调用方显式指定 model={explicit_model}",
            )
        # 指定了但没命中，不继续猜：降级到默认模型
        if registry.default_model:
            resolved = registry.resolve(registry.default_model)
            if resolved:
                provider, model = resolved
                return RoutingDecision(
                    model=model or registry.default_model,
                    provider=provider.name,
                    layer="fallback",
                    reason=f"显式 model={explicit_model} 未匹配到 provider，回退 default",
                )

    # 1. 规则层
    tag = _classify_by_rule(message)
    if tag:
        # 写稿/推理类用 high-tier，路由/分拣类用 cheap
        prefer_cheap = tag in {"routing", "batch", "cheap"}
        picked = _pick_by_tag(tag, registry, prefer_cheap=prefer_cheap)
        if picked:
            model, provider = picked
            return RoutingDecision(
                model=model,
                provider=provider,
                layer="rule" if prefer_cheap else "high",
                reason=f"规则命中 tag={tag}",
            )

    # 2. Embedding 层（当前 stub）
    tag = _classify_by_embedding(message)
    if tag:
        picked = _pick_by_tag(tag, registry, prefer_cheap=False)
        if picked:
            model, provider = picked
            return RoutingDecision(
                model=model, provider=provider, layer="embedding",
                reason=f"Embedding 命中 tag={tag}",
            )

    # 3. 廉价云兜底：任何带 "routing"/"cheap" 标签的就绪模型
    picked = _pick_by_tag("cheap", registry, prefer_cheap=True) \
        or _pick_by_tag("routing", registry, prefer_cheap=True)
    if picked:
        model, provider = picked
        return RoutingDecision(
            model=model,
            provider=provider,
            layer="cheap",
            reason="未命中规则，走廉价兜底",
        )

    # 4. registry 里第一个 ready 的（最终兜底，等价于以前的 first_ready）
    ready = registry.first_ready()
    if ready:
        provider, model = ready
        return RoutingDecision(
            model=model or registry.default_model or "",
            provider=provider.name,
            layer="fallback",
            reason="没有画像匹配且廉价层缺席，用第一个就绪 provider 兜底",
        )

    # 5. 彻底没 provider：mock
    return RoutingDecision(
        model="mock-local",
        provider="",
        layer="mock",
        reason="没有任何 provider 就绪，走 mock",
    )
