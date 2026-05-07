"""
steelg8 · 结构化日志系统
--------------------------

目标：每次 /chat 请求的全链路都有迹可循——HTTP 上游状态码、agent 每轮 iter、
tool 调用 args/result/耗时、RAG 召回、压缩触发、cache 命中。

落盘：
  ~/.steelg8/logs/YYYY-MM-DD.jsonl
  每日滚动，默认保留 14 天（preferences.log_retention_days）。

每条日志格式（JSONL 一行一条）：
  ts             ISO8601（毫秒精度）
  level          debug | info | warn | error
  event          事件名，如 "chat.start" / "llm.call" / "tool.result"
  conversation_id  可选
  ...任意结构化字段

日志级别（受 preferences.log_level 控制，默认 info）：
  debug — 所有事件（含每个 delta chunk）
  info  — 请求/响应/tool 调用/压缩/缓存（默认）
  warn  — 降级、超时、空响应、重试
  error — 上游错误、工具异常、kernel 异常
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import preferences
except ImportError:
    preferences = None  # 启动早期可能还没准备好


_LOCK = threading.Lock()

LEVEL_ORDER = {"debug": 10, "info": 20, "warn": 30, "error": 40}

# 内存缓存：避免每条日志都读 preferences.json
_cached_level = "info"
_cached_retention = 14
_cached_level_at = 0.0


def _logs_dir() -> Path:
    p = Path(os.environ.get(
        "STEELG8_LOGS_DIR",
        Path.home() / ".steelg8" / "logs",
    )).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _today_file() -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return _logs_dir() / f"{today}.jsonl"


def _current_level() -> str:
    """读当前 log_level，带 5 秒缓存避免高频 IO。"""
    global _cached_level, _cached_retention, _cached_level_at
    now = time.time()
    if now - _cached_level_at < 5:
        return _cached_level
    try:
        if preferences:
            raw = preferences.get("log_level")
            if raw in LEVEL_ORDER:
                _cached_level = raw
            retention = preferences.get("log_retention_days")
            if isinstance(retention, (int, float)) and retention > 0:
                _cached_retention = int(retention)
    except Exception:  # noqa: BLE001
        pass
    _cached_level_at = now
    return _cached_level


def _retention_days() -> int:
    _current_level()  # 顺便刷新 retention
    return _cached_retention


def _should_write(level: str) -> bool:
    return LEVEL_ORDER.get(level, 20) >= LEVEL_ORDER.get(_current_level(), 20)


def _prune_old_logs() -> None:
    """清理过期日志文件。每天第一次写日志时触发一次。"""
    retention = _retention_days()
    if retention <= 0:
        return
    cutoff = time.time() - retention * 86400
    try:
        for p in _logs_dir().glob("*.jsonl"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


_last_prune_day = ""


def _maybe_prune() -> None:
    global _last_prune_day
    today = datetime.now().strftime("%Y-%m-%d")
    if today != _last_prune_day:
        _last_prune_day = today
        _prune_old_logs()


def log(level: str, event: str, **fields: Any) -> None:
    """写一条日志。level 低于阈值时直接丢弃。

    示例：
      log("info", "llm.call", model="kimi-k2.5", duration_ms=1234)
      log("error", "tool.exception", tool="docx_fill", error=str(exc))
    """
    if level not in LEVEL_ORDER:
        level = "info"
    if not _should_write(level):
        return

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "level": level,
        "event": event,
    }
    # fields 里如果有同名 key 覆盖不了核心三字段
    record.update({k: v for k, v in fields.items() if k not in ("ts", "level", "event")})

    try:
        line = json.dumps(record, ensure_ascii=False, default=_json_default) + "\n"
    except (TypeError, ValueError):
        # 有不可序列化对象就降级成字符串
        safe = {k: (v if _is_jsonable(v) else repr(v)) for k, v in record.items()}
        line = json.dumps(safe, ensure_ascii=False) + "\n"

    _maybe_prune()
    path = _today_file()
    with _LOCK:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass  # 不阻塞主流程


def _is_jsonable(v: Any) -> bool:
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


def _json_default(o: Any) -> Any:
    if hasattr(o, "__dict__"):
        return {k: v for k, v in o.__dict__.items() if _is_jsonable(v)}
    return repr(o)


# ---------- 便捷语法糖 ----------


def debug(event: str, **fields: Any) -> None:
    log("debug", event, **fields)


def info(event: str, **fields: Any) -> None:
    log("info", event, **fields)


def warn(event: str, **fields: Any) -> None:
    log("warn", event, **fields)


def error(event: str, exc: BaseException | None = None, **fields: Any) -> None:
    if exc is not None:
        fields.setdefault("error_type", exc.__class__.__name__)
        fields.setdefault("error_msg", str(exc))
        fields.setdefault("traceback", traceback.format_exc()[-2000:])
    log("error", event, **fields)


# ---------- 读取 API（给 /logs 端点用） ----------


def read_recent(
    *,
    limit: int = 200,
    conversation_id: int | None = None,
    level: str | None = None,
    event_prefix: str | None = None,
    days: int = 2,
) -> list[dict[str, Any]]:
    """倒序读最近 N 条日志（跨日文件）。"""
    path_list: list[Path] = []
    logs_dir = _logs_dir()
    for i in range(days):
        day = (datetime.now().date() - _timedelta_days(i)).strftime("%Y-%m-%d")
        p = logs_dir / f"{day}.jsonl"
        if p.exists():
            path_list.append(p)

    min_level = LEVEL_ORDER.get(level, 0) if level else 0
    out: list[dict[str, Any]] = []
    # 倒序扫文件，最新的在顶
    for p in path_list:
        try:
            with open(p, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if _is_test_artifact(rec):
                continue
            if conversation_id is not None and rec.get("conversation_id") != conversation_id:
                continue
            if min_level and LEVEL_ORDER.get(rec.get("level", ""), 0) < min_level:
                continue
            if event_prefix and not str(rec.get("event", "")).startswith(event_prefix):
                continue
            out.append(rec)
            if len(out) >= limit:
                return out
    return out


def _is_test_artifact(rec: dict[str, Any]) -> bool:
    text = "\n".join(
        str(rec.get(key, ""))
        for key in ("traceback", "error_msg", "error", "event", "model")
    )
    return "/Python/tests/" in text or "Python/tests/" in text


def _timedelta_days(n: int):
    from datetime import timedelta
    return timedelta(days=n)


def stats(days: int = 1) -> dict[str, Any]:
    """简单聚合：过去 N 天 error/warn 计数、top events。"""
    errors = 0
    warns = 0
    events: dict[str, int] = {}
    for rec in read_recent(limit=10000, days=days):
        lv = rec.get("level", "")
        if lv == "error":
            errors += 1
        elif lv == "warn":
            warns += 1
        ev = rec.get("event", "")
        events[ev] = events.get(ev, 0) + 1
    top_events = sorted(events.items(), key=lambda x: -x[1])[:10]
    return {
        "days": days,
        "errors": errors,
        "warns": warns,
        "topEvents": [{"event": e, "count": c} for e, c in top_events],
        "level": _current_level(),
        "retentionDays": _retention_days(),
    }
