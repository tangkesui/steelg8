"""
L5 知识库沉淀
---------------

和单项目 RAG 区分开：知识库是**跨项目共享**的冷数据，每次对话永远会去翻。

实现方式：
- 物理存储：`~/.steelg8/knowledge/` 下一堆 markdown 文件
- Agent 通过 save_knowledge tool 或用户手动往里写
- 向量库：就复用 vectordb 的 project 抽象，固定挂一个 project_id，
  存在 project 表里的一行（path = KNOWLEDGE_ROOT，name = "knowledge"）
- 对话时：project.retrieve() 会在当前激活项目之外，**再**查这个知识库，
  把命中段一起送进 rerank，让最相关的不论出处都能浮出来

写入流程：
  save_card(title, content, source) → 写 markdown 文件
    → 调 embedding.embed_one 拿这条向量
    → 直接 append_chunk 到 knowledge 项目
  （不重新索引全库；只增量加这一条 chunk）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import embedding
import extract
import rag_store
import vectordb
from providers import ProviderRegistry


KNOWLEDGE_ROOT = Path(os.environ.get(
    "STEELG8_KNOWLEDGE_DIR",
    Path.home() / ".steelg8" / "knowledge",
))


def knowledge_root() -> Path:
    KNOWLEDGE_ROOT.mkdir(parents=True, exist_ok=True)
    return KNOWLEDGE_ROOT


def _slug(s: str) -> str:
    s = re.sub(r"[\s/\\:*?\"<>|]+", "-", s.strip())
    s = s.strip("-.") or "card"
    return s[:40]


def _ensure_project() -> vectordb.ProjectRow:
    """知识库复用 vectordb 的 project 抽象，这条总是 ready。"""
    root = knowledge_root()
    path = str(root.resolve())
    vectordb.upsert_project(path, name="knowledge", embed_dims=1024)
    row = vectordb.get_project(path)
    assert row is not None
    return row


def active_project_id() -> int:
    return _ensure_project().id


def save_card(
    title: str,
    content: str,
    registry: ProviderRegistry,
    *,
    source: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """写一个知识卡片 + 立刻增量 embed 入库。返回 {path, chunks}。"""
    if not content or not content.strip():
        raise ValueError("content 不能为空")

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d-%H%M%S")
    slug = _slug(title or content[:20])
    file_path = knowledge_root() / f"{ts}-{slug}.md"

    frontmatter = [
        "---",
        f"title: {title or '(无标题)'}",
        f"created: {now.isoformat(timespec='seconds')}",
    ]
    if source:
        frontmatter.append(f"source: {source}")
    if tags:
        frontmatter.append("tags: " + ", ".join(tags))
    frontmatter.append("---\n")

    body_header = f"# {title or '(无标题)'}\n" if title else ""
    full_text = "\n".join(frontmatter) + "\n" + body_header + content.strip() + "\n"
    file_path.write_text(full_text, encoding="utf-8")

    # 切块 + embedding（这一个文件）
    chunks = extract.chunk_text(full_text, file_path.name)
    if not chunks:
        return {"path": str(file_path), "chunks": 0}

    res = embedding.embed([c.text for c in chunks], registry)
    proj = _ensure_project()

    # 知识库用"追加"而不是全量覆盖；复用 replace_chunks 每次覆盖该文件的旧条目
    # 简化：把本文件相关的 chunks 全部清掉，再写入新的
    rows = [
        _chunk_row(chunk, vec)
        for chunk, vec in zip(chunks, res.vectors)
    ]
    rag_store.default_store().replace_file_chunks(
        proj.id,
        file_path.name,
        rows,
        size=file_path.stat().st_size,
        mtime=file_path.stat().st_mtime,
        content_hash=extract.file_hash(str(file_path)),
        text_hash=extract.text_hash(full_text),
        embed_model=embedding.DEFAULT_MODEL,
    )
    return {
        "path": str(file_path),
        "chunks": len(chunks),
        "embed_tokens": res.usage.get("total_tokens", 0),
    }


def _chunk_row(chunk: extract.Chunk, vec: list[float]) -> tuple:
    metadata = {
        "source_path": chunk.source_path,
        "page": chunk.page,
        "heading": chunk.heading,
        "paragraph_idx": chunk.paragraph_idx,
        "start_char": chunk.start_char,
        "end_char": chunk.end_char,
        "content_hash": chunk.content_hash,
        "source_type": "knowledge",
    }
    return (
        chunk.rel_path,
        chunk.chunk_idx,
        chunk.text,
        vec,
        chunk.approx_tokens,
        metadata,
    )


def search(registry: ProviderRegistry, query: str, top_k: int = 3) -> list[vectordb.Hit]:
    """在知识库里找 top_k 相关片段。失败就返回空。"""
    try:
        proj = _ensure_project()
        if vectordb.count_chunks(proj.id) == 0:
            return []
        q_vec = embedding.embed_one(query, registry)
        hits = vectordb.search(proj.id, q_vec, top_k=top_k)
        return hits
    except Exception:
        return []


def list_cards() -> list[dict[str, Any]]:
    """列出所有卡片：文件名 + 标题 + mtime。"""
    root = knowledge_root()
    out = []
    for p in sorted(root.glob("*.md")):
        try:
            stat = p.stat()
            head = p.read_text(encoding="utf-8")[:500]
            # 抓第一个 "title:" 或 "# " 行
            title = ""
            for line in head.splitlines():
                if line.startswith("title:"):
                    title = line[len("title:"):].strip()
                    break
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            out.append({
                "name": p.name,
                "title": title or p.stem,
                "path": str(p),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        except OSError:
            continue
    return out
