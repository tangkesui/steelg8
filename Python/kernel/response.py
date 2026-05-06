from __future__ import annotations

import json
from typing import Any


def json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def sse_event(event: dict[str, Any]) -> bytes:
    line = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    return line.encode("utf-8")
