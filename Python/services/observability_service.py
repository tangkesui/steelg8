from __future__ import annotations

from typing import Any, Mapping

import logger
import usage


def usage_summary() -> dict[str, Any]:
    return usage.summary()


def recent_usage(*, limit: int = 100) -> dict[str, Any]:
    return {"items": usage.recent(limit=limit)}


def logs(query: Mapping[str, list[str]]) -> dict[str, Any]:
    limit = _bounded_int(_first(query, "limit", "200"), default=200, minimum=1, maximum=1000)
    conv_id = _optional_int(_first(query, "conv", None))
    level = _first(query, "level", None)
    event_prefix = _first(query, "event", None)
    days = _bounded_int(_first(query, "days", "2"), default=2, minimum=1, maximum=14)
    return {
        "items": logger.read_recent(
            limit=limit,
            conversation_id=conv_id,
            level=level,
            event_prefix=event_prefix,
            days=days,
        ),
        "stats": logger.stats(days=1),
    }


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
