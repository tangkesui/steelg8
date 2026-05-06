from __future__ import annotations

from typing import Any

import conversations as conv_store
import project as project_mod
from services.common import ServiceError, ServiceResponse, required_text


def list_conversations(*, limit: int = 100) -> dict[str, Any]:
    convs = conv_store.list_conversations(limit=limit)
    return {"items": [c.to_dict() for c in convs]}


def conversation_detail(conversation_id: int) -> dict[str, Any]:
    conv = conv_store.get_conversation(conversation_id)
    if not conv:
        raise ServiceError(404, {"error": "not found"})
    return conv.to_dict()


def conversation_messages(conversation_id: int) -> dict[str, Any]:
    conv = conv_store.get_conversation(conversation_id)
    if not conv:
        raise ServiceError(404, {"error": "not found"})
    msgs = conv_store.list_messages(conversation_id, only_active=False)
    return {
        "conversation": conv.to_dict(),
        "messages": [m.to_dict() for m in msgs],
    }


def create_conversation(body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    title = str(body.get("title", "")).strip()
    project_root = body.get("projectRoot") or None
    if project_root is None:
        active = project_mod.get_active()
        if active:
            project_root = active.get("path")
    conv = conv_store.create_conversation(title=title, project_root=project_root)
    return conv.to_dict()


def rename_conversation(conversation_id: int, body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    title = required_text(body, "title", error="title required")
    conv = conv_store.rename_conversation(conversation_id, title)
    if not conv:
        raise ServiceError(404, {"error": "not found"})
    return conv.to_dict()


def remove_conversation(conversation_id: int) -> ServiceResponse:
    ok = conv_store.delete_conversation(conversation_id)
    return ServiceResponse(status=200 if ok else 404, payload={"ok": ok})
