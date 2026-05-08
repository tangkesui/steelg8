"""
RAG 诊断 ring buffer（4 条单容量，含成功 + 失败）。

dogfood 中要看"上次成功是啥时候、用了多久"和"上次失败是啥原因"，
单看失败不够。本模块给 embedding / rerank 各一对（success / error）
最近一次记录，进程内单例，路由设置 / RAG 管理 / 运行状态 RAG 页都来读。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class EmbedSuccess:
    timestamp: float
    provider: str
    model: str
    fingerprint: str
    dimensions: int
    total_texts: int
    latency_ms: int = 0
    batch_size: int = 0


@dataclass
class EmbedFailure:
    timestamp: float
    provider: str
    model: str
    kind: str        # http_error / timeout / parse_error / no_provider / unknown
    message: str


@dataclass
class RerankSuccess:
    timestamp: float
    provider: str
    model: str
    endpoint_kind: str
    doc_count: int
    fallback_used: bool
    latency_ms: int = 0


@dataclass
class RerankFailure:
    timestamp: float
    provider: str
    model: str
    kind: str
    message: str


_LAST_EMBED_OK: EmbedSuccess | None = None
_LAST_EMBED_ERR: EmbedFailure | None = None
_LAST_RERANK_OK: RerankSuccess | None = None
_LAST_RERANK_ERR: RerankFailure | None = None

# 长 message 截断保护
_MSG_MAX = 200


def _truncate(s: str) -> str:
    s = s or ""
    return s if len(s) <= _MSG_MAX else s[: _MSG_MAX - 1] + "…"


def record_embed_success(
    provider: str,
    model: str,
    dimensions: int,
    total_texts: int,
    fingerprint: str | None = None,
    *,
    latency_ms: int = 0,
    batch_size: int = 0,
) -> None:
    global _LAST_EMBED_OK
    if fingerprint is None:
        try:
            import rag_config
            fingerprint = rag_config.current().embedding_fingerprint()
        except Exception:  # noqa: BLE001
            fingerprint = ""
    _LAST_EMBED_OK = EmbedSuccess(
        timestamp=time.time(),
        provider=provider,
        model=model,
        fingerprint=fingerprint,
        dimensions=int(dimensions),
        total_texts=int(total_texts),
        latency_ms=int(latency_ms),
        batch_size=int(batch_size),
    )


def record_embed_error(
    provider: str, model: str, kind: str, message: str
) -> None:
    global _LAST_EMBED_ERR
    _LAST_EMBED_ERR = EmbedFailure(
        timestamp=time.time(),
        provider=provider,
        model=model,
        kind=kind,
        message=_truncate(message),
    )


def record_rerank_success(
    provider: str,
    model: str,
    endpoint_kind: str,
    doc_count: int,
    fallback_used: bool = False,
    *,
    latency_ms: int = 0,
) -> None:
    global _LAST_RERANK_OK
    _LAST_RERANK_OK = RerankSuccess(
        timestamp=time.time(),
        provider=provider,
        model=model,
        endpoint_kind=endpoint_kind,
        doc_count=int(doc_count),
        fallback_used=bool(fallback_used),
        latency_ms=int(latency_ms),
    )


def record_rerank_error(
    provider: str, model: str, kind: str, message: str
) -> None:
    global _LAST_RERANK_ERR
    _LAST_RERANK_ERR = RerankFailure(
        timestamp=time.time(),
        provider=provider,
        model=model,
        kind=kind,
        message=_truncate(message),
    )


def snapshot() -> dict[str, Any]:
    """返四条最近记录，给 GET /rag/diagnostics 用。"""
    return {
        "embed_ok": asdict(_LAST_EMBED_OK) if _LAST_EMBED_OK else None,
        "embed_err": asdict(_LAST_EMBED_ERR) if _LAST_EMBED_ERR else None,
        "rerank_ok": asdict(_LAST_RERANK_OK) if _LAST_RERANK_OK else None,
        "rerank_err": asdict(_LAST_RERANK_ERR) if _LAST_RERANK_ERR else None,
    }


def clear() -> None:
    """测试 / 调试用。"""
    global _LAST_EMBED_OK, _LAST_EMBED_ERR, _LAST_RERANK_OK, _LAST_RERANK_ERR
    _LAST_EMBED_OK = _LAST_EMBED_ERR = _LAST_RERANK_OK = _LAST_RERANK_ERR = None
