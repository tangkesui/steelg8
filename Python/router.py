"""
steelg8 模型路由（v0.3 简化版）
-------------------------------

之前有一套四层漏斗（规则 / embedding / 廉价兜底 / 高能模型）。用户反馈："我不
需要这个自动编排逻辑，宁可后期用 Dify 做自定义工作流。"合理。多数场景下
用户要么显式选模型（对话框右上 dropdown），要么就走 default_model，不需要系
统替他猜。

现在的行为：
  1. 调用方给了 explicit_model 且能 resolve → 用它（layer=explicit）
  2. 否则走 default_model（layer=default）
  3. 上面两步都不行 → 用第一个就绪 provider 兜底（layer=fallback）
  4. 完全没 provider → mock（layer=mock）

capabilities.py 保留只用来给 pricing / 画像（tool-use / context 等），不再做路由决策。
如果以后要回"智能路由"，从 git 里把这个文件拉回旧版即可。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from providers import ProviderRegistry


@dataclass
class RoutingDecision:
    model: str
    provider: str
    layer: str  # "explicit" | "default" | "fallback" | "mock"
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "layer": self.layer,
            "reason": self.reason,
        }


def route(
    message: str,                   # noqa: ARG001 保留接口以备将来重启智能路由
    registry: ProviderRegistry,
    *,
    explicit_model: str | None = None,
) -> RoutingDecision:
    # 1. 显式
    if explicit_model:
        resolved = registry.resolve(explicit_model)
        if resolved:
            provider, model = resolved
            return RoutingDecision(
                model=model or explicit_model,
                provider=provider.name,
                layer="explicit",
                reason=f"显式选了 {explicit_model}",
            )

    # 2. default_model
    if registry.default_model:
        resolved = registry.resolve(registry.default_model)
        if resolved:
            provider, model = resolved
            return RoutingDecision(
                model=model or registry.default_model,
                provider=provider.name,
                layer="default",
                reason=f"使用 default_model={registry.default_model}",
            )

    # 3. 第一个就绪
    ready = registry.first_ready()
    if ready:
        provider, model = ready
        return RoutingDecision(
            model=model or "",
            provider=provider.name,
            layer="fallback",
            reason="default_model 没就绪，用第一个就绪 provider 兜底",
        )

    # 4. mock
    return RoutingDecision(
        model="mock-local",
        provider="",
        layer="mock",
        reason="没有任何 provider 就绪",
    )
