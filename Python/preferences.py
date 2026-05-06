"""
steelg8 用户偏好（~/.steelg8/preferences.json）
------------------------------------------------

存放一些"可配置但不是 API key"的用户设置：
  - templates_dir:   模板库目录（默认 ~/Documents/steelg8/templates）
  - knowledge_dir:   知识库目录（默认 ~/.steelg8/knowledge；留接口以后可改）
  - budget_mode:     预算模式（true 时强制走 default_model，当前暂未启用）
  - compression_trigger_ratio: 历史压缩触发线（默认 0.60）

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
    "compression_trigger_ratio": 0.60,
    "log_level": "info",                 # debug | info | warn | error
    "log_retention_days": 14,
    "workspace_allowlist": [],           # 12.1：tool 沙箱 home 之外允许的目录
}

# 已知 key 的类型校验 —— 值类型不对就丢弃（避免 Swift 侧 decode 炸 UI）
_EXPECTED_TYPES: dict[str, tuple[type, ...]] = {
    "templates_dir": (str,),
    "knowledge_dir": (str,),
    "compression_trigger_ratio": (int, float),
    "log_level": (str,),
    "log_retention_days": (int, float),
    "workspace_allowlist": (list,),
}


def _coerce(key: str, value: Any) -> Any:
    """按 key 的期望类型做一次温柔转换；转不过就返回 None（保留默认值）。"""
    if value is None:
        return None
    if key == "workspace_allowlist":
        if not isinstance(value, list):
            return None
        cleaned: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                cleaned.append(item.strip())
        return cleaned
    expected = _EXPECTED_TYPES.get(key)
    if expected is None:
        return value  # 未登记的 key 不拦
    if isinstance(value, expected):
        # bool 是 int 的子类但我们不当数字用
        if isinstance(value, bool) and float not in expected:
            return None
        return value
    # 字符串形式的数字允许转
    if float in expected or int in expected:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


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
        for k, v in raw.items():
            coerced = _coerce(k, v)
            if coerced is not None:
                out[k] = coerced
        return out
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)


def save(data: dict[str, Any]) -> dict[str, Any]:
    _ensure_parent()
    cur = load()
    for k, v in (data or {}).items():
        if v is None:
            continue
        coerced = _coerce(k, v)
        if coerced is None:
            continue  # 类型不对，拒绝写入
        cur[k] = coerced
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
