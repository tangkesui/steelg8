"""
steelg8 用户偏好（~/.steelg8/preferences.json）
------------------------------------------------

存放一些"可配置但不是 API key"的用户设置：
  - templates_dir:   模板库目录（默认 ~/Documents/steelg8/templates）
  - knowledge_dir:   知识库目录（默认 ~/.steelg8/knowledge；留接口以后可改）
  - budget_mode:     预算模式（true 时强制走 default_model，当前暂未启用）

Swift 端通过 JS bridge 写这个文件（拿到 NSOpenPanel 选的目录后写入），
Python 这边读取。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PREF_PATH = Path(os.environ.get(
    "STEELG8_PREF_PATH",
    Path.home() / ".steelg8" / "preferences.json",
))


DEFAULTS: dict[str, Any] = {
    "templates_dir": str(Path.home() / "Documents" / "steelg8" / "templates"),
    "knowledge_dir": str(Path.home() / ".steelg8" / "knowledge"),
}


def _ensure_parent() -> None:
    PREF_PATH.parent.mkdir(parents=True, exist_ok=True)


def load() -> dict[str, Any]:
    _ensure_parent()
    if not PREF_PATH.exists():
        return dict(DEFAULTS)
    try:
        raw = json.loads(PREF_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return dict(DEFAULTS)
        out = dict(DEFAULTS)
        out.update({k: v for k, v in raw.items() if v is not None})
        return out
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)


def save(data: dict[str, Any]) -> dict[str, Any]:
    _ensure_parent()
    cur = load()
    cur.update({k: v for k, v in (data or {}).items() if v is not None})
    try:
        PREF_PATH.write_text(
            json.dumps(cur, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        PREF_PATH.chmod(0o600)
    except OSError:
        pass
    return cur


def get(key: str) -> Any:
    return load().get(key, DEFAULTS.get(key))


def set(key: str, value: Any) -> dict[str, Any]:  # noqa: A001
    return save({key: value})
