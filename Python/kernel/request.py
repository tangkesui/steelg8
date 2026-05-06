from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse


def path_only(raw_path: str) -> str:
    return raw_path.split("?", 1)[0]


def query_params(raw_path: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(raw_path).query)


def url_decode(value: str) -> str:
    return unquote(value)


def parse_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    parsed = parse_int(value)
    out = default if parsed is None else parsed
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def read_json(headers: Mapping[str, str], rfile: Any) -> Any:
    raw_length = headers.get("Content-Length", "0")
    length = int(raw_length) if raw_length.isdigit() else 0
    raw = rfile.read(length) if length else b""
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None
