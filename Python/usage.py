"""
steelg8 token / 费用 持久化追踪
-------------------------------

- 存储：JSONL 追加到 ~/.steelg8/usage.jsonl（一行一条，time-sorted）
- 聚合：在 Python 进程内做内存缓存（今天 / 本月 / 总计）
- 多进程：文件追加天生并发安全（POSIX O_APPEND）
- 读取：/usage/summary 端点走聚合；/usage/recent 返回最近 N 条明细

每一条 record 字段：
  ts         ISO8601（秒精度）
  model      "google/gemini-2.5-flash-lite"
  provider   "openrouter"
  layer      "explicit" | "rule" | "cheap" | "fallback" | "mock"
  prompt     int（input tokens）
  completion int（output tokens）
  total      int（= prompt + completion）
  cost_usd   float（当时用 pricing 表算的）
  session    str（steelg8 进程 id，区分"本次会话"）

注：只记 token，不记 message 内容；避免日志泄露敏感信息。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pricing


_LOCK = threading.Lock()
_SESSION_ID = f"{os.getpid()}-{int(datetime.now().timestamp())}"


def usage_file() -> Path:
    p = Path(os.environ.get("STEELG8_USAGE_PATH", Path.home() / ".steelg8" / "usage.jsonl"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.expanduser()


@dataclass
class UsageRecord:
    ts: str
    model: str
    provider: str
    layer: str
    prompt: int
    completion: int
    total: int
    cost_usd: float
    session: str


def record(
    *,
    model: str,
    provider: str,
    layer: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> UsageRecord:
    """计费并追加一条。prompt_tokens 允许为 0（调用方不知道时）。"""
    prompt = int(prompt_tokens or 0)
    completion = int(completion_tokens or 0)
    cost = pricing.cost_usd(model, provider, prompt, completion)
    rec = UsageRecord(
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        model=model or "",
        provider=provider or "",
        layer=layer or "",
        prompt=prompt,
        completion=completion,
        total=prompt + completion,
        cost_usd=round(cost, 8),
        session=_SESSION_ID,
    )
    _append(rec)
    return rec


def _append(rec: UsageRecord) -> None:
    line = json.dumps(asdict(rec), ensure_ascii=False) + "\n"
    path = usage_file()
    with _LOCK:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
        except OSError as exc:
            # 失败不炸，打到 stderr 即可；计费只是辅助信息
            import sys
            sys.stderr.write(f"steelg8 usage log write failed: {exc}\n")


# ---------- 读取 / 聚合 ----------


def _iter_records() -> Iterable[UsageRecord]:
    path = usage_file()
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                yield UsageRecord(**raw)
            except (json.JSONDecodeError, TypeError):
                continue


def summary() -> dict[str, Any]:
    """聚合当前进程会话 / 今日 / 总累计的 token 和费用，带按 model 拆分。"""
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    agg_session = _empty_agg()
    agg_today = _empty_agg()
    agg_all = _empty_agg()
    per_model: dict[str, dict[str, Any]] = {}

    for rec in _iter_records():
        # 全量
        _accumulate(agg_all, rec)
        # 今天
        if rec.ts.startswith(today):
            _accumulate(agg_today, rec)
        # 当前 session
        if rec.session == _SESSION_ID:
            _accumulate(agg_session, rec)
            key = rec.model or "(unknown)"
            bucket = per_model.setdefault(
                key,
                {"model": key, "provider": rec.provider, "prompt": 0, "completion": 0, "cost_usd": 0.0, "calls": 0},
            )
            bucket["prompt"] += rec.prompt
            bucket["completion"] += rec.completion
            bucket["cost_usd"] += rec.cost_usd
            bucket["calls"] += 1

    # session 按 cost 排序方便前端展示
    session_breakdown = sorted(
        per_model.values(), key=lambda x: -x["cost_usd"]
    )
    for b in session_breakdown:
        b["cost_usd"] = round(b["cost_usd"], 6)

    return {
        "session": agg_session,
        "today": agg_today,
        "total": agg_all,
        "sessionId": _SESSION_ID,
        "sessionBreakdown": session_breakdown,
        "usdToCny": pricing.USD_TO_CNY,
    }


def recent(limit: int = 50) -> list[dict[str, Any]]:
    items = list(_iter_records())
    items = items[-limit:]
    return [asdict(r) for r in items]


def _empty_agg() -> dict[str, Any]:
    return {"prompt": 0, "completion": 0, "total": 0, "cost_usd": 0.0, "calls": 0}


def _accumulate(agg: dict[str, Any], rec: UsageRecord) -> None:
    agg["prompt"] += rec.prompt
    agg["completion"] += rec.completion
    agg["total"] += rec.total
    agg["cost_usd"] = round(agg["cost_usd"] + rec.cost_usd, 6)
    agg["calls"] += 1


def session_id() -> str:
    return _SESSION_ID
