"""
官方 / 社区数据源拉取定价（best-effort，仅在 catalog refresh 时调用）。

设计原则：
- 失败静默：任何异常返空 dict，不阻断 refresh 主流程
- 不参与 chat / agent 路径，不打破"HTTP/agent 核心 stdlib-only"约束
- 优先 JSON 数据源（LiteLLM 社区维护表），不抓 SPA

返回：
    scrape_pricing("kimi") → {
        "moonshot-v1-8k": {"input": 1.67, "output": 1.67},   # USD per Mtok
        ...
    }
缺数据 → 该 model id 不出现在返回值里 → catalog 保持 null。

数据源：
- LiteLLM 维护的 JSON：https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json
  字段：input_cost_per_token / output_cost_per_token（USD per token，× 1e6 得 per Mtok）
  不命中的模型本期不强行兜底。
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Callable

_LOG = logging.getLogger("steelg8.pricing_scraper")
_TIMEOUT = 8

_LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# 单次进程内缓存（避免一次 catalog refresh 对每个 provider 都重新拉）
_litellm_cache: dict[str, dict] | None = None


def scrape_pricing(provider_id: str) -> dict[str, dict[str, float]]:
    """主入口。任何异常都吞掉返空。"""
    fn: Callable[[], dict[str, dict[str, float]]] | None = _DISPATCH.get(provider_id)
    if fn is None:
        return {}
    try:
        return fn() or {}
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("pricing scrape for %s failed: %s", provider_id, exc)
        return {}


def _load_litellm() -> dict[str, dict]:
    global _litellm_cache
    if _litellm_cache is not None:
        return _litellm_cache
    try:
        req = urllib.request.Request(
            _LITELLM_URL,
            headers={"User-Agent": "steelg8/0.1 pricing-scraper"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = resp.read()
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            parsed = {}
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("litellm json fetch failed: %s", exc)
        parsed = {}
    _litellm_cache = parsed
    return parsed


def _per_mtok(value: float | int | None) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    f = float(value) * 1_000_000
    return round(f, 4) if f > 0 else None


def _filter_litellm_by_keys(
    keys_pred: Callable[[str], bool],
    *,
    id_transform: Callable[[str], str] | None = None,
) -> dict[str, dict[str, float]]:
    """从 LiteLLM 表里挑符合 predicate 的 key，提取定价。

    id_transform：把 LiteLLM key（可能含 prefix）映射回我们用的 model id。
    返回字典里**只**包含两端价格至少一端非空的条目。
    """
    src = _load_litellm()
    out: dict[str, dict[str, float]] = {}
    for key, val in src.items():
        if not isinstance(val, dict):
            continue
        if not keys_pred(key):
            continue
        ip = _per_mtok(val.get("input_cost_per_token"))
        op = _per_mtok(val.get("output_cost_per_token"))
        if ip is None and op is None:
            continue
        mid = id_transform(key) if id_transform else key
        out[mid] = {"input": ip, "output": op}
    return out


# ---------- per-provider scrapers ----------

def _scrape_deepseek() -> dict[str, dict[str, float]]:
    """deepseek 直连：LiteLLM 里 'deepseek-chat' / 'deepseek-reasoner' 是平的。"""
    return _filter_litellm_by_keys(
        lambda k: k.startswith("deepseek-") and "/" not in k
    )


def _scrape_kimi() -> dict[str, dict[str, float]]:
    """kimi 直连：LiteLLM 里多以 azure_ai/ bedrock/ 等 prefix 出现，平 id 较少。
    挑 'moonshot-' 开头平 id 试一次。"""
    return _filter_litellm_by_keys(
        lambda k: (k.startswith("moonshot-") or k.startswith("kimi-"))
        and "/" not in k
    )


def _scrape_bailian() -> dict[str, dict[str, float]]:
    """阿里百炼：LiteLLM 中 qwen 直连 id 多带各种 prefix。挑 'qwen-' 开头平 id。"""
    return _filter_litellm_by_keys(
        lambda k: k.startswith("qwen-") and "/" not in k
    )


def _scrape_openrouter() -> dict[str, dict[str, float]]:
    """openrouter pricing 已在 catalog_refresh 主流程从上游 /v1/models 拿到（verified）。
    本函数返空，scraper 二阶段不重复处理。"""
    return {}


_DISPATCH: dict[str, Callable[[], dict[str, dict[str, float]]]] = {
    "bailian":    _scrape_bailian,
    "kimi":       _scrape_kimi,
    "deepseek":   _scrape_deepseek,
    "openrouter": _scrape_openrouter,
}
