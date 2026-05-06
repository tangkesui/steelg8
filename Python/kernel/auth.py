from __future__ import annotations

import hmac
import os
from http.server import BaseHTTPRequestHandler


class LocalAuth:
    """Per-launch token guard for the local HTTP kernel."""

    def __init__(self, token: str | None = None) -> None:
        self.token = (token or "").strip()

    @classmethod
    def from_env(cls) -> "LocalAuth":
        return cls(os.environ.get("STEELG8_AUTH_TOKEN", ""))

    @property
    def required(self) -> bool:
        return bool(self.token)

    def request_token(self, handler: BaseHTTPRequestHandler) -> str:
        auth = (handler.headers.get("Authorization") or "").strip()
        if auth[:7].lower() == "bearer ":
            return auth[7:].strip()
        return (handler.headers.get("X-SteelG8-Token") or "").strip()

    def is_authenticated(self, handler: BaseHTTPRequestHandler) -> bool:
        if not self.token:
            return True
        return hmac.compare_digest(self.request_token(handler), self.token)

    def unauthorized_payload(self) -> dict[str, str]:
        return {
            "error": "unauthorized",
            "message": "steelg8 local kernel token required",
        }
