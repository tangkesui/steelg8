from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import embedding  # noqa: E402
import extract  # noqa: E402
import project  # noqa: E402
import rag_store  # noqa: E402
import vectordb  # noqa: E402
from document import chunkers as document_chunkers  # noqa: E402
from document import registry as document_registry  # noqa: E402


class TempVectorDB:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = os.environ.get("STEELG8_VECTORS_DB")
        os.environ["STEELG8_VECTORS_DB"] = str(Path(self.tmp.name) / "vectors.db")
        vectordb.init()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.old_path is None:
            os.environ.pop("STEELG8_VECTORS_DB", None)
        else:
            os.environ["STEELG8_VECTORS_DB"] = self.old_path
        self.tmp.cleanup()


def chunk_row(chunk: extract.Chunk, vec: list[float], source_type: str = "project") -> tuple:
    return (
        chunk.rel_path,
        chunk.chunk_idx,
        chunk.text,
        vec,
        chunk.approx_tokens,
        {
            "source_path": chunk.source_path,
            "page": chunk.page,
            "heading": chunk.heading,
            "paragraph_idx": chunk.paragraph_idx,
            "start_char": chunk.start_char,
            "end_char": chunk.end_char,
            "content_hash": chunk.content_hash,
            "source_type": source_type,
            **(chunk.metadata or {}),
        },
    )


class ChunkMetadataTests(unittest.TestCase):
    def test_chunk_text_preserves_citation_metadata(self):
        text = "<!-- page 2 -->\n# 规划说明\n\n第一段内容。\n\n第二段内容。"
        chunks = extract.chunk_text(text, "docs/spec.md", target_tokens=100, overlap_chars=0)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].source_path, "docs/spec.md")
        self.assertEqual(chunks[0].page, 2)
        self.assertEqual(chunks[0].heading, "规划说明")
        self.assertEqual(chunks[0].paragraph_idx, 0)
        self.assertGreater(chunks[0].end_char, chunks[0].start_char)
        self.assertTrue(chunks[0].content_hash)
        self.assertEqual(chunks[0].metadata["parser"], "legacy-text")

    def test_parser_diagnostics_summarizes_document_shape(self):
        doc = document_registry.parse_text(
            "# 标题\n\n正文\n\n| A | B |\n| --- | --- |\n| 1 | 2 |",
            rel_path="docs/diag.md",
            parser="markdown",
        )
        chunks = extract.chunk_document(doc, overlap_chars=0)
        diagnostics = extract.parser_diagnostics(doc, chunks).to_dict()

        self.assertEqual(diagnostics["parser"], "markdown")
        self.assertEqual(diagnostics["chunkCount"], len(chunks))
        self.assertEqual(diagnostics["tableCount"], 1)
        self.assertFalse(diagnostics["emptyText"])


class StructuredDocumentTests(unittest.TestCase):
    def test_parse_text_emits_structural_blocks(self):
        doc = document_registry.parse_text(
            "\n".join([
                "<!-- page 3 -->",
                "# 总纲",
                "",
                "第一段。",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                "| A | B |",
                "",
                "```python",
                "print('x')",
                "```",
                "",
                "- 条目一",
                "- 条目二",
            ]),
            rel_path="docs/structured.md",
            parser="markdown",
        )

        self.assertEqual(doc.title, "总纲")
        self.assertEqual([b.type for b in doc.blocks], ["heading", "paragraph", "table", "code", "list"])
        self.assertEqual(doc.blocks[0].page, 3)
        self.assertEqual(doc.blocks[2].heading_path, ["总纲"])
        self.assertEqual(doc.blocks[2].metadata["block_index"], 2)
        self.assertIn("| A | B |", doc.blocks[2].text)

    def test_heading_aware_chunker_keeps_section_boundary(self):
        doc = document_registry.parse_text(
            "# A\n\nalpha\n\n## B\n\nbeta",
            rel_path="docs/sections.md",
            parser="markdown",
        )
        chunks = document_chunkers.chunk_document(doc, target_tokens=500, overlap_chars=0)

        self.assertEqual(len(chunks), 2)
        self.assertIn("# A", chunks[0].text)
        self.assertNotIn("## B", chunks[0].text)
        self.assertEqual(chunks[1].heading, "B")

    def test_extract_read_text_uses_structured_parser_compat_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.md"
            path.write_text("<!-- page 4 -->\n# 标题\n\n正文", encoding="utf-8")

            text = extract.read_text(str(path))

        self.assertIn("<!-- page 4 -->", text)
        self.assertIn("# 标题", text)

    def test_template_chunker_uses_profile_boundaries(self):
        doc = document_registry.parse_text(
            "会议时间：2026-04-28\n\n议题：预算安排\n\n讨论内容：形成两个行动项。",
            rel_path="docs/meeting.md",
            parser="text",
        )
        chunks = document_chunkers.chunk_document(doc, profile="meeting", overlap_chars=0)

        self.assertEqual(len(chunks), 2)
        self.assertIn("会议时间", chunks[0].text)
        self.assertTrue(chunks[1].text.startswith("议题"))
        self.assertEqual(chunks[1].metadata["chunk_profile"], "meeting")

    def test_table_aware_chunker_repeats_header_for_split_tables(self):
        table = "\n".join(
            [
                "| 名称 | 金额 |",
                "| --- | --- |",
                "| A | 1 |",
                "| B | 2 |",
                "| C | 3 |",
                "| D | 4 |",
                "| E | 5 |",
            ]
        )
        doc = document_registry.parse_text(table, rel_path="docs/table.md", parser="markdown")
        options = document_chunkers.ChunkingOptions(
            target_tokens=200,
            overlap_chars=0,
            keep_tables_atomic=False,
            table_max_tokens=200,
            table_rows_per_chunk=2,
            profile_name="table-test",
        )

        chunks = document_chunkers.chunk_document(doc, options=options)

        self.assertEqual(len(chunks), 3)
        for chunk in chunks:
            self.assertIn("| 名称 | 金额 |", chunk.text)
            self.assertIn("| --- | --- |", chunk.text)
            self.assertTrue(chunk.metadata["contains_table_split"])
        self.assertEqual(chunks[0].metadata["table_parts"], [0])
        self.assertEqual(chunks[1].metadata["table_parts"], [1])


class RagStoreTests(unittest.TestCase):
    def test_vectordb_init_migrates_old_chunk_schema_before_heading_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_path = os.environ.get("STEELG8_VECTORS_DB")
            db_path = Path(tmp) / "vectors.db"
            os.environ["STEELG8_VECTORS_DB"] = str(db_path)
            try:
                # sqlite3.Connection 的 context manager 只处理 commit/rollback，
                # 不会关连接 —— 这里必须显式 close，否则会泄漏到测试结束。
                with contextlib.closing(sqlite3.connect(db_path)) as conn, conn:
                    conn.executescript(
                        """
                        CREATE TABLE project (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            path TEXT UNIQUE NOT NULL,
                            name TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            indexed_at TEXT,
                            embed_dims INTEGER DEFAULT 1024
                        );
                        CREATE TABLE chunks (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            project_id INTEGER NOT NULL,
                            rel_path TEXT NOT NULL,
                            chunk_idx INTEGER NOT NULL,
                            text TEXT NOT NULL,
                            embedding BLOB NOT NULL,
                            tokens INTEGER DEFAULT 0,
                            updated_at TEXT NOT NULL
                        );
                        CREATE TABLE file_manifest (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            project_id INTEGER NOT NULL,
                            rel_path TEXT NOT NULL,
                            size INTEGER NOT NULL,
                            mtime REAL NOT NULL,
                            content_hash TEXT NOT NULL,
                            text_hash TEXT DEFAULT '',
                            chunk_count INTEGER DEFAULT 0,
                            embed_model TEXT DEFAULT '',
                            indexed_at TEXT NOT NULL,
                            UNIQUE(project_id, rel_path)
                        );
                        """
                    )

                vectordb.init()

                with contextlib.closing(sqlite3.connect(db_path)) as conn:
                    chunk_cols = {
                        row[1]
                        for row in conn.execute("PRAGMA table_info(chunks)").fetchall()
                    }
                    project_cols = {
                        row[1]
                        for row in conn.execute("PRAGMA table_info(project)").fetchall()
                    }
                    indexes = {
                        row[1]
                        for row in conn.execute("PRAGMA index_list(chunks)").fetchall()
                    }

                self.assertIn("heading", chunk_cols)
                self.assertIn("metadata_json", chunk_cols)
                self.assertIn("embed_model", project_cols)
                self.assertIn("idx_chunks_heading", indexes)
            finally:
                if old_path is None:
                    os.environ.pop("STEELG8_VECTORS_DB", None)
                else:
                    os.environ["STEELG8_VECTORS_DB"] = old_path

    def test_sqlite_store_keeps_manifest_and_metadata_hits(self):
        with TempVectorDB(), tempfile.TemporaryDirectory() as root:
            project_id = vectordb.upsert_project(root)
            chunks = extract.chunk_text(
                "# 投资计划\n\n这里记录 zoning 与 schedule。",
                "docs/policy.md",
                target_tokens=100,
                overlap_chars=0,
            )
            rows = [chunk_row(chunks[0], [1.0, 0.0])]
            store = rag_store.default_store()
            store.replace_file_chunks(
                project_id,
                "docs/policy.md",
                rows,
                size=123,
                mtime=456.0,
                content_hash="file-hash",
                text_hash="text-hash",
                embed_model=embedding.DEFAULT_MODEL,
                parser_diagnostics={"parser": "markdown", "blockCount": 2},
            )

            manifest = store.list_manifest(project_id)
            self.assertEqual(manifest["docs/policy.md"].chunk_count, 1)
            self.assertEqual(manifest["docs/policy.md"].content_hash, "file-hash")
            self.assertEqual(manifest["docs/policy.md"].parser_diagnostics["parser"], "markdown")

            vector_hits = store.vector_search(project_id, [1.0, 0.0], top_k=1)
            self.assertEqual(vector_hits[0].heading, "投资计划")
            self.assertEqual(vector_hits[0].citation()["sourcePath"], "docs/policy.md")

            keyword_hits = store.keyword_search(project_id, "zoning", top_k=1)
            self.assertEqual(keyword_hits[0].retrieval, "keyword")

            filename_hits = store.filename_search(project_id, "policy", top_k=1)
            self.assertEqual(filename_hits[0].retrieval, "title")


class IncrementalIndexTests(unittest.TestCase):
    def test_index_job_skips_unchanged_files_on_second_run(self):
        with TempVectorDB(), tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "spec.md").write_text("# 标题\n\n稳定内容。", encoding="utf-8")
            project_id = vectordb.upsert_project(str(root))
            row = vectordb.get_project(str(root))
            assert row is not None

            def fake_embed(texts, registry):
                return embedding.EmbeddingResult(
                    vectors=[[1.0, 0.0] for _ in texts],
                    usage={"total_tokens": len(texts)},
                    model=embedding.DEFAULT_MODEL,
                )

            with patch.object(project.embedding, "embed", side_effect=fake_embed) as embed_mock:
                job_id = project._begin_index_job(row)
                project._run_index_job(row, Mock(), job_id)
                self.assertEqual(project.status()["state"], "done")
                self.assertEqual(project.status()["skipped_files"], 0)
                self.assertGreater(embed_mock.call_count, 0)
                manifest = rag_store.default_store().list_manifest(project_id)
                diag = manifest["spec.md"].parser_diagnostics or {}
                self.assertEqual(diag["parser"], "markdown")
                self.assertEqual(diag["chunkCount"], 1)

            with patch.object(project.embedding, "embed", side_effect=fake_embed) as embed_mock:
                job_id = project._begin_index_job(row)
                project._run_index_job(row, Mock(), job_id)
                self.assertEqual(project.status()["state"], "done")
                self.assertEqual(project.status()["skipped_files"], 1)
                embed_mock.assert_not_called()


class RagBackendRegistryTests(unittest.TestCase):
    """`STEELG8_RAG_BACKEND` 选 backend；未注册时回退到 SQLite。"""

    def setUp(self):
        rag_store.reset_default_store()
        self._old = os.environ.get("STEELG8_RAG_BACKEND")

    def tearDown(self):
        rag_store.reset_default_store()
        if self._old is None:
            os.environ.pop("STEELG8_RAG_BACKEND", None)
        else:
            os.environ["STEELG8_RAG_BACKEND"] = self._old
        # 移除测试期间注册的伪 backend，免得污染下个用例
        rag_store._BACKENDS.pop("test-fake", None)

    def test_default_backend_is_sqlite_brute_force(self):
        os.environ.pop("STEELG8_RAG_BACKEND", None)
        store = rag_store.default_store()
        caps = store.capabilities()
        self.assertEqual(caps.name, "sqlite-brute-force")
        self.assertFalse(caps.supports_ann)
        self.assertTrue(caps.supports_persistence)

    def test_register_backend_then_select_via_env(self):
        class _FakeStore:
            def capabilities(self):
                return rag_store.BackendCapabilities(name="fake", supports_ann=True)

        rag_store.register_backend("test-fake", _FakeStore)
        os.environ["STEELG8_RAG_BACKEND"] = "test-fake"
        rag_store.reset_default_store()
        caps = rag_store.default_store().capabilities()
        self.assertEqual(caps.name, "fake")
        self.assertTrue(caps.supports_ann)

    def test_unknown_backend_falls_back_to_sqlite(self):
        os.environ["STEELG8_RAG_BACKEND"] = "does-not-exist"
        rag_store.reset_default_store()
        caps = rag_store.default_store().capabilities()
        self.assertEqual(caps.name, "sqlite-brute-force")


if __name__ == "__main__":
    unittest.main()
