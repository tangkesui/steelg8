"""
SQLite 向量存储 + 纯 Python 余弦 top-K
---------------------------------------

Phase 2 Step 1 先不装 Qdrant，放一个最小可用的方案：
- SQLite 存 {project_id, rel_path, chunk_idx, text, embedding_bytes, tokens, mtime}
- embedding 存成 float32 little-endian bytes（比 json 紧凑 ~4x）
- 检索时把所有 chunks 读出来，内存里算余弦，选 top-K

性能：每条 1024 维 float32 = 4KB；1 万条 = 40MB，完全 hold 得住。
超过 10 万条再考虑 faiss 或 Qdrant。
"""

from __future__ import annotations

import math
import os
import sqlite3
import struct
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path.home() / ".steelg8" / "vectors.db"

_LOCK = threading.Lock()


def db_path() -> Path:
    p = Path(os.environ.get("STEELG8_VECTORS_DB", DEFAULT_DB_PATH))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _vec_to_bytes(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _bytes_to_vec(b: bytes) -> list[float]:
    n = len(b) // 4
    return list(struct.unpack(f"{n}f", b))


@dataclass
class ProjectRow:
    id: int
    path: str
    name: str
    created_at: str
    indexed_at: str | None
    embed_dims: int
    chunk_count: int


@dataclass
class Hit:
    rel_path: str
    chunk_idx: int
    text: str
    score: float


# ---- 初始化 ----


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _LOCK, _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS project (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                path         TEXT UNIQUE NOT NULL,
                name         TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                indexed_at   TEXT,
                embed_dims   INTEGER DEFAULT 1024
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id   INTEGER NOT NULL,
                rel_path     TEXT NOT NULL,
                chunk_idx    INTEGER NOT NULL,
                text         TEXT NOT NULL,
                embedding    BLOB NOT NULL,
                tokens       INTEGER DEFAULT 0,
                updated_at   TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(project_id, rel_path);
        """)
        conn.commit()


# ---- project CRUD ----


def upsert_project(path: str, name: str | None = None, embed_dims: int = 1024) -> int:
    """按 path 幂等创建 project，返回 id。"""
    init()
    name = name or Path(path).name
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT id FROM project WHERE path = ?", (path,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO project (path, name, created_at, embed_dims) VALUES (?, ?, ?, ?)",
            (path, name, now, embed_dims),
        )
        conn.commit()
        return cur.lastrowid


def get_project(path: str) -> ProjectRow | None:
    init()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            """SELECT p.*, COUNT(c.id) AS chunk_count
               FROM project p LEFT JOIN chunks c ON c.project_id = p.id
               WHERE p.path = ? GROUP BY p.id""",
            (path,),
        ).fetchone()
    if row is None:
        return None
    return ProjectRow(
        id=row["id"],
        path=row["path"],
        name=row["name"],
        created_at=row["created_at"],
        indexed_at=row["indexed_at"],
        embed_dims=row["embed_dims"],
        chunk_count=row["chunk_count"] or 0,
    )


def mark_indexed(project_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE project SET indexed_at = ? WHERE id = ?",
            (now, project_id),
        )
        conn.commit()


def list_projects() -> list[ProjectRow]:
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute("""
            SELECT p.*, COUNT(c.id) AS chunk_count
            FROM project p LEFT JOIN chunks c ON c.project_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """).fetchall()
    return [
        ProjectRow(
            id=r["id"], path=r["path"], name=r["name"],
            created_at=r["created_at"], indexed_at=r["indexed_at"],
            embed_dims=r["embed_dims"], chunk_count=r["chunk_count"] or 0,
        )
        for r in rows
    ]


def delete_project(project_id: int) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM chunks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM project WHERE id = ?", (project_id,))
        conn.commit()


# ---- chunks CRUD ----


def replace_chunks(
    project_id: int,
    rows: list[tuple[str, int, str, list[float], int]],
) -> None:
    """覆盖某个 project 的所有 chunks。rows = (rel_path, chunk_idx, text, embedding, tokens)。"""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM chunks WHERE project_id = ?", (project_id,))
        conn.executemany(
            """INSERT INTO chunks
               (project_id, rel_path, chunk_idx, text, embedding, tokens, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (project_id, rel, idx, text, _vec_to_bytes(vec), tokens, now)
                for (rel, idx, text, vec, tokens) in rows
            ],
        )
        conn.commit()


def count_chunks(project_id: int) -> int:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


# ---- 检索 ----


def search(project_id: int, query_vec: list[float], top_k: int = 5) -> list[Hit]:
    """纯 Python 余弦相似度 top-K。适合 <10k chunks。"""
    if not query_vec:
        return []

    q_norm = math.sqrt(sum(x * x for x in query_vec))
    if q_norm == 0:
        return []

    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """SELECT rel_path, chunk_idx, text, embedding
               FROM chunks WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

    hits: list[tuple[float, sqlite3.Row]] = []
    for r in rows:
        vec = _bytes_to_vec(r["embedding"])
        if len(vec) != len(query_vec):
            continue
        # 点积
        dot = sum(a * b for a, b in zip(vec, query_vec))
        n = math.sqrt(sum(a * a for a in vec))
        if n == 0:
            continue
        score = dot / (q_norm * n)
        hits.append((score, r))

    hits.sort(key=lambda x: -x[0])
    return [
        Hit(
            rel_path=r["rel_path"],
            chunk_idx=r["chunk_idx"],
            text=r["text"],
            score=round(s, 4),
        )
        for s, r in hits[:top_k]
    ]
