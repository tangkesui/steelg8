from __future__ import annotations

from pathlib import Path
from typing import Any

import preferences as prefs_mod
from services.common import ServiceError


# 不允许加入 workspace_allowlist 的系统根（路径必须 resolve 后比较）。
# 这层是 settings_service 的入口校验；safe_path 还会再校验一次（双层防御）。
_FORBIDDEN_ALLOWLIST_ROOTS: set[str] = {
    "/",
    "/Users",
    "/System",
    "/Library",
    "/private",
    "/var",
    "/etc",
    "/bin",
    "/sbin",
    "/usr",
}

_MAX_PATH_LENGTH = 4096


def load_preferences() -> dict[str, Any]:
    return prefs_mod.load()


def save_preferences(body: Any) -> dict[str, Any]:
    body = body or {}
    if not isinstance(body, dict):
        raise ServiceError(400, {"error": "invalid json"})
    return prefs_mod.save(body)


def get_workspace_allowlist() -> dict[str, Any]:
    prefs = prefs_mod.load()
    items = prefs.get("workspace_allowlist") or []
    return {"items": list(items) if isinstance(items, list) else []}


def save_workspace_allowlist(body: Any) -> dict[str, Any]:
    body = body or {}
    if not isinstance(body, dict):
        raise ServiceError(400, {"error": "invalid json"})
    items = body.get("items")
    if items is None:
        items = body.get("workspace_allowlist")
    if not isinstance(items, list):
        raise ServiceError(400, {"error": "items 必须是数组"})

    cleaned: list[str] = []
    for entry in items:
        if not isinstance(entry, str) or not entry.strip():
            raise ServiceError(400, {"error": "每条 allowlist 必须是非空字符串"})
        if len(entry) > _MAX_PATH_LENGTH:
            raise ServiceError(400, {"error": f"单条路径长度超过 {_MAX_PATH_LENGTH}"})
        try:
            resolved = Path(entry).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise ServiceError(400, {"error": f"路径无法解析：{entry}（{exc}）"}) from exc
        if not resolved.is_absolute():
            raise ServiceError(400, {"error": f"路径必须是绝对路径：{entry}"})
        if not resolved.exists() or not resolved.is_dir():
            raise ServiceError(400, {"error": f"路径不存在或不是目录：{resolved}"})
        if str(resolved) in _FORBIDDEN_ALLOWLIST_ROOTS:
            raise ServiceError(400, {"error": f"不允许加入系统根目录：{resolved}"})
        cleaned.append(str(resolved))

    saved = prefs_mod.save({"workspace_allowlist": cleaned})
    items_out = saved.get("workspace_allowlist") or []
    return {"items": list(items_out) if isinstance(items_out, list) else []}
