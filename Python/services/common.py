from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ServiceResponse:
    status: int
    payload: dict[str, Any]


class ServiceError(Exception):
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("error") or payload))
        self.status = status
        self.payload = payload


def require_dict(body: Any, *, allow_empty: bool = True) -> dict[str, Any]:
    if body is None and allow_empty:
        return {}
    if not isinstance(body, dict):
        raise ServiceError(400, {"error": "invalid json"})
    return body


def required_text(body: dict[str, Any], key: str, *, error: str) -> str:
    value = str(body.get(key, "")).strip()
    if not value:
        raise ServiceError(400, {"error": error})
    return value
