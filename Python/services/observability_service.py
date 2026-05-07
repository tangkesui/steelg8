from __future__ import annotations

from typing import Any, Mapping

import logger
import usage


def usage_summary() -> dict[str, Any]:
    return usage.summary()


def recent_usage(*, limit: int = 100) -> dict[str, Any]:
    return {"items": usage.recent(limit=limit)}


def bounded_limit(
    query: Mapping[str, list[str]],
    *,
    default: int,
    maximum: int,
) -> int:
    return _bounded_int(
        _first(query, "limit", str(default)),
        default=default,
        minimum=1,
        maximum=maximum,
    )


def logs(query: Mapping[str, list[str]]) -> dict[str, Any]:
    limit = _bounded_int(_first(query, "limit", "200"), default=200, minimum=1, maximum=1000)
    conv_id = _optional_int(_first(query, "conv", None))
    level = _first(query, "level", None)
    event_prefix = _first(query, "event", None)
    days = _bounded_int(_first(query, "days", "2"), default=2, minimum=1, maximum=14)
    items = logger.read_recent(
        limit=limit,
        conversation_id=conv_id,
        level=level,
        event_prefix=event_prefix,
        days=days,
    )
    return {
        "items": [_enrich(r) for r in items],
        "stats": logger.stats(days=1),
    }


def _enrich(rec: dict[str, Any]) -> dict[str, Any]:
    """给日志记录合成 message 字段，方便前端直接展示。"""
    if "message" in rec:
        return rec
    event = rec.get("event", "")
    msg = ""
    if event == "chat.start":
        parts = []
        if rec.get("model_resolved"):
            parts.append(rec["model_resolved"])
        if rec.get("provider"):
            parts.append(f"via {rec['provider']}")
        if rec.get("routing_layer"):
            parts.append(f"[{rec['routing_layer']}]")
        msg = "  ".join(parts)
    elif event in ("agent.iter.start", "agent.iter.end"):
        model = rec.get("model", "")
        dur = rec.get("duration_ms")
        tok = rec.get("output_tokens") or rec.get("completion_tokens")
        parts = [model] if model else []
        if dur is not None:
            parts.append(f"{dur}ms")
        if tok:
            parts.append(f"↓{tok}tok")
        msg = "  ".join(parts)
    elif event == "agent.iter.exception":
        msg = rec.get("error_msg", "")[:120]
    elif event in ("http.failed", "http.retry"):
        url = rec.get("url", "")
        status = rec.get("status", "")
        attempts = rec.get("attempts") or rec.get("attempt", "")
        msg = f"HTTP {status}  {url}"
        if attempts:
            msg += f"  (attempt {attempts})"
    elif event == "tool.call":
        msg = rec.get("tool", "") + "  " + str(rec.get("args", ""))[:60]
    elif event == "tool.result":
        msg = rec.get("tool", "")
    elif event == "conversation.create":
        msg = f"conv #{rec.get('conversation_id', '?')}"
    elif rec.get("error_msg"):
        msg = str(rec["error_msg"])[:120]
    elif rec.get("reason"):
        msg = str(rec["reason"])[:120]
    return {**rec, "message": msg or None}


def _first(query: Mapping[str, list[str]], key: str, default: str | None) -> str | None:
    values = query.get(key)
    if not values:
        return default
    return values[0]


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
