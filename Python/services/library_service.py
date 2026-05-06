from __future__ import annotations

from typing import Any

import scratch
import templates as template_lib
from services.common import ServiceError, ServiceResponse


def scratch_note() -> dict[str, Any]:
    return {"text": scratch.read()}


def save_scratch_note(body: Any) -> dict[str, Any]:
    body = body or {}
    text = body.get("text", "") if isinstance(body, dict) else ""
    if not isinstance(text, str):
        raise ServiceError(400, {"error": "text must be string"})
    scratch.write(text)
    return {"ok": True, "length": len(text)}


def templates() -> dict[str, Any]:
    return {
        "dir": str(template_lib.default_dir()),
        "items": [t.to_dict() for t in template_lib.list_all()],
    }


def delete_template(path: str) -> ServiceResponse:
    ok = template_lib.delete(path)
    return ServiceResponse(status=200 if ok else 400, payload={"ok": ok})


def knowledge_cards() -> dict[str, Any]:
    import knowledge as knowledge_mod

    return {
        "dir": str(knowledge_mod.knowledge_root()),
        "items": knowledge_mod.list_cards(),
    }
