"""
steelg8 · 会话持久化（L4 记忆层）
--------------------------------

SQLite 存 conversations 与 messages 两张表。所有 /chat 都挂在某个
conversation 上；历史从 DB 读，不再依赖前端 history 数组。

Schema：
  conversations (id, title, project_root, created_at, updated_at,
                 summary, summary_tokens, last_compressed_at, archived)
  messages      (id, conversation_id, role, content, name,
                 tool_calls_json, tool_call_id, tokens,
                 compressed, created_at)

约定：
- `compressed=1` 表示这条消息已被压缩进 summary，LLM payload 不再带上
  原文，但前端历史仍可查看。
- `summary` 是 **所有已压缩消息** 的滚动摘要；每次再压缩时把新一批
  消息 + 旧 summary 一起喂给 qwen-turbo 得到新 summary。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DB_PATH = Path(
    os.environ.get(
        "STEELG8_CONVERSATIONS_DB",
        Path.home() / ".steelg8" / "conversations.db",
    )
).expanduser()


_DB_LOCK = threading.Lock()


@dataclass
class Conversation:
    id: int
    title: str
    project_root: str | None
    created_at: int
    updated_at: int
    summary: str
    summary_tokens: int
    last_compressed_at: int | None
    archived: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "projectRoot": self.project_root,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "summary": self.summary,
            "summaryTokens": self.summary_tokens,
            "lastCompressedAt": self.last_compressed_at,
            "archived": self.archived,
        }


@dataclass
class StoredMessage:
    id: int
    conversation_id: int
    role: str
    content: str
    name: str | None
    tool_calls: list[dict[str, Any]]
    tool_call_id: str | None
    tokens: int
    compressed: bool
    created_at: int

    def to_openai(self) -> dict[str, Any]:
        """转成 OpenAI chat.completions 的 message 格式。"""
        m: dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.name:
            m["name"] = self.name
        if self.role == "assistant" and self.tool_calls:
            m["tool_calls"] = self.tool_calls
        if self.role == "tool" and self.tool_call_id:
            m["tool_call_id"] = self.tool_call_id
        return m

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversationId": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "name": self.name,
            "toolCalls": self.tool_calls,
            "toolCallId": self.tool_call_id,
            "tokens": self.tokens,
            "compressed": self.compressed,
            "createdAt": self.created_at,
        }


# ---------- infra ----------

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


_INITED = False


def _init_schema() -> None:
    global _INITED
    if _INITED:
        return
    with _DB_LOCK:
        if _INITED:
            return
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL DEFAULT '',
                    project_root TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    summary_tokens INTEGER NOT NULL DEFAULT 0,
                    last_compressed_at INTEGER,
                    archived INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    name TEXT,
                    tool_calls_json TEXT,
                    tool_call_id TEXT,
                    tokens INTEGER NOT NULL DEFAULT 0,
                    compressed INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conv
                    ON messages(conversation_id, id);
                CREATE INDEX IF NOT EXISTS idx_messages_conv_active
                    ON messages(conversation_id, compressed, id);
                CREATE INDEX IF NOT EXISTS idx_conv_updated
                    ON conversations(archived, updated_at DESC);
                """
            )
        finally:
            conn.close()
        _INITED = True


def _now() -> int:
    return int(time.time())


def _row_to_conv(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=int(row["id"]),
        title=row["title"] or "",
        project_root=row["project_root"],
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
        summary=row["summary"] or "",
        summary_tokens=int(row["summary_tokens"] or 0),
        last_compressed_at=int(row["last_compressed_at"]) if row["last_compressed_at"] is not None else None,
        archived=bool(row["archived"]),
    )


def _row_to_msg(row: sqlite3.Row) -> StoredMessage:
    tc_json = row["tool_calls_json"] or ""
    try:
        tool_calls = json.loads(tc_json) if tc_json else []
    except json.JSONDecodeError:
        tool_calls = []
    return StoredMessage(
        id=int(row["id"]),
        conversation_id=int(row["conversation_id"]),
        role=row["role"],
        content=row["content"] or "",
        name=row["name"],
        tool_calls=tool_calls or [],
        tool_call_id=row["tool_call_id"],
        tokens=int(row["tokens"] or 0),
        compressed=bool(row["compressed"]),
        created_at=int(row["created_at"]),
    )


# ---------- conversations ----------

def create_conversation(*, title: str = "", project_root: str | None = None) -> Conversation:
    _init_schema()
    now = _now()
    with _DB_LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO conversations(title, project_root, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (title.strip(), project_root, now, now),
            )
            conv_id = int(cur.lastrowid)
            row = conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conv_id,)
            ).fetchone()
            return _row_to_conv(row)
        finally:
            conn.close()


def get_conversation(conv_id: int) -> Conversation | None:
    _init_schema()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=?", (int(conv_id),)
        ).fetchone()
        return _row_to_conv(row) if row else None
    finally:
        conn.close()


def list_conversations(*, limit: int = 100, include_archived: bool = False) -> list[Conversation]:
    _init_schema()
    conn = _connect()
    try:
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE archived=0 "
                "ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [_row_to_conv(r) for r in rows]
    finally:
        conn.close()


def get_or_create_project_conversation(
    *,
    project_root: str | None,
    title: str = "",
) -> Conversation:
    """Return the single live conversation bound to a project/root.

    The UI treats one project as one durable context. We keep old conversations
    in the DB, but new chat turns without an explicit conversationId should
    attach to this canonical project conversation instead of creating another
    thread.
    """
    _init_schema()
    root = (project_root or "").strip()
    conn = _connect()
    try:
        if root:
            row = conn.execute(
                """
                SELECT * FROM conversations
                 WHERE archived=0 AND project_root=?
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                (root,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM conversations
                 WHERE archived=0 AND (project_root IS NULL OR project_root='')
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """
            ).fetchone()
        if row:
            return _row_to_conv(row)
    finally:
        conn.close()

    return create_conversation(
        title=title.strip() or ("项目对话" if root else "默认对话"),
        project_root=root or None,
    )


def rename_conversation(conv_id: int, title: str) -> Conversation | None:
    _init_schema()
    now = _now()
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                (title.strip()[:200], now, int(conv_id)),
            )
            row = conn.execute(
                "SELECT * FROM conversations WHERE id=?", (int(conv_id),)
            ).fetchone()
            return _row_to_conv(row) if row else None
        finally:
            conn.close()


def delete_conversation(conv_id: int) -> bool:
    _init_schema()
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (int(conv_id),))
            cur = conn.execute("DELETE FROM conversations WHERE id=?", (int(conv_id),))
            return cur.rowcount > 0
        finally:
            conn.close()


def update_summary(conv_id: int, *, summary: str, summary_tokens: int) -> None:
    _init_schema()
    now = _now()
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE conversations
                   SET summary=?, summary_tokens=?, last_compressed_at=?, updated_at=?
                 WHERE id=?
                """,
                (summary, int(summary_tokens), now, now, int(conv_id)),
            )
        finally:
            conn.close()


def touch(conv_id: int) -> None:
    _init_schema()
    now = _now()
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (now, int(conv_id))
            )
        finally:
            conn.close()


# ---------- messages ----------

def append_message(
    conv_id: int,
    *,
    role: str,
    content: str,
    name: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str | None = None,
    tokens: int = 0,
) -> StoredMessage:
    _init_schema()
    now = _now()
    tc_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None
    with _DB_LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO messages(conversation_id, role, content, name,
                                     tool_calls_json, tool_call_id, tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (int(conv_id), role, content or "", name, tc_json, tool_call_id,
                 int(tokens), now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (now, int(conv_id))
            )
            row = conn.execute(
                "SELECT * FROM messages WHERE id=?", (int(cur.lastrowid),)
            ).fetchone()
            return _row_to_msg(row)
        finally:
            conn.close()


def list_messages(
    conv_id: int,
    *,
    only_active: bool = False,
    limit: int | None = None,
) -> list[StoredMessage]:
    _init_schema()
    conn = _connect()
    try:
        base = "SELECT * FROM messages WHERE conversation_id=?"
        params: list[Any] = [int(conv_id)]
        if only_active:
            base += " AND compressed=0"
        base += " ORDER BY id ASC"
        if limit is not None:
            base += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(base, tuple(params)).fetchall()
        return [_row_to_msg(r) for r in rows]
    finally:
        conn.close()


def mark_messages_compressed(conv_id: int, message_ids: Iterable[int]) -> int:
    _init_schema()
    ids = [int(i) for i in message_ids]
    if not ids:
        return 0
    placeholders = ",".join(["?"] * len(ids))
    with _DB_LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                f"UPDATE messages SET compressed=1 "
                f"WHERE conversation_id=? AND id IN ({placeholders})",
                (int(conv_id), *ids),
            )
            return int(cur.rowcount)
        finally:
            conn.close()


def count_active_messages(conv_id: int) -> int:
    _init_schema()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM messages "
            "WHERE conversation_id=? AND compressed=0",
            (int(conv_id),),
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


def auto_title_from_first_user(conv_id: int, *, limit: int = 32) -> str | None:
    """如果会话还没有 title，根据第一条 user 消息生成一个 40 字内的短标题。"""
    _init_schema()
    conv = get_conversation(conv_id)
    if not conv or conv.title.strip():
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT content FROM messages "
            "WHERE conversation_id=? AND role='user' "
            "ORDER BY id ASC LIMIT 1",
            (int(conv_id),),
        ).fetchone()
        if not row:
            return None
        raw = (row["content"] or "").strip()
        if not raw:
            return None
        # 单行、去首尾空白、截断
        first_line = raw.splitlines()[0].strip()
        title = first_line[:limit] + ("…" if len(first_line) > limit else "")
        rename_conversation(conv_id, title)
        return title
    finally:
        conn.close()
