"""
项目状态管理 + 索引编排
-------------------------

Phase 2：
- 单项目模式：任意时刻只激活一个项目（路径存在 ~/.steelg8/active_project.json）
- 打开项目 = 确认路径 → upsert 到 vectordb → 触发增量索引（后台线程）
- 索引完成 → mark_indexed，chunk_count 显示给 UI
- RAG 检索时读 active_project.id，走 vector + keyword + title + knowledge 混合召回

索引流程：
  walk_project → 收集文件清单
  对比 file_manifest → 跳过未变化文件，清理已删除文件
  变化文件 read_text + chunk_text → 切块
  按 batch 调 embedding.embed → 拿到向量
  RagStore.replace_file_chunks 按文件覆盖写入
  mark_indexed + 更新 status
"""

from __future__ import annotations

import json
import os
import threading
import traceback
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import embedding
import extract
import knowledge as knowledge_mod
import rag_store
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
    job_id: int = 0
    state: str = "idle"    # "idle" | "running" | "done" | "error"
    project_path: str = ""
    project_id: int = 0
    total_files: int = 0
    indexed_files: int = 0
    total_chunks: int = 0
    embedded_chunks: int = 0
    embed_tokens: int = 0
    skipped_files: int = 0
    deleted_files: int = 0
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


_STATUS = IndexStatus()
_STATUS_LOCK = threading.Lock()
_INDEX_JOB_SEQ = 0


def status() -> dict[str, Any]:
    with _STATUS_LOCK:
        return _STATUS.snapshot()


def _update_status(*, job_id: int | None = None, **kwargs: Any) -> bool:
    with _STATUS_LOCK:
        if job_id is not None and _STATUS.job_id != job_id:
            return False
        for k, v in kwargs.items():
            setattr(_STATUS, k, v)
        return True


def _begin_index_job(project: vectordb.ProjectRow) -> int:
    global _INDEX_JOB_SEQ
    with _STATUS_LOCK:
        _INDEX_JOB_SEQ += 1
        _STATUS.job_id = _INDEX_JOB_SEQ
        _STATUS.state = "running"
        _STATUS.project_path = project.path
        _STATUS.project_id = project.id
        _STATUS.total_files = 0
        _STATUS.indexed_files = 0
        _STATUS.total_chunks = 0
        _STATUS.embedded_chunks = 0
        _STATUS.embed_tokens = 0
        _STATUS.skipped_files = 0
        _STATUS.deleted_files = 0
        _STATUS.error = None
        _STATUS.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _STATUS.finished_at = None
        return _STATUS.job_id


def _invalidate_index_jobs(**kwargs: Any) -> None:
    global _INDEX_JOB_SEQ
    with _STATUS_LOCK:
        _INDEX_JOB_SEQ += 1
        _STATUS.job_id = _INDEX_JOB_SEQ
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


# 输出目录约定：<project_root>/steelg8-output/
OUTPUT_DIR_NAME = "steelg8-output"


def output_dir(*, ensure: bool = True) -> Path | None:
    """返回当前激活项目的输出根目录；没有激活项目返回 None。

    `ensure=True` 会自动创建 steelg8-output/ 目录。
    """
    active = get_active()
    if not active:
        return None
    root = Path(active["path"]).expanduser() / OUTPUT_DIR_NAME
    if ensure:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
    return root


def task_output_dir(task_name: str) -> Path | None:
    """
    返回 <project>/steelg8-output/<task_name>/。任务名会被标准化（空白折叠、
    非法字符替换为 _）。
    """
    root = output_dir(ensure=True)
    if not root:
        return None
    import re as _re
    safe = _re.sub(r'[\\/:*?"<>|]', "_", task_name or "未命名").strip()
    safe = _re.sub(r"\s+", " ", safe)[:80] or "未命名"
    sub = root / safe
    try:
        sub.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return sub


def next_version_path(task_name: str, ext: str = ".docx", *, label: str = "") -> Path | None:
    """
    给"任务 + 后缀"找下一个版本号路径：
      steelg8-output/<task_name>/v1.docx
      steelg8-output/<task_name>/v2-补投资估算.docx
    """
    dir_ = task_output_dir(task_name)
    if not dir_:
        return None
    # 扫描已有 vN* 文件，算最大 N
    import re as _re
    max_n = 0
    for p in dir_.glob(f"v*{ext}"):
        m = _re.match(r"^v(\d+)", p.name)
        if m:
            try:
                max_n = max(max_n, int(m.group(1)))
            except ValueError:
                pass
    n = max_n + 1
    if label:
        label_safe = _re.sub(r'[\\/:*?"<>|]', "_", label).strip()
        return dir_ / f"v{n}-{label_safe}{ext}"
    return dir_ / f"v{n}{ext}"


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

    # 自动建输出目录，所有生成文件默认落这里
    try:
        (Path(abs_path) / OUTPUT_DIR_NAME).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # 已经在跑就不重复启动
    with _STATUS_LOCK:
        running_same_project = _STATUS.state == "running" and _STATUS.project_id == project.id
    if running_same_project:
        return project

    # 启动后台索引
    if rebuild or project.chunk_count == 0:
        job_id = _begin_index_job(project)
        t = threading.Thread(
            target=_run_index_job,
            args=(project, registry, job_id),
            daemon=True,
        )
        t.start()

    return project


def close_project() -> None:
    clear_active()
    _invalidate_index_jobs(
        state="idle",
        project_id=0,
        project_path="",
        total_files=0,
        indexed_files=0,
        total_chunks=0,
        embedded_chunks=0,
        embed_tokens=0,
        skipped_files=0,
        deleted_files=0,
        error=None,
        started_at=None,
        finished_at=None,
    )


# ---- 多项目 API ----

def list_all() -> list[dict[str, Any]]:
    """返回所有历史打开过的项目，附当前激活状态。

    过滤掉 steelg8 内部 "virtual" 项目（如 knowledge 知识库）——
    这些不是用户显式打开的项目，不该混进项目列表 UI。
    """
    active = get_active()
    active_id = active.get("id") if active else None
    projects = vectordb.list_projects()
    out = []
    home = str(Path.home())
    for p in projects:
        # 跳过 ~/.steelg8 下的虚拟项目（knowledge 等）
        if p.path.startswith(str(Path(home) / ".steelg8")):
            continue
        out.append({
            "id": p.id,
            "path": p.path,
            "name": p.name,
            "createdAt": p.created_at,
            "indexedAt": p.indexed_at,
            "chunkCount": p.chunk_count,
            "embedModel": p.embed_model,
            "active": p.id == active_id,
        })
    return out


def activate_by_id(project_id: int) -> dict[str, Any] | None:
    """把某个已存在项目切成激活态。不会触发重新索引。"""
    p = vectordb.get_project_by_id(int(project_id))
    if p is None:
        return None
    set_active(p)
    return {
        "id": p.id,
        "path": p.path,
        "name": p.name,
        "chunkCount": p.chunk_count,
    }


def rename(project_id: int, new_name: str) -> dict[str, Any] | None:
    p = vectordb.rename_project(int(project_id), new_name)
    if p is None:
        return None
    # 如果改的是当前激活项目，同步更新 active.json
    active = get_active()
    if active and active.get("id") == p.id:
        set_active(p)
    return {"id": p.id, "path": p.path, "name": p.name}


def remove(project_id: int) -> bool:
    """删除项目和所有索引数据。如果是当前激活项目，一并清掉 active。"""
    p = vectordb.get_project_by_id(int(project_id))
    if p is None:
        return False
    vectordb.delete_project(int(project_id))
    active = get_active()
    if active and active.get("id") == p.id:
        clear_active()
        _invalidate_index_jobs(
            state="idle",
            project_id=0,
            project_path="",
            total_files=0,
            indexed_files=0,
            total_chunks=0,
            embedded_chunks=0,
            embed_tokens=0,
            skipped_files=0,
            deleted_files=0,
            error=None,
            started_at=None,
            finished_at=None,
        )
    return True


def _run_index_job(project: vectordb.ProjectRow, registry: ProviderRegistry, job_id: int) -> None:
    try:
        store = rag_store.default_store()

        # 1) 遍历文件，并和 manifest 对比，删除已经不存在的文件索引。
        files = list(extract.walk_project(project.path))
        if not _update_status(job_id=job_id, total_files=len(files)):
            return

        manifest = store.list_manifest(project.id)
        current_paths = {f.rel_path for f in files}
        deleted_files = 0
        for rel_path in sorted(set(manifest) - current_paths):
            store.delete_file_chunks(project.id, rel_path)
            deleted_files += 1
            if not _update_status(job_id=job_id, deleted_files=deleted_files):
                return

        indexed_files = 0
        skipped_files = 0
        total_chunks = 0
        embedded_chunks = 0
        embed_tokens = 0

        # 2) 按文件增量索引：mtime/size/model 一致则直接复用旧 chunk。
        for fref in files:
            existing = manifest.get(fref.rel_path)
            if _manifest_is_fresh(existing, fref):
                indexed_files += 1
                skipped_files += 1
                total_chunks += existing.chunk_count if existing else 0
                if not _update_status(
                    job_id=job_id,
                    indexed_files=indexed_files,
                    skipped_files=skipped_files,
                    total_chunks=total_chunks,
                    embedded_chunks=embedded_chunks,
                    embed_tokens=embed_tokens,
                ):
                    return
                continue

            content_hash = extract.file_hash(fref.abs_path)
            if existing and existing.content_hash == content_hash and existing.embed_model == embedding.DEFAULT_MODEL:
                store.update_file_manifest(
                    project.id,
                    fref.rel_path,
                    size=fref.size,
                    mtime=fref.mtime,
                    content_hash=content_hash,
                    text_hash=existing.text_hash,
                    chunk_count=existing.chunk_count,
                    embed_model=embedding.DEFAULT_MODEL,
                    parser_diagnostics=existing.parser_diagnostics or {},
                )
                indexed_files += 1
                skipped_files += 1
                total_chunks += existing.chunk_count
                if not _update_status(
                    job_id=job_id,
                    indexed_files=indexed_files,
                    skipped_files=skipped_files,
                    total_chunks=total_chunks,
                    embedded_chunks=embedded_chunks,
                    embed_tokens=embed_tokens,
                ):
                    return
                continue

            document = extract.parse_document(fref.abs_path, rel_path=fref.rel_path)
            text = document.to_text()
            chunks = extract.chunk_document(document)
            text_hash = extract.text_hash(text)
            diagnostics = extract.parser_diagnostics(document, chunks).to_dict()

            if not chunks:
                store.replace_file_chunks(
                    project.id,
                    fref.rel_path,
                    [],
                    size=fref.size,
                    mtime=fref.mtime,
                    content_hash=content_hash,
                    text_hash=text_hash,
                    embed_model=embedding.DEFAULT_MODEL,
                    parser_diagnostics=diagnostics,
                )
                indexed_files += 1
                if not _update_status(
                    job_id=job_id,
                    indexed_files=indexed_files,
                    total_chunks=total_chunks,
                    skipped_files=skipped_files,
                    deleted_files=deleted_files,
                ):
                    return
                continue

            vectors: list[list[float]] = []
            file_tokens = 0
            for batch_start in range(0, len(chunks), 10):
                batch = chunks[batch_start: batch_start + 10]
                res = embedding.embed([c.text for c in batch], registry)
                vectors.extend(res.vectors)
                file_tokens += int(res.usage.get("total_tokens") or 0)
                if not _update_status(
                    job_id=job_id,
                    embedded_chunks=embedded_chunks + len(vectors),
                    embed_tokens=embed_tokens + file_tokens,
                ):
                    return

            rows = [
                _chunk_row(c, vec, source_type="project")
                for c, vec in zip(chunks, vectors)
            ]
            store.replace_file_chunks(
                project.id,
                fref.rel_path,
                rows,
                size=fref.size,
                mtime=fref.mtime,
                content_hash=content_hash,
                text_hash=text_hash,
                embed_model=embedding.DEFAULT_MODEL,
                parser_diagnostics=diagnostics,
            )
            indexed_files += 1
            total_chunks += len(chunks)
            embedded_chunks += len(vectors)
            embed_tokens += file_tokens
            if not _update_status(
                job_id=job_id,
                indexed_files=indexed_files,
                total_chunks=total_chunks,
                embedded_chunks=embedded_chunks,
                embed_tokens=embed_tokens,
                skipped_files=skipped_files,
                deleted_files=deleted_files,
            ):
                return

        # 3) 标记完成。chunk_count 从数据库实时统计，避免跳过文件时状态少算。
        final_chunks = store.count_chunks(project.id)
        vectordb.mark_indexed(project.id, embed_model=embedding.DEFAULT_MODEL)
        _update_status(
            job_id=job_id,
            state="done",
            total_chunks=final_chunks,
            indexed_files=len(files),
            skipped_files=skipped_files,
            deleted_files=deleted_files,
            finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    except Exception as exc:  # noqa: BLE001
        _update_status(
            job_id=job_id,
            state="error",
            error=f"{exc.__class__.__name__}: {exc}",
            finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        import sys
        sys.stderr.write("[steelg8 project index error]\n")
        traceback.print_exc()


def _manifest_is_fresh(record: vectordb.FileManifest | None, fref: extract.FileRef) -> bool:
    if record is None:
        return False
    return (
        record.size == fref.size
        and abs(record.mtime - fref.mtime) < 0.001
        and record.embed_model == embedding.DEFAULT_MODEL
    )


def _chunk_row(
    chunk: extract.Chunk,
    vec: list[float],
    *,
    source_type: str,
) -> tuple[str, int, str, list[float], int, dict[str, Any]]:
    metadata = {
        "source_path": chunk.source_path or chunk.rel_path,
        "page": chunk.page,
        "heading": chunk.heading,
        "paragraph_idx": chunk.paragraph_idx,
        "start_char": chunk.start_char,
        "end_char": chunk.end_char,
        "content_hash": chunk.content_hash,
        "source_type": source_type,
    }
    metadata.update(chunk.metadata or {})
    metadata["source_type"] = source_type
    return (
        chunk.rel_path,
        chunk.chunk_idx,
        chunk.text,
        vec,
        chunk.approx_tokens,
        metadata,
    )


def _dedupe_hits(hits: list[vectordb.Hit]) -> list[vectordb.Hit]:
    merged: dict[tuple[str, str, int], vectordb.Hit] = {}
    for h in hits:
        source_type = h.source_type or (h.metadata or {}).get("source_type") or "project"
        key = (source_type, h.source_path or h.rel_path, h.chunk_idx)
        if key not in merged:
            merged[key] = replace(h, source_type=source_type)
            continue
        existing = merged[key]
        merged[key] = replace(
            existing,
            score=max(existing.score, h.score),
            retrieval=_merge_retrieval(existing.retrieval, h.retrieval),
        )
    return sorted(merged.values(), key=lambda item: item.score, reverse=True)


def _merge_retrieval(left: str, right: str) -> str:
    parts: list[str] = []
    for value in (left, right):
        for part in (value or "").split("+"):
            part = part.strip()
            if part and part not in parts:
                parts.append(part)
    return "+".join(parts) or "vector"


def _mark_knowledge_hit(hit: vectordb.Hit) -> vectordb.Hit:
    rel = hit.rel_path
    if not rel.startswith("[knowledge] "):
        rel = f"[knowledge] {rel}"
    return replace(
        hit,
        rel_path=rel,
        source_type="knowledge",
        source_path=hit.source_path or hit.rel_path,
    )


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
    """给用户的 query 做混合检索。

    流程：
      1. 尽量 embed(query)，失败时仍保留 keyword/title 召回
      2. 项目库：vector + keyword/BM25-ish + filename/title
      3. 知识库：同样参与召回，并标记 source_type=knowledge
      4. 合并去重后可选 rerank，最终按 min_score 过滤
    """
    active = get_active()
    project_id = active["id"] if active else 0
    store = rag_store.default_store()

    # 还在索引中且一条 embedding 都没出来 → 跳过当前项目（但知识库还会查）
    skip_current = False
    if active and (
        _STATUS.state == "running"
        and _STATUS.project_id == project_id
        and _STATUS.embedded_chunks == 0
    ):
        skip_current = True

    # 模型一致性校验
    if active:
        proj = vectordb.get_project(active["path"])
        if proj and proj.embed_model and proj.embed_model != embedding.DEFAULT_MODEL:
            import sys
            sys.stderr.write(
                f"[steelg8] embedding model 不一致 · 索引时={proj.embed_model} "
                f"· 当前={embedding.DEFAULT_MODEL}。该项目的 RAG 召回已停用，"
                f"请重新索引。\n"
            )
            skip_current = True

    q_vec: list[float] | None = None
    try:
        q_vec = embedding.embed_one(query, registry, timeout=query_timeout)
    except embedding.EmbeddingError as exc:
        import sys
        sys.stderr.write(f"[steelg8] query embedding 失败: {exc}\n")

    coarse: list[vectordb.Hit] = []
    if active and project_id and not skip_current:
        if q_vec:
            coarse.extend(store.vector_search(project_id, q_vec, top_k=max(coarse_k, top_k)))
        coarse.extend(store.keyword_search(project_id, query, top_k=max(8, top_k)))
        coarse.extend(store.filename_search(project_id, query, top_k=5))

    # L5 知识库：每次对话都掺进去
    try:
        kb_proj = knowledge_mod._ensure_project()
        if store.count_chunks(kb_proj.id) > 0:
            kb_hits: list[vectordb.Hit] = []
            if q_vec:
                kb_hits.extend(store.vector_search(kb_proj.id, q_vec, top_k=5))
            kb_hits.extend(store.keyword_search(kb_proj.id, query, top_k=5))
            kb_hits.extend(store.filename_search(kb_proj.id, query, top_k=3))
            coarse.extend(_mark_knowledge_hit(h) for h in kb_hits)
    except Exception:
        pass

    coarse = _dedupe_hits(coarse)

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
                            replace(
                                h,
                                score=round(score, 4),
                                retrieval=_merge_retrieval(h.retrieval, "rerank"),
                            )
                        )
                return [h for h in reranked[:top_k] if h.score >= min_score]
        except rerank.RerankError as exc:
            import sys
            sys.stderr.write(
                f"[steelg8] rerank 失败，回退到 embedding 粗排: {exc}\n"
            )

    return [h for h in coarse[:top_k] if h.score >= min_score]


def retrieve_debug(
    query: str,
    registry: ProviderRegistry,
    *,
    top_k: int = 5,
    coarse_k: int = 15,
    query_timeout: int = 10,
) -> dict[str, Any]:
    """Return each retrieval lane plus the final reranked output for debugging."""
    active = get_active()
    project_id = active["id"] if active else 0
    store = rag_store.default_store()
    embedding_error = ""
    q_vec: list[float] | None = None
    try:
        q_vec = embedding.embed_one(query, registry, timeout=query_timeout)
    except embedding.EmbeddingError as exc:
        embedding_error = str(exc)

    lanes: dict[str, list[vectordb.Hit]] = {
        "vector": [],
        "keyword": [],
        "title": [],
        "knowledge": [],
    }
    skipped_current = False
    if active and project_id:
        proj = vectordb.get_project(active["path"])
        if proj and proj.embed_model and proj.embed_model != embedding.DEFAULT_MODEL:
            skipped_current = True
        elif _STATUS.state == "running" and _STATUS.project_id == project_id and _STATUS.embedded_chunks == 0:
            skipped_current = True
        else:
            if q_vec:
                lanes["vector"] = store.vector_search(project_id, q_vec, top_k=max(coarse_k, top_k))
            lanes["keyword"] = store.keyword_search(project_id, query, top_k=max(8, top_k))
            lanes["title"] = store.filename_search(project_id, query, top_k=5)

    try:
        kb_proj = knowledge_mod._ensure_project()
        if store.count_chunks(kb_proj.id) > 0:
            kb_hits: list[vectordb.Hit] = []
            if q_vec:
                kb_hits.extend(store.vector_search(kb_proj.id, q_vec, top_k=5))
            kb_hits.extend(store.keyword_search(kb_proj.id, query, top_k=5))
            kb_hits.extend(store.filename_search(kb_proj.id, query, top_k=3))
            lanes["knowledge"] = [_mark_knowledge_hit(h) for h in kb_hits]
    except Exception:
        lanes["knowledge"] = []

    coarse = _dedupe_hits([hit for hits in lanes.values() for hit in hits])
    rerank_error = ""
    reranked = list(coarse)
    if len(coarse) > 1:
        try:
            pairs = rerank.rerank(query, [h.text for h in coarse], registry, top_n=top_k)
            if pairs:
                reranked = []
                for idx, score in pairs:
                    if 0 <= idx < len(coarse):
                        h = coarse[idx]
                        reranked.append(
                            replace(
                                h,
                                score=round(score, 4),
                                retrieval=_merge_retrieval(h.retrieval, "rerank"),
                            )
                        )
        except rerank.RerankError as exc:
            rerank_error = str(exc)

    return {
        "query": query,
        "activeProject": active,
        "skippedCurrentProject": skipped_current,
        "embedding": {
            "ok": q_vec is not None,
            "dims": len(q_vec or []),
            "model": embedding.DEFAULT_MODEL,
            "error": embedding_error,
        },
        "lanes": lanes,
        "coarse": coarse,
        "final": reranked[:top_k],
        "rerank": {
            "attempted": len(coarse) > 1,
            "ok": not rerank_error,
            "error": rerank_error,
        },
    }


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
