"""
Embedding 封装：阿里云百炼（DashScope）Qwen Embedding 系列，走 OpenAI 兼容模式。

默认走 **text-embedding-v3**：不同账号在百炼看到的模型列表不一样，v3 是个人账号
最常见且开通即用的版本（500K token 免费额度，付费 ¥0.7/M）。

企业账号/特定 region 看得到 v4（Qwen3-Embedding，¥0.5/M，2048 dims）的话，改
环境变量：

    STEELG8_EMBED_MODEL=text-embedding-v4 open steelg8.app

维度默认 1024（v3 / v4 都支持），需要 768 / 512 压缩存储时可以：

    STEELG8_EMBED_DIMS=512

返回：list[list[float]]，每条对应 input 中一条文本的 embedding 向量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from providers import ProviderRegistry
import network


# ---- 默认配置 ----

DEFAULT_MODEL = os.environ.get("STEELG8_EMBED_MODEL", "text-embedding-v3")
DEFAULT_DIMS = int(os.environ.get("STEELG8_EMBED_DIMS", "1024"))
BATCH_SIZE = 10  # DashScope 推荐每批 ≤10 条


class EmbeddingError(RuntimeError):
    pass


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    usage: dict[str, int]  # {total_tokens, ...}
    model: str


def _resolve_provider(
    registry: ProviderRegistry, provider_name: str | None
) -> tuple[Any, str]:
    """按 rag_config / 显式参数解析 embedding provider。
    返 (provider, provider_name)；找不到 / 没就绪就抛 EmbeddingError。
    """
    if provider_name:
        prov = registry.providers.get(provider_name)
        if prov and prov.is_ready():
            return prov, provider_name
        raise EmbeddingError(
            f"配置的 embedding provider='{provider_name}' 未就绪。"
            "去 Settings → 模型 → RAG 管理 选一个，并确保该 provider 配了 key。"
        )
    # 兼容老调用：没传 provider_name → 从 rag_config 读
    import rag_config

    cfg = rag_config.current()
    name = cfg.embedding.provider
    if not name:
        raise EmbeddingError(
            "RAG 未配 embedding provider。去 Settings → 模型 → RAG 管理 选一个。"
        )
    prov = registry.providers.get(name)
    if prov is None or not prov.is_ready():
        raise EmbeddingError(
            f"RAG 配置的 embedding provider='{name}' 未就绪。"
            "去 Settings → 模型 → RAG 管理 检查配置。"
        )
    return prov, name


def embed(
    texts: list[str],
    registry: ProviderRegistry,
    *,
    model: str | None = None,
    dimensions: int | None = None,
    timeout: int = 60,
    provider_name: str | None = None,
) -> EmbeddingResult:
    """批量 embedding。

    Args:
        provider_name: 显式指定 provider；缺省从 rag_config 读
        model / dimensions: 缺省从 rag_config 读，env var 老兼容路径在 rag_config 处理
    """
    # rag_config 读默认值（之前是 hardcoded DEFAULT_MODEL / DEFAULT_DIMS）
    import rag_config

    cfg = rag_config.current()
    eff_model = model or cfg.embedding.model or DEFAULT_MODEL
    eff_dims = dimensions if dimensions else (cfg.embedding.dimensions or DEFAULT_DIMS)

    if not texts:
        return EmbeddingResult(vectors=[], usage={}, model=eff_model)

    provider, prov_name = _resolve_provider(registry, provider_name)

    import time as _time
    all_vectors: list[list[float]] = []
    total_tokens = 0
    url = f"{provider.base_url}/embeddings"
    last_batch_size = 0
    t_start = _time.monotonic()

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        last_batch_size = len(batch)
        payload: dict[str, Any] = {
            "model": eff_model,
            "input": batch,
            "dimensions": eff_dims,
            "encoding_format": "float",
        }
        try:
            body = network.request_json(
                url,
                method="POST",
                payload=payload,
                headers={
                    "Authorization": f"Bearer {provider.api_key()}",
                },
                timeout=timeout,
                retries=1,
            )
        except network.NetworkError as exc:
            _record_embed_error(prov_name, eff_model, "http_error", str(exc))
            raise EmbeddingError(str(exc)) from exc
        if not isinstance(body, dict):
            raise EmbeddingError("embedding 响应不是 JSON 对象")

        data = body.get("data") or []
        if len(data) != len(batch):
            raise EmbeddingError(
                f"embedding 返回条数不匹配：请求 {len(batch)}，返回 {len(data)}"
            )
        # 保证顺序和 input 一一对应（API 会返回 index 字段）
        data_sorted = sorted(data, key=lambda x: x.get("index", 0))
        for item in data_sorted:
            vec = item.get("embedding") or []
            if not vec:
                raise EmbeddingError("返回了空向量")
            all_vectors.append([float(x) for x in vec])

        u = body.get("usage") or {}
        total_tokens += int(u.get("total_tokens") or 0)

    result = EmbeddingResult(
        vectors=all_vectors,
        usage={"total_tokens": total_tokens},
        model=eff_model,
    )
    latency_ms = int((_time.monotonic() - t_start) * 1000)
    _record_embed_success(
        prov_name, eff_model, eff_dims, len(texts),
        latency_ms=latency_ms, batch_size=last_batch_size,
    )
    return result


def _record_embed_success(
    provider: str, model: str, dimensions: int, total_texts: int,
    *, latency_ms: int = 0, batch_size: int = 0,
) -> None:
    try:
        import rag_diagnostics
        rag_diagnostics.record_embed_success(
            provider=provider,
            model=model,
            dimensions=dimensions,
            total_texts=total_texts,
            latency_ms=latency_ms,
            batch_size=batch_size,
        )
    except Exception:  # noqa: BLE001
        pass


def _record_embed_error(provider: str, model: str, kind: str, message: str) -> None:
    try:
        import rag_diagnostics
        rag_diagnostics.record_embed_error(
            provider=provider, model=model, kind=kind, message=message
        )
    except Exception:  # noqa: BLE001
        pass


def embed_one(
    text: str,
    registry: ProviderRegistry,
    *,
    timeout: int = 10,
    **kwargs: Any,
) -> list[float]:
    """便捷：单条 embedding。query 路径默认 10s 超时，避免阻塞对话。"""
    res = embed([text], registry, timeout=timeout, **kwargs)
    if not res.vectors:
        raise EmbeddingError("embed_one 返回空")
    return res.vectors[0]
