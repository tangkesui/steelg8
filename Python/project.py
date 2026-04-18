"""
项目状态管理 + 索引编排
-------------------------

Phase 2 Step 1：
- 单项目模式：任意时刻只激活一个项目（路径存在 ~/.steelg8/active_project.json）
- 打开项目 = 确认路径 → upsert 到 vectordb → 触发全量索引（后台线程）
- 索引完成 → mark_indexed，chunk_count 显示给 UI
- RAG 检索时读 active_project.id 过滤 chunks

索引流程：
  walk_project → 收集文件清单
  逐文件 read_text + chunk_text → 切块
  按 batch 调 embedding.embed → 拿到向量
  vectordb.replace_chunks 覆盖写入
  mark_indexed + 更新 status
"""

from __future__ import annotations

import json
import os
import threading
import traceback
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import embedding
import extract
import rerank
import vectordb
from providers import ProviderRegistry


ACTIVE_PATH = Path(os.environ.get(
    "STEELG8_ACTIVE_PROJECT_PATH",
    Path.home() / ".steelg8" / "active_project.json",
))


# ---- 索引状态（进程内）----


@dataclass
class IndexStatus:
    state: str = "idle"    # "idle" | "running" | "done" | "error"
    project_path: str = ""
    project_id: int = 0
    total_files: int = 0
    indexed_files: int = 0
    total_chunks: int = 0
    embedded_chunks: int = 0
    embed_tokens: int = 0
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


_STATUS = IndexStatus()
_STATUS_LOCK = threading.Lock()


def status() -> dict[str, Any]:
    with _STATUS_LOCK:
        return _STATUS.snapshot()


def _update_status(**kwargs: Any) -> None:
    with _STATUS_LOCK:
        for k, v in kwargs.items():
            setattr(_STATUS, k, v)


# ---- active project state ----


def get_active() -> dict[str, Any] | None:
    if not ACTIVE_PATH.exists():
        return None
    try:
        return json.loads(ACTIVE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def set_active(project: vectordb.ProjectRow) -> None:
    ACTIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": project.id,
        "path": project.path,
        "name": project.name,
    }
    ACTIVE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def clear_active() -> None:
    try:
        ACTIVE_PATH.unlink()
    except FileNotFoundError:
        pass


# ---- 打开项目 + 索引 ----


def open_project(
    path: str,
    registry: ProviderRegistry,
    *,
    rebuild: bool = True,
    embed_dims: int = 1024,
) -> vectordb.ProjectRow:
    """确认路径，注册到 vectordb，异步启动索引。返回 project 快照（chunk_count 可能还是 0）。"""
    abs_path = str(Path(path).expanduser().resolve())
    if not Path(abs_path).is_dir():
        raise ValueError(f"不是一个目录：{abs_path}")

    project_id = vectordb.upsert_project(abs_path, embed_dims=embed_dims)
    project = vectordb.get_project(abs_path)
    assert project is not None
    set_active(project)

    # 已经在跑就不重复启动
    if _STATUS.state == "running" and _STATUS.project_id == project.id:
        return project

    # 启动后台索引
    if rebuild or project.chunk_count == 0:
        t = threading.Thread(
            target=_run_index_job,
            args=(project, registry),
            daemon=True,
        )
        t.start()

    return project


def close_project() -> None:
    clear_active()
    _update_status(state="idle")


def _run_index_job(project: vectordb.ProjectRow, registry: ProviderRegistry) -> None:
    _update_status(
        state="running",
        project_path=project.path,
        project_id=project.id,
        total_files=0,
        indexed_files=0,
        total_chunks=0,
        embedded_chunks=0,
        embed_tokens=0,
        error=None,
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        finished_at=None,
    )
    try:
        # 1) 遍历文件
        files = list(extract.walk_project(project.path))
        _update_status(total_files=len(files))

        # 2) 对每个文件切块
        all_chunks: list[extract.Chunk] = []
        for i, fref in enumerate(files, 1):
            text = extract.read_text(fref.abs_path)
            chs = extract.chunk_text(text, fref.rel_path)
            all_chunks.extend(chs)
            _update_status(indexed_files=i, total_chunks=len(all_chunks))

        if not all_chunks:
            vectordb.replace_chunks(project.id, [])
            vectordb.mark_indexed(project.id, embed_model=embedding.DEFAULT_MODEL)
            _update_status(
                state="done",
                finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            return

        # 3) 批量 embedding
        vectors: list[list[float]] = []
        tokens_total = 0
        for batch_start in range(0, len(all_chunks), 10):
            batch = all_chunks[batch_start : batch_start + 10]
            res = embedding.embed([c.text for c in batch], registry)
            vectors.extend(res.vectors)
            tokens_total += int(res.usage.get("total_tokens") or 0)
            _update_status(
                embedded_chunks=len(vectors),
                embed_tokens=tokens_total,
            )

        # 4) 写库（覆盖）
        rows = [
            (c.rel_path, c.chunk_idx, c.text, vec, c.approx_tokens)
            for c, vec in zip(all_chunks, vectors)
        ]
        vectordb.replace_chunks(project.id, rows)
        vectordb.mark_indexed(project.id, embed_model=embedding.DEFAULT_MODEL)

        _update_status(
            state="done",
            finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    except Exception as exc:  # noqa: BLE001
        _update_status(
            state="error",
            error=f"{exc.__class__.__name__}: {exc}",
            finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        import sys
        sys.stderr.write("[steelg8 project index error]\n")
        traceback.print_exc()


# ---- 检索入口 ----


def retrieve(
    query: str,
    registry: ProviderRegistry,
    *,
    top_k: int = 5,
    min_score: float = 0.4,
    coarse_k: int = 15,
    use_rerank: bool = True,
    query_timeout: int = 10,
) -> list[vectordb.Hit]:
    """给用户的 query embedding 后在当前 active project 里检索。

    流程：
      1. embed(query) —— 短超时，失败就放弃 RAG
      2. vectordb top-K 粗排（默认 15 条候选）
      3. 若 use_rerank：调 qwen3-rerank 重排，取 top-K
      4. 按 min_score 过滤
    """
    active = get_active()
    if not active:
        return []
    project_id = active["id"]

    # 还在索引中且一条 embedding 都没出来 → 跳过
    if (
        _STATUS.state == "running"
        and _STATUS.project_id == project_id
        and _STATUS.embedded_chunks == 0
    ):
        return []

    # 模型一致性校验：索引时用的模型名要和 query 用的一致
    proj = vectordb.get_project(active["path"])
    if proj and proj.embed_model and proj.embed_model != embedding.DEFAULT_MODEL:
        import sys
        sys.stderr.write(
            f"[steelg8] embedding model 不一致 · 索引时={proj.embed_model} "
            f"· 当前={embedding.DEFAULT_MODEL}。该项目的 RAG 召回已停用，"
            f"请重新索引。\n"
        )
        return []

    try:
        q_vec = embedding.embed_one(query, registry, timeout=query_timeout)
    except embedding.EmbeddingError as exc:
        import sys
        sys.stderr.write(f"[steelg8] query embedding 失败: {exc}\n")
        return []

    coarse = vectordb.search(project_id, q_vec, top_k=max(coarse_k, top_k))
    if not coarse:
        return []

    if use_rerank and len(coarse) > 1:
        try:
            pairs = rerank.rerank(
                query,
                [h.text for h in coarse],
                registry,
                top_n=top_k,
            )
            if pairs:
                reranked: list[vectordb.Hit] = []
                for idx, score in pairs:
                    if 0 <= idx < len(coarse):
                        h = coarse[idx]
                        reranked.append(
                            vectordb.Hit(
                                rel_path=h.rel_path,
                                chunk_idx=h.chunk_idx,
                                text=h.text,
                                score=round(score, 4),
                            )
                        )
                return [h for h in reranked[:top_k] if h.score >= min_score]
        except rerank.RerankError as exc:
            import sys
            sys.stderr.write(
                f"[steelg8] rerank 失败，回退到 embedding 粗排: {exc}\n"
            )

    return [h for h in coarse[:top_k] if h.score >= min_score]


def active_project_summary() -> dict[str, Any] | None:
    active = get_active()
    if not active:
        return None
    proj = vectordb.get_project(active["path"])
    if not proj:
        return None
    return {
        "id": proj.id,
        "path": proj.path,
        "name": proj.name,
        "indexedAt": proj.indexed_at,
        "chunkCount": proj.chunk_count,
        "embedDims": proj.embed_dims,
        "indexStatus": status(),
    }
