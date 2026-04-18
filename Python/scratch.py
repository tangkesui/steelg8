"""
Scratch（捕获台）持久化
-------------------------

产品方案 §7.3：时间轴模式，新内容追加到底部。数据由 main app 侧栏和
⌘⇧N 召唤窗共享。

存储：~/.steelg8/scratch.jsonl 一行一条。最新在文件底部。

一条 entry：
  id        UUID4 字符串
  ts        ISO8601（秒精度，UTC）
  text      正文（不限长度，不做 markdown 解析）
  origin    "manual" | "hotkey" | "ai-organize" | "ocr" | ...
  tags      list[str]，可选
  status    "active" | "archived"（删除走 archived 软删除）
  saved     bool（是否已"存为知识卡片"；真正的知识库 Phase 2 接）

设计决策：
- JSONL 而非 SQLite，因为 Phase 1 数据量远小于单进程追加的极限，
  append-only 天生并发安全，崩溃也只会丢最后一行未 flush 的记录
- 删除走软删除：status 置 archived，不物理抹除，给将来"回收站"留口
- 文件锁：进程内用 threading.Lock；不跨进程（假设 kernel 只有一个实例）
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


_LOCK = threading.Lock()


def scratch_file() -> Path:
    p = Path(os.environ.get("STEELG8_SCRATCH_PATH", Path.home() / ".steelg8" / "scratch.jsonl"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.expanduser()


@dataclass
class ScratchEntry:
    id: str
    ts: str
    text: str
    origin: str = "manual"
    tags: list[str] = field(default_factory=list)
    status: str = "active"
    saved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iter_all() -> Iterator[ScratchEntry]:
    path = scratch_file()
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                yield ScratchEntry(
                    id=str(raw.get("id") or uuid.uuid4()),
                    ts=str(raw.get("ts") or ""),
                    text=str(raw.get("text") or ""),
                    origin=str(raw.get("origin") or "manual"),
                    tags=list(raw.get("tags") or []),
                    status=str(raw.get("status") or "active"),
                    saved=bool(raw.get("saved", False)),
                )
            except (json.JSONDecodeError, TypeError):
                continue


def _append_line(entry: ScratchEntry) -> None:
    line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
    path = scratch_file()
    with _LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()


def _rewrite_all(entries: Iterable[ScratchEntry]) -> None:
    """按"最后一次更新覆盖前面"的语义整理 jsonl：相同 id 只留末条，
    然后把"活着的"顺序写回。用于 delete / update / organize 这类
    需要合并日志的场景。"""
    latest: dict[str, ScratchEntry] = {}
    order: list[str] = []
    for e in _iter_all():
        if e.id not in latest:
            order.append(e.id)
        latest[e.id] = e

    path = scratch_file()
    tmp = path.with_suffix(".jsonl.tmp")
    with _LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            for entry in entries:
                latest[entry.id] = entry  # 以调用方给的为准
                if entry.id not in order:
                    order.append(entry.id)
            for sid in order:
                if sid in latest:
                    f.write(json.dumps(latest[sid].to_dict(), ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


# ---------- 公开 API ----------


def list_entries(include_archived: bool = False) -> list[ScratchEntry]:
    """按时间顺序返回所有 entry（最新在最后）。"""
    seen: dict[str, ScratchEntry] = {}
    order: list[str] = []
    for e in _iter_all():
        if e.id not in seen:
            order.append(e.id)
        seen[e.id] = e

    out = []
    for sid in order:
        e = seen[sid]
        if not include_archived and e.status == "archived":
            continue
        out.append(e)
    return out


def append(text: str, *, origin: str = "manual", tags: list[str] | None = None) -> ScratchEntry:
    """新增一条 entry。text 可以多行，不做裁剪。"""
    entry = ScratchEntry(
        id=str(uuid.uuid4()),
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        text=text,
        origin=origin,
        tags=list(tags or []),
    )
    _append_line(entry)
    return entry


def update(entry_id: str, *, text: str | None = None, tags: list[str] | None = None,
           saved: bool | None = None, status: str | None = None) -> ScratchEntry | None:
    """按 id 更新字段。未指定的字段保持不变。"""
    current = find(entry_id)
    if current is None:
        return None
    updated = ScratchEntry(
        id=current.id,
        ts=current.ts,   # 保留原创建时间；必要时可加一个 updated_ts 字段
        text=text if text is not None else current.text,
        origin=current.origin,
        tags=tags if tags is not None else current.tags,
        status=status if status is not None else current.status,
        saved=saved if saved is not None else current.saved,
    )
    _append_line(updated)  # append-only：同 id 多条，读取时保留最后一条
    return updated


def delete(entry_id: str) -> bool:
    """软删除 = 把 status 置为 archived。"""
    cur = find(entry_id)
    if cur is None or cur.status == "archived":
        return False
    update(entry_id, status="archived")
    return True


def find(entry_id: str) -> ScratchEntry | None:
    cur: ScratchEntry | None = None
    for e in _iter_all():
        if e.id == entry_id:
            cur = e  # 保留最后一条
    return cur


def compact() -> int:
    """合并同 id 的历史条目，物理抹掉 archived。返回最终条数。"""
    entries = list_entries(include_archived=False)
    _rewrite_all(entries)
    return len(entries)
