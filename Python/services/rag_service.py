"""RAG 管理页 / 路由设置页用的服务层。

3 类端点：
- GET /rag/config —— 当前 rag.json + 候选清单
- PUT /rag/config —— 写盘 + reload
- POST /rag/test-embedding —— 测试 embedding
- GET /rag/diagnostics —— 4-条 ring buffer 快照（embed/rerank 成功+失败）

provider 解析诊断 / catalog capability toggle 在 provider_service.py 里。
"""
from __future__ import annotations

import time
from typing import Any

from services.common import ServiceError, ServiceResponse
from providers import ProviderRegistry


def get_config(registry: ProviderRegistry) -> ServiceResponse:
    """返当前 rag.json + 可用候选清单。"""
    import rag_config
    import rag_strategy
    import rag_store
    import model_catalog

    cfg = rag_config.current()

    # 候选 provider 列表（kind == openai-compatible 的，按 ready 排序）
    candidates: list[dict[str, Any]] = []
    for prov in registry.providers.values():
        if prov.kind == "tool":
            continue
        candidates.append({
            "id": prov.name,
            "displayName": prov.display_name or prov.name,
            "kind": prov.kind,
            "ready": prov.is_ready(),
            "baseUrl": prov.base_url,
        })

    # 按 capability 过滤的 model 候选（embedding / rerank）
    embedding_models: list[dict[str, Any]] = []
    rerank_models: list[dict[str, Any]] = []
    for prov in registry.providers.values():
        if prov.kind == "tool":
            continue
        for m in model_catalog.all_models(prov.name):
            caps = m.get("capabilities") or ["chat"]
            if "embedding" in caps:
                embedding_models.append({
                    "provider": prov.name,
                    "model": m["id"],
                    "ready": prov.is_ready(),
                })
            if "rerank" in caps:
                rerank_models.append({
                    "provider": prov.name,
                    "model": m["id"],
                    "ready": prov.is_ready(),
                })

    return ServiceResponse(
        status=200,
        payload={
            "ok": True,
            "config": cfg.to_dict(),
            "fingerprint": cfg.embedding_fingerprint(),
            "providers": candidates,
            "embedding_candidates": embedding_models,
            "rerank_candidates": rerank_models,
            "strategies": rag_strategy.list_strategies(),
            "backends": rag_store.list_backends(),
        },
    )


def put_config(registry: ProviderRegistry, payload: dict[str, Any]) -> ServiceResponse:  # noqa: ARG001
    """写新 rag.json + 触发热加载。
    body 完整 schema 同 rag.json。
    """
    import rag_config
    import rag_store

    if not isinstance(payload, dict):
        raise ServiceError(400, {"error": "body must be a JSON object"})

    cfg = rag_config.RagConfig(
        embedding=rag_config._parse_embedding(payload.get("embedding")),
        rerank=rag_config._parse_rerank(payload.get("rerank")),
        strategy=rag_config._parse_strategy(payload.get("strategy")),
        backend=rag_config._parse_backend(payload.get("backend")),
        version=int(payload.get("version", 1) or 1),
    )

    # 校验 strategy / backend id 是否注册了；不出错只 fallback 到 default
    import rag_strategy
    if cfg.strategy.id not in rag_strategy.list_strategies():
        cfg.strategy.id = "default"
    if cfg.backend.id not in rag_store.list_backends():
        cfg.backend.id = "sqlite-brute-force"

    try:
        rag_config.save(cfg)
    except OSError as exc:
        raise ServiceError(500, {"error": f"写 rag.json 失败：{exc}"}) from exc

    rag_store.reset_default_store()
    return ServiceResponse(
        status=200,
        payload={
            "ok": True,
            "config": cfg.to_dict(),
            "fingerprint": cfg.embedding_fingerprint(),
        },
    )


def test_embedding(registry: ProviderRegistry, payload: dict[str, Any]) -> ServiceResponse:
    """单条文本测 embedding。返 dim、首 5 个分量、用时。"""
    import embedding

    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ServiceError(400, {"error": "text is required"})

    started = time.time()
    try:
        # 不传 provider_name → embedding.py 自己从 rag_config 读
        result = embedding.embed([text], registry)
    except embedding.EmbeddingError as exc:
        raise ServiceError(500, {"error": str(exc)}) from exc
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(500, {"error": f"unknown: {exc}"}) from exc

    elapsed_ms = int((time.time() - started) * 1000)
    if not result.vectors:
        raise ServiceError(500, {"error": "embedding 返回空向量"})
    vec = result.vectors[0]
    return ServiceResponse(
        status=200,
        payload={
            "ok": True,
            "model": result.model,
            "dimensions": len(vec),
            "preview": vec[:5],
            "usage": result.usage,
            "elapsed_ms": elapsed_ms,
        },
    )


def diagnostics() -> ServiceResponse:
    """GET /rag/diagnostics —— 4 条 ring buffer 快照。"""
    import rag_diagnostics
    return ServiceResponse(
        status=200,
        payload={
            "ok": True,
            **rag_diagnostics.snapshot(),
        },
    )
