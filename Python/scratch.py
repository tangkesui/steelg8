"""
Scratch（便签）极简版
------------------------

之前是时间轴 + 每条四动作的结构，用下来觉得太重。现在就是一个单体文本文件，
存在 `~/.steelg8/notepad.txt`，前端一个 textarea 实时同步。

API：
  read() -> str     读全文
  write(text: str)  覆盖写（夹到 0600 权限）
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

_LOCK = threading.Lock()


def note_file() -> Path:
    p = Path(os.environ.get("STEELG8_NOTE_PATH", Path.home() / ".steelg8" / "notepad.txt"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def read() -> str:
    p = note_file()
    if not p.exists():
        # 首次启动时尝试从老的 scratch.jsonl 迁一次（保留用户之前的 active 条目）
        _migrate_from_jsonl(p)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def write(text: str) -> None:
    p = note_file()
    tmp = p.with_suffix(".txt.tmp")
    with _LOCK:
        tmp.write_text(text or "", encoding="utf-8")
        tmp.replace(p)
        try:
            p.chmod(0o600)
        except OSError:
            pass


def _migrate_from_jsonl(target: Path) -> None:
    """一次性：把老的 time-axis scratch.jsonl 里活着的条目拼成纯文本写到 notepad.txt。
    以后这个 JSONL 文件可以留着也可以手删，本模块不再读。"""
    import json
    old = Path.home() / ".steelg8" / "scratch.jsonl"
    if not old.exists():
        return
    latest: dict[str, dict] = {}
    order: list[str] = []
    try:
        with open(old, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = str(rec.get("id") or "")
                if not sid:
                    continue
                if sid not in latest:
                    order.append(sid)
                latest[sid] = rec
    except OSError:
        return

    kept: list[str] = []
    for sid in order:
        rec = latest.get(sid) or {}
        if rec.get("status") == "archived":
            continue
        text = str(rec.get("text") or "").strip()
        if text:
            kept.append(text)

    if kept:
        target.write_text("\n\n---\n\n".join(kept), encoding="utf-8")
        try:
            target.chmod(0o600)
        except OSError:
            pass
