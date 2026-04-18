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

import json
import os
from typing import Any
from urllib import request, error

from providers import ProviderRegistry


DEFAULT_MODEL = os.environ.get("STEELG8_RERANK_MODEL", "qwen3-rerank")
DEFAULT_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)
DEFAULT_TIMEOUT = 20


class RerankError(RuntimeError):
    pass


def _pick_bailian(registry: ProviderRegistry):
    for name in ("bailian", "qwen"):
        prov = registry.providers.get(name)
        if prov and prov.is_ready():
            return prov
    return None


def rerank(
    query: str,
    docs: list[str],
    registry: ProviderRegistry,
    *,
    top_n: int | None = None,
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[tuple[int, float]]:
    """把候选文档重排，返回 [(原始 index, 相关性分), ...]，按分数降序。

    docs 为空或 provider 没就绪时返回空，调用方按 fallback 逻辑处理。

    """
    if not docs:
        return []

    provider = _pick_bailian(registry)
    if provider is None:
        raise RerankError("没有就绪的 bailian provider，rerank 不可用")

    payload: dict[str, Any] = {
        "model": model,
        "input": {
            "query": query,
            "documents": docs,
        },
        "parameters": {
            "top_n": top_n or len(docs),
            "return_documents": False,  # 省流量，只要分数
        },
    }

    req = request.Request(
        DEFAULT_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {provider.api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400] if exc.fp else ""
        raise RerankError(f"HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RerankError(f"网络错误：{exc}") from exc

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
