"""
SQLite 向量存储 + 纯 Python 余弦 top-K
---------------------------------------

Phase 2 Step 1 先不装 Qdrant，放一个最小可用的方案：
- SQLite 存 {project_id, rel_path, chunk_idx, text, embedding_bytes, tokens, metadata}
- file_manifest 记录每个文件的 size/mtime/hash，用于增量索引
- embedding 存成 float32 little-endian bytes（比 json 紧凑 ~4x）
- 检索时把所有 chunks 读出来，内存里算余弦，选 top-K

性能：每条 1024 维 float32 = 4KB；1 万条 = 40MB，完全 hold 得住。
超过 10 万条再考虑 faiss 或 Qdrant。
"""

from __future__ import annotations

import math
import os
import json
import sqlite3
import struct
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path.home() / ".steelg8" / "vectors.db"

_LOCK = threading.Lock()


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        should_suppress = super().__exit__(exc_type, exc, tb)
        self.close()
        return bool(should_suppress)


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
    embed_model: str
    chunk_count: int


@dataclass
class Hit:
    rel_path: str
    chunk_idx: int
    text: str
    score: float
    source_path: str = ""
    page: int | None = None
    heading: str = ""
    paragraph_idx: int = 0
    start_char: int = 0
    end_char: int = 0
    content_hash: str = ""
    source_type: str = "project"
    retrieval: str = "vector"
    metadata: dict[str, Any] | None = None

    def citation(self) -> dict[str, Any]:
        return {
            "sourcePath": self.source_path or self.rel_path,
            "relPath": self.rel_path,
            "page": self.page,
            "heading": self.heading,
            "paragraphIndex": self.paragraph_idx,
            "charStart": self.start_char,
            "charEnd": self.end_char,
            "contentHash": self.content_hash,
            "sourceType": self.source_type,
            "retrieval": self.retrieval,
        }


@dataclass
class FileManifest:
    project_id: int
    rel_path: str
    size: int
    mtime: float
    content_hash: str
    text_hash: str
    chunk_count: int
    embed_model: str
    indexed_at: str
    parser_diagnostics: dict[str, Any] | None = None


# ---- 初始化 ----


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), factory=_ClosingConnection)
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
                embed_dims   INTEGER DEFAULT 1024,
                embed_model  TEXT DEFAULT ''
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
                source_path  TEXT DEFAULT '',
                page         INTEGER,
                heading      TEXT DEFAULT '',
                paragraph_idx INTEGER DEFAULT 0,
                start_char   INTEGER DEFAULT 0,
                end_char     INTEGER DEFAULT 0,
                content_hash TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '',
                FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS file_manifest (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id   INTEGER NOT NULL,
                rel_path     TEXT NOT NULL,
                size         INTEGER NOT NULL,
                mtime        REAL NOT NULL,
                content_hash TEXT NOT NULL,
                text_hash    TEXT DEFAULT '',
                chunk_count  INTEGER DEFAULT 0,
                embed_model  TEXT DEFAULT '',
                diagnostics_json TEXT DEFAULT '',
                indexed_at   TEXT NOT NULL,
                UNIQUE(project_id, rel_path),
                FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(project_id, rel_path);
            CREATE INDEX IF NOT EXISTS idx_manifest_project ON file_manifest(project_id);
        """)
        # 老库迁移：给 project 表补 embed_model 列
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(project)").fetchall()]
        if "embed_model" not in cols:
            conn.execute("ALTER TABLE project ADD COLUMN embed_model TEXT DEFAULT ''")
        chunk_cols = [r["name"] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()]
        chunk_migrations = {
            "source_path": "ALTER TABLE chunks ADD COLUMN source_path TEXT DEFAULT ''",
            "page": "ALTER TABLE chunks ADD COLUMN page INTEGER",
            "heading": "ALTER TABLE chunks ADD COLUMN heading TEXT DEFAULT ''",
            "paragraph_idx": "ALTER TABLE chunks ADD COLUMN paragraph_idx INTEGER DEFAULT 0",
            "start_char": "ALTER TABLE chunks ADD COLUMN start_char INTEGER DEFAULT 0",
            "end_char": "ALTER TABLE chunks ADD COLUMN end_char INTEGER DEFAULT 0",
            "content_hash": "ALTER TABLE chunks ADD COLUMN content_hash TEXT DEFAULT ''",
            "metadata_json": "ALTER TABLE chunks ADD COLUMN metadata_json TEXT DEFAULT ''",
        }
        for col, sql in chunk_migrations.items():
            if col not in chunk_cols:
                conn.execute(sql)
        manifest_cols = [r["name"] for r in conn.execute("PRAGMA table_info(file_manifest)").fetchall()]
        if "diagnostics_json" not in manifest_cols:
            conn.execute("ALTER TABLE file_manifest ADD COLUMN diagnostics_json TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_heading ON chunks(project_id, heading)")
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
        embed_model=row["embed_model"] or "",
        chunk_count=row["chunk_count"] or 0,
    )


def mark_indexed(project_id: int, embed_model: str = "") -> None:
    """标记索引完成，同时钉上用的 embedding 模型名，用于后续 query 校验。"""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK, _connect() as conn:
        if embed_model:
            conn.execute(
                "UPDATE project SET indexed_at = ?, embed_model = ? WHERE id = ?",
                (now, embed_model, project_id),
            )
        else:
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
            embed_dims=r["embed_dims"],
            embed_model=r["embed_model"] if "embed_model" in r.keys() else "",
            chunk_count=r["chunk_count"] or 0,
        )
        for r in rows
    ]


def delete_project(project_id: int) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM file_manifest WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM chunks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM project WHERE id = ?", (project_id,))
        conn.commit()


def rename_project(project_id: int, new_name: str) -> ProjectRow | None:
    init()
    new_name = (new_name or "").strip()
    if not new_name:
        return None
    with _LOCK, _connect() as conn:
        conn.execute("UPDATE project SET name = ? WHERE id = ?", (new_name[:200], project_id))
        conn.commit()
    return get_project_by_id(project_id)


def get_project_by_id(project_id: int) -> ProjectRow | None:
    init()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            """SELECT p.*, COUNT(c.id) AS chunk_count
               FROM project p LEFT JOIN chunks c ON c.project_id = p.id
               WHERE p.id = ? GROUP BY p.id""",
            (int(project_id),),
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
        embed_model=row["embed_model"] or "",
        chunk_count=row["chunk_count"] or 0,
    )


# ---- chunks CRUD ----


def replace_chunks(
    project_id: int,
    rows: list[Any],
) -> None:
    """覆盖某个 project 的所有 chunks。rows = (rel_path, chunk_idx, text, embedding, tokens)。"""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM chunks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM file_manifest WHERE project_id = ?", (project_id,))
        conn.executemany(
            """INSERT INTO chunks
               (project_id, rel_path, chunk_idx, text, embedding, tokens, updated_at,
                source_path, page, heading, paragraph_idx, start_char, end_char,
                content_hash, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                _insert_tuple(project_id, row, now)
                for row in rows
            ],
        )
        conn.commit()


def clear_project_index(project_id: int) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM chunks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM file_manifest WHERE project_id = ?", (project_id,))
        conn.commit()


def replace_file_chunks(
    project_id: int,
    rel_path: str,
    rows: list[Any],
    *,
    size: int,
    mtime: float,
    content_hash: str,
    text_hash: str,
    embed_model: str,
    parser_diagnostics: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK, _connect() as conn:
        conn.execute(
            "DELETE FROM chunks WHERE project_id = ? AND rel_path = ?",
            (project_id, rel_path),
        )
        conn.executemany(
            """INSERT INTO chunks
               (project_id, rel_path, chunk_idx, text, embedding, tokens, updated_at,
                source_path, page, heading, paragraph_idx, start_char, end_char,
                content_hash, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [_insert_tuple(project_id, row, now) for row in rows],
        )
        conn.execute(
            """INSERT INTO file_manifest
               (project_id, rel_path, size, mtime, content_hash, text_hash,
                chunk_count, embed_model, diagnostics_json, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, rel_path) DO UPDATE SET
                 size=excluded.size,
                 mtime=excluded.mtime,
                 content_hash=excluded.content_hash,
                 text_hash=excluded.text_hash,
                 chunk_count=excluded.chunk_count,
                 embed_model=excluded.embed_model,
                 diagnostics_json=excluded.diagnostics_json,
                 indexed_at=excluded.indexed_at""",
            (
                project_id,
                rel_path,
                int(size),
                float(mtime),
                content_hash,
                text_hash,
                len(rows),
                embed_model,
                _json_dumps(parser_diagnostics or {}),
                now,
            ),
        )
        conn.commit()


def update_file_manifest(
    project_id: int,
    rel_path: str,
    *,
    size: int,
    mtime: float,
    content_hash: str,
    text_hash: str,
    chunk_count: int,
    embed_model: str,
    parser_diagnostics: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK, _connect() as conn:
        conn.execute(
            """INSERT INTO file_manifest
               (project_id, rel_path, size, mtime, content_hash, text_hash,
                chunk_count, embed_model, diagnostics_json, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, rel_path) DO UPDATE SET
                 size=excluded.size,
                 mtime=excluded.mtime,
                 content_hash=excluded.content_hash,
                 text_hash=excluded.text_hash,
                 chunk_count=excluded.chunk_count,
                 embed_model=excluded.embed_model,
                 diagnostics_json=excluded.diagnostics_json,
                 indexed_at=excluded.indexed_at""",
            (
                project_id,
                rel_path,
                int(size),
                float(mtime),
                content_hash,
                text_hash,
                int(chunk_count),
                embed_model,
                _json_dumps(parser_diagnostics or {}),
                now,
            ),
        )
        conn.commit()


def delete_file_chunks(project_id: int, rel_path: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "DELETE FROM chunks WHERE project_id = ? AND rel_path = ?",
            (project_id, rel_path),
        )
        conn.execute(
            "DELETE FROM file_manifest WHERE project_id = ? AND rel_path = ?",
            (project_id, rel_path),
        )
        conn.commit()


def list_manifest(project_id: int) -> dict[str, FileManifest]:
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM file_manifest WHERE project_id = ?""",
            (project_id,),
        ).fetchall()
    return {
        r["rel_path"]: FileManifest(
            project_id=int(r["project_id"]),
            rel_path=r["rel_path"],
            size=int(r["size"]),
            mtime=float(r["mtime"]),
            content_hash=r["content_hash"],
            text_hash=r["text_hash"] or "",
            chunk_count=int(r["chunk_count"] or 0),
            embed_model=r["embed_model"] or "",
            indexed_at=r["indexed_at"],
            parser_diagnostics=_json_loads(r["diagnostics_json"] if "diagnostics_json" in r.keys() else ""),
        )
        for r in rows
    }


def count_chunks(project_id: int) -> int:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def _insert_tuple(project_id: int, row: Any, updated_at: str) -> tuple[Any, ...]:
    rel, idx, text, vec, tokens, metadata = _normalize_chunk_row(row)
    return (
        project_id,
        rel,
        idx,
        text,
        _vec_to_bytes(vec),
        tokens,
        updated_at,
        metadata.get("source_path") or rel,
        metadata.get("page"),
        metadata.get("heading") or "",
        int(metadata.get("paragraph_idx") or 0),
        int(metadata.get("start_char") or 0),
        int(metadata.get("end_char") or 0),
        metadata.get("content_hash") or "",
        _json_dumps(metadata),
    )


def _normalize_chunk_row(row: Any) -> tuple[str, int, str, list[float], int, dict[str, Any]]:
    if isinstance(row, dict):
        rel = str(row["rel_path"])
        idx = int(row["chunk_idx"])
        text = str(row["text"])
        vec = row["embedding"]
        tokens = int(row.get("tokens") or 0)
        metadata = dict(row.get("metadata") or {})
    else:
        values = tuple(row)
        if len(values) == 5:
            rel, idx, text, vec, tokens = values
            metadata = {}
        elif len(values) == 6:
            rel, idx, text, vec, tokens, metadata = values
            metadata = dict(metadata or {})
        else:
            raise ValueError("chunk row must have 5 or 6 items")
    metadata.setdefault("source_path", rel)
    metadata.setdefault("content_hash", "")
    metadata.setdefault("end_char", len(text))
    return str(rel), int(idx), str(text), list(vec), int(tokens), metadata


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
            """SELECT rel_path, chunk_idx, text, embedding, source_path, page,
                      heading, paragraph_idx, start_char, end_char, content_hash,
                      metadata_json
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
        _hit_from_row(r, round(s, 4), retrieval="vector")
        for s, r in hits[:top_k]
    ]


def keyword_search(project_id: int, query: str, top_k: int = 5) -> list[Hit]:
    terms = _terms(query)
    if not terms:
        return []
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """SELECT rel_path, chunk_idx, text, source_path, page, heading,
                      paragraph_idx, start_char, end_char, content_hash,
                      metadata_json
               FROM chunks WHERE project_id = ?""",
            (project_id,),
        ).fetchall()
    total_docs = max(1, len(rows))
    doc_freq: dict[str, int] = {}
    row_terms: list[tuple[sqlite3.Row, list[str]]] = []
    for r in rows:
        toks = _terms(r["text"])
        row_terms.append((r, toks))
        unique = set(toks)
        for term in terms:
            if term in unique:
                doc_freq[term] = doc_freq.get(term, 0) + 1

    scored: list[tuple[float, sqlite3.Row]] = []
    avg_len = sum(len(toks) for _, toks in row_terms) / total_docs if row_terms else 1.0
    for r, toks in row_terms:
        if not toks:
            continue
        score = 0.0
        length = len(toks)
        for term in terms:
            tf = toks.count(term)
            if tf == 0:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            denom = tf + 1.2 * (1 - 0.75 + 0.75 * length / max(avg_len, 1.0))
            score += idf * (tf * 2.2) / denom
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [_hit_from_row(r, round(score, 4), retrieval="keyword") for score, r in scored[:top_k]]


def filename_search(project_id: int, query: str, top_k: int = 5) -> list[Hit]:
    terms = _terms(query)
    if not terms:
        return []
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """SELECT rel_path, chunk_idx, text, source_path, page, heading,
                      paragraph_idx, start_char, end_char, content_hash,
                      metadata_json
               FROM chunks WHERE project_id = ?""",
            (project_id,),
        ).fetchall()
    scored: list[tuple[float, sqlite3.Row]] = []
    for r in rows:
        haystack = " ".join([
            str(r["rel_path"] or ""),
            str(r["heading"] or ""),
        ]).lower()
        score = 0.0
        for term in terms:
            if term in haystack:
                score += 1.0 + min(2.0, len(term) / 8)
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    out: list[Hit] = []
    for score, r in scored:
        key = f"{r['rel_path']}:{r['chunk_idx']}"
        if key in seen:
            continue
        seen.add(key)
        out.append(_hit_from_row(r, round(score, 4), retrieval="title"))
        if len(out) >= top_k:
            break
    return out


def _hit_from_row(row: sqlite3.Row, score: float, *, retrieval: str) -> Hit:
    metadata = _metadata_from_row(row)
    return Hit(
        rel_path=row["rel_path"],
        chunk_idx=int(row["chunk_idx"]),
        text=row["text"],
        score=score,
        source_path=row["source_path"] or row["rel_path"],
        page=int(row["page"]) if row["page"] is not None else None,
        heading=row["heading"] or "",
        paragraph_idx=int(row["paragraph_idx"] or 0),
        start_char=int(row["start_char"] or 0),
        end_char=int(row["end_char"] or 0),
        content_hash=row["content_hash"] or "",
        source_type=str(metadata.get("source_type") or "project"),
        retrieval=retrieval,
        metadata=metadata,
    )


def _metadata_from_row(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["metadata_json"] if "metadata_json" in row.keys() else ""
    return _json_loads(raw)


def _json_loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)


def _terms(text: str) -> list[str]:
    import re

    raw = (text or "").lower()
    terms = re.findall(r"[\w\u4e00-\u9fff]{2,}", raw)
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", raw)
    for token in cjk:
        if len(token) > 2:
            terms.extend(token[i : i + 2] for i in range(len(token) - 1))
    return terms
