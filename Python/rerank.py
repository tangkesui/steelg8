"""
Rerank 封装：阿里云百炼 qwen3-rerank
--------------------------------------

RAG 流程里，embedding 余弦检索拿 top-K 只是"粗排"。送进 rerank 让模型看
query + 每条候选文本，打真正的相关性分，再按这个分重排。准确率能明显提升。

API：https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank

这个端点不在 OpenAI 兼容模式里，走 DashScope 原生协议。但既然 key 相同，就
直接用 bailian provider 的 api_key。base_url 单独写。

用户账号里"qwen3-rerank"有 1M token 免费额度，配合"免费额度用完即停"，
安全兜底。
"""

from __future__ import annotations

import os
from typing import Any

from providers import ProviderRegistry
import network


DEFAULT_MODEL = os.environ.get("STEELG8_RERANK_MODEL", "qwen3-rerank")
DEFAULT_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)
DEFAULT_TIMEOUT = 20


class RerankError(RuntimeError):
    pass


def _resolve_provider(
    registry: ProviderRegistry, provider_name: str | None
) -> tuple[Any, str]:
    """按 rag_config / 显式参数解析 rerank provider；找不到就抛 RerankError。"""
    if provider_name:
        prov = registry.providers.get(provider_name)
        if prov and prov.is_ready():
            return prov, provider_name
        raise RerankError(
            f"配置的 rerank provider='{provider_name}' 未就绪。"
            "去 Settings → 模型 → RAG 管理 检查"
        )
    import rag_config

    cfg = rag_config.current()
    name = cfg.rerank.provider
    if not name:
        raise RerankError(
            "RAG 未配 rerank provider。去 Settings → 模型 → RAG 管理 选一个。"
        )
    prov = registry.providers.get(name)
    if prov is None or not prov.is_ready():
        raise RerankError(
            f"RAG 配置的 rerank provider='{name}' 未就绪"
        )
    return prov, name


def rerank(
    query: str,
    docs: list[str],
    registry: ProviderRegistry,
    *,
    top_n: int | None = None,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    provider_name: str | None = None,
    endpoint_kind: str | None = None,
) -> list[tuple[int, float]]:
    """把候选文档重排，返回 [(原始 index, 相关性分), ...]，按分数降序。

    docs 为空或 provider 没就绪时返回空，调用方按 fallback 逻辑处理。
    endpoint_kind: 'dashscope-native'（默认走原 DashScope rerank）/ 'openai-compat'（cohere-style /rerank）
    """
    if not docs:
        return []

    import rag_config

    cfg = rag_config.current()
    eff_model = model or cfg.rerank.model or DEFAULT_MODEL
    eff_kind = endpoint_kind or cfg.rerank.endpoint_kind or "dashscope-native"
    eff_url_override = cfg.rerank.endpoint_url_override

    try:
        provider, prov_name = _resolve_provider(registry, provider_name)
    except RerankError as exc:
        _record_rerank_error("", eff_model, "no_provider", str(exc))
        raise

    import time as _time
    t_start = _time.monotonic()
    try:
        if eff_kind == "dashscope-native":
            pairs = _rerank_dashscope_native(
                query=query, docs=docs, provider=provider,
                model=eff_model, top_n=top_n, timeout=timeout,
                url_override=eff_url_override,
            )
        elif eff_kind == "openai-compat":
            pairs = _rerank_openai_compat(
                query=query, docs=docs, provider=provider,
                model=eff_model, top_n=top_n, timeout=timeout,
            )
        else:
            raise RerankError(f"未知 endpoint_kind: {eff_kind}")
    except RerankError as exc:
        _record_rerank_error(prov_name, eff_model, "http_error", str(exc))
        raise
    except Exception as exc:  # noqa: BLE001
        _record_rerank_error(prov_name, eff_model, "unknown", str(exc))
        raise

    latency_ms = int((_time.monotonic() - t_start) * 1000)
    _record_rerank_success(
        prov_name, eff_model, eff_kind, len(docs),
        latency_ms=latency_ms, fallback_used=False,
    )
    return pairs


def _rerank_dashscope_native(
    *,
    query: str,
    docs: list[str],
    provider: Any,
    model: str,
    top_n: int | None,
    timeout: int,
    url_override: str | None,
) -> list[tuple[int, float]]:
    payload: dict[str, Any] = {
        "model": model,
        "input": {"query": query, "documents": docs},
        "parameters": {
            "top_n": top_n or len(docs),
            "return_documents": False,
        },
    }
    url = url_override or DEFAULT_ENDPOINT
    try:
        body = network.request_json(
            url,
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {provider.api_key()}"},
            timeout=timeout,
            retries=1,
        )
    except network.NetworkError as exc:
        raise RerankError(str(exc)) from exc
    if not isinstance(body, dict):
        raise RerankError("rerank 响应不是 JSON 对象")
    output = body.get("output") or {}
    results = output.get("results") or []
    pairs: list[tuple[int, float]] = []
    for r in results:
        idx = int(r.get("index", -1))
        score = float(r.get("relevance_score", 0.0))
        if idx >= 0:
            pairs.append((idx, score))
    pairs.sort(key=lambda x: -x[1])
    return pairs


def _rerank_openai_compat(
    *,
    query: str,
    docs: list[str],
    provider: Any,
    model: str,
    top_n: int | None,
    timeout: int,
) -> list[tuple[int, float]]:
    """Cohere 风格的 OpenAI-兼容 /rerank 端点。
    body schema: {model, query, documents, top_n}；response: {results:[{index, relevance_score}]}.
    """
    url = f"{provider.base_url.rstrip('/')}/rerank"
    payload = {
        "model": model,
        "query": query,
        "documents": docs,
        "top_n": top_n or len(docs),
        "return_documents": False,
    }
    try:
        body = network.request_json(
            url,
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {provider.api_key()}"},
            timeout=timeout,
            retries=1,
        )
    except network.NetworkError as exc:
        raise RerankError(str(exc)) from exc
    if not isinstance(body, dict):
        raise RerankError("rerank 响应不是 JSON 对象")
    results = body.get("results") or body.get("data") or []
    pairs: list[tuple[int, float]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        idx = r.get("index")
        score = r.get("relevance_score") or r.get("score")
        if idx is None or score is None:
            continue
        try:
            pairs.append((int(idx), float(score)))
        except (TypeError, ValueError):
            continue
    pairs.sort(key=lambda x: -x[1])
    return pairs


def _record_rerank_success(
    provider: str, model: str, endpoint_kind: str, doc_count: int,
    *, latency_ms: int = 0, fallback_used: bool = False,
) -> None:
    try:
        import rag_diagnostics
        rag_diagnostics.record_rerank_success(
            provider=provider, model=model,
            endpoint_kind=endpoint_kind, doc_count=doc_count,
            fallback_used=fallback_used, latency_ms=latency_ms,
        )
    except Exception:  # noqa: BLE001
        pass


def _record_rerank_error(provider: str, model: str, kind: str, message: str) -> None:
    try:
        import rag_diagnostics
        rag_diagnostics.record_rerank_error(
            provider=provider, model=model, kind=kind, message=message,
        )
    except Exception:  # noqa: BLE001
        pass
