"""
RAG 存储边界。

当前唯一实现是 `SQLiteBruteForceStore` —— 进程内加载所有 chunk 做余弦扫描。
1 万 chunk 以内可用，再大需要切换到 ANN backend（sqlite-vec / FAISS / Qdrant）。

为了让 Phase 9 引入 ANN backend 时不动 chat / project 业务，这里：
1. 定义 `BackendCapabilities`，让上层可以判断 backend 是否支持 ANN / metadata
   filter / BM25，从而决定是否走 fallback。
2. RagStore 协议加 `capabilities()` 方法。
3. `default_store()` 通过 `STEELG8_RAG_BACKEND` 环境变量挑选实现，未来 ANN
   backend 只需 register + 设环境变量就能替换。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import vectordb


@dataclass
class FileIndexRecord:
    rel_path: str
    size: int
    mtime: float
    content_hash: str
    text_hash: str
    chunk_count: int
    embed_model: str
    parser_diagnostics: dict[str, Any] | None = None


@dataclass
class BackendCapabilities:
    """描述一个 RagStore 后端能做什么，给诊断面板和上层路由用。"""

    name: str
    supports_ann: bool = False
    supports_metadata_filter: bool = False
    supports_bm25: bool = False
    supports_persistence: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "supportsAnn": self.supports_ann,
            "supportsMetadataFilter": self.supports_metadata_filter,
            "supportsBm25": self.supports_bm25,
            "supportsPersistence": self.supports_persistence,
            "notes": self.notes,
        }


class RagStore(Protocol):
    """Storage boundary for project RAG.

    The first implementation is still SQLite + Python brute force. Keeping this
    boundary explicit lets us swap search internals later without touching chat.
    """

    def capabilities(self) -> BackendCapabilities:
        ...

    def count_chunks(self, project_id: int) -> int:
        ...

    def clear_project(self, project_id: int) -> None:
        ...

    def list_manifest(self, project_id: int) -> dict[str, vectordb.FileManifest]:
        ...

    def replace_file_chunks(
        self,
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
        ...

    def update_file_manifest(
        self,
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
        ...

    def delete_file_chunks(self, project_id: int, rel_path: str) -> None:
        ...

    def vector_search(self, project_id: int, query_vec: list[float], *, top_k: int) -> list[vectordb.Hit]:
        ...

    def keyword_search(self, project_id: int, query: str, *, top_k: int) -> list[vectordb.Hit]:
        ...

    def filename_search(self, project_id: int, query: str, *, top_k: int) -> list[vectordb.Hit]:
        ...


class SQLiteBruteForceStore:
    """Current backend: SQLite persistence and in-process ranking.

    1 万 chunk 内交互正常；再大走 ANN backend（Phase 9）。
    """

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name="sqlite-brute-force",
            supports_ann=False,
            supports_metadata_filter=True,  # SQL WHERE 子句可加 metadata 过滤
            supports_bm25=False,            # 当前 keyword_search 是 LIKE 不是 BM25
            supports_persistence=True,
            notes="进程内余弦扫描；1 万 chunk 内可用，再大请切 ANN backend。",
        )

    def count_chunks(self, project_id: int) -> int:
        return vectordb.count_chunks(project_id)

    def clear_project(self, project_id: int) -> None:
        vectordb.clear_project_index(project_id)

    def list_manifest(self, project_id: int) -> dict[str, vectordb.FileManifest]:
        return vectordb.list_manifest(project_id)

    def replace_file_chunks(
        self,
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
        vectordb.replace_file_chunks(
            project_id,
            rel_path,
            rows,
            size=size,
            mtime=mtime,
            content_hash=content_hash,
            text_hash=text_hash,
            embed_model=embed_model,
            parser_diagnostics=parser_diagnostics,
        )

    def update_file_manifest(
        self,
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
        vectordb.update_file_manifest(
            project_id,
            rel_path,
            size=size,
            mtime=mtime,
            content_hash=content_hash,
            text_hash=text_hash,
            chunk_count=chunk_count,
            embed_model=embed_model,
            parser_diagnostics=parser_diagnostics,
        )

    def delete_file_chunks(self, project_id: int, rel_path: str) -> None:
        vectordb.delete_file_chunks(project_id, rel_path)

    def vector_search(self, project_id: int, query_vec: list[float], *, top_k: int) -> list[vectordb.Hit]:
        return vectordb.search(project_id, query_vec, top_k=top_k)

    def keyword_search(self, project_id: int, query: str, *, top_k: int) -> list[vectordb.Hit]:
        return vectordb.keyword_search(project_id, query, top_k=top_k)

    def filename_search(self, project_id: int, query: str, *, top_k: int) -> list[vectordb.Hit]:
        return vectordb.filename_search(project_id, query, top_k=top_k)


_BACKENDS: dict[str, Callable[[], RagStore]] = {
    "sqlite-brute-force": SQLiteBruteForceStore,
}


def register_backend(name: str, factory: Callable[[], RagStore]) -> None:
    """让 Phase 9 的 sqlite-vec / FAISS backend 通过同一接口接入。

    用法：
        rag_store.register_backend("sqlite-vec", make_sqlite_vec_store)
        # 然后设 STEELG8_RAG_BACKEND=sqlite-vec
    """
    _BACKENDS[name] = factory


_DEFAULT_STORE: RagStore | None = None


def default_store() -> RagStore:
    """返回当前激活的 backend。第一次调用按 `STEELG8_RAG_BACKEND` 选；缓存到下一次进程。"""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        backend_name = (os.environ.get("STEELG8_RAG_BACKEND") or "sqlite-brute-force").strip()
        factory = _BACKENDS.get(backend_name)
        if factory is None:
            # 未注册的 backend 名 → 回退到 SQLite，避免 chat 链路因配置错误整链中断
            factory = SQLiteBruteForceStore
        _DEFAULT_STORE = factory()
    return _DEFAULT_STORE


def reset_default_store() -> None:
    """测试用：清掉缓存，下次 default_store() 重新按环境变量选 backend。"""
    global _DEFAULT_STORE
    _DEFAULT_STORE = None
