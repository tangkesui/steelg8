from __future__ import annotations

from typing import Any

import conversations as conv_store
import project as project_mod
from providers import ProviderRegistry
from services.common import ServiceError, ServiceResponse, require_dict


def active_project() -> dict[str, Any]:
    return {"active": project_mod.active_project_summary()}


def project_conversation() -> dict[str, Any]:
    active = project_mod.active_project_summary()
    project_root = active.get("path") if active else None
    title = active.get("name") if active else "默认对话"
    conv = conv_store.get_or_create_project_conversation(
        project_root=project_root,
        title=title or "项目对话",
    )
    msgs = conv_store.list_messages(conv.id, only_active=False)
    return {
        "conversation": conv.to_dict(),
        "messages": [m.to_dict() for m in msgs],
    }


def index_status() -> dict[str, Any]:
    return project_mod.status()


def list_projects() -> dict[str, Any]:
    return {"items": project_mod.list_all()}


def open_project(body: Any, registry: ProviderRegistry) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ServiceError(400, {"error": "invalid json"})
    path = str(body.get("path", "")).strip()
    if not path:
        raise ServiceError(400, {"error": "path is required"})
    rebuild = bool(body.get("rebuild", True))
    try:
        proj = project_mod.open_project(path, registry, rebuild=rebuild)
    except ValueError as exc:
        raise ServiceError(400, {"error": str(exc)}) from exc
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(500, {"error": f"{exc.__class__.__name__}: {exc}"}) from exc
    return {
        "id": proj.id,
        "path": proj.path,
        "name": proj.name,
        "chunkCount": proj.chunk_count,
        "indexStatus": project_mod.status(),
    }


def close_project() -> dict[str, Any]:
    project_mod.close_project()
    return {"ok": True}


def reindex_project(registry: ProviderRegistry) -> dict[str, Any]:
    active = project_mod.get_active()
    if not active:
        raise ServiceError(400, {"error": "没有激活的项目"})
    try:
        proj = project_mod.open_project(active["path"], registry, rebuild=True)
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(500, {"error": f"{exc.__class__.__name__}: {exc}"}) from exc
    return {
        "id": proj.id,
        "path": proj.path,
        "indexStatus": project_mod.status(),
    }


def activate_project(project_id: int) -> dict[str, Any]:
    res = project_mod.activate_by_id(project_id)
    if res is None:
        raise ServiceError(404, {"error": "project not found"})
    return res


def rename_project(project_id: int, body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    new_name = str(body.get("name", "")).strip()
    if not new_name:
        raise ServiceError(400, {"error": "name required"})
    res = project_mod.rename(project_id, new_name)
    if res is None:
        raise ServiceError(404, {"error": "project not found"})
    return res


def remove_project(project_id: int) -> ServiceResponse:
    ok = project_mod.remove(project_id)
    return ServiceResponse(status=200 if ok else 404, payload={"ok": ok})


def rag_debug(body: Any, registry: ProviderRegistry) -> dict[str, Any]:
    body = require_dict(body, allow_empty=False)
    query = str(body.get("query", "")).strip()
    if not query:
        raise ServiceError(400, {"error": "query is required"})
    top_k = _bounded_int(body.get("topK"), default=5, minimum=1, maximum=20)
    debug = project_mod.retrieve_debug(query, registry, top_k=top_k)
    return {
        "ok": True,
        "query": debug["query"],
        "activeProject": debug["activeProject"],
        "skippedCurrentProject": debug["skippedCurrentProject"],
        "embedding": debug["embedding"],
        "rerank": debug["rerank"],
        "lanes": {
            name: [_hit_payload(hit) for hit in hits]
            for name, hits in debug["lanes"].items()
        },
        "coarse": [_hit_payload(hit) for hit in debug["coarse"]],
        "final": [_hit_payload(hit) for hit in debug["final"]],
    }


def _hit_payload(hit: Any) -> dict[str, Any]:
    citation = hit.citation() if hasattr(hit, "citation") else {}
    metadata = getattr(hit, "metadata", None) or {}
    return {
        "relPath": hit.rel_path,
        "chunkIdx": hit.chunk_idx,
        "score": hit.score,
        "retrieval": hit.retrieval,
        "sourceType": hit.source_type,
        "heading": hit.heading,
        "page": hit.page,
        "preview": hit.text[:420],
        "citation": citation,
        "parser": metadata.get("parser"),
        "chunkProfile": metadata.get("chunk_profile"),
        "blockTypes": metadata.get("block_types") or [],
    }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)
