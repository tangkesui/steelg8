from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kernel import request as http_request  # noqa: E402
from services import chat_service  # noqa: E402
from services import common  # noqa: E402
from services import conversation_service  # noqa: E402
from services import diagnostics_service  # noqa: E402
from services import docx_service  # noqa: E402
from services import library_service  # noqa: E402
from services import observability_service  # noqa: E402
from services import project_service  # noqa: E402
from services import provider_service  # noqa: E402
from services import settings_service  # noqa: E402


class FakeRow:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return dict(self.payload)


class ServiceCommonTests(unittest.TestCase):
    def test_service_error_carries_status_and_payload(self):
        err = common.ServiceError(400, {"error": "bad"})
        self.assertEqual(err.status, 400)
        self.assertEqual(err.payload, {"error": "bad"})

    def test_request_read_json_returns_none_for_invalid_json(self):
        body = io.BytesIO(b"{bad")
        self.assertIsNone(http_request.read_json({"Content-Length": "4"}, body))


class ChatServiceTests(unittest.TestCase):
    def test_soul_summary_uses_first_bullet(self):
        self.assertEqual(chat_service.soul_summary("# Soul\n\n- 方案不求人。"), "方案不求人。")


class ProjectServiceTests(unittest.TestCase):
    def test_open_project_requires_path(self):
        with self.assertRaises(common.ServiceError) as ctx:
            project_service.open_project({}, Mock())
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.payload["error"], "path is required")

    def test_remove_project_maps_missing_to_404(self):
        with patch.object(project_service.project_mod, "remove", return_value=False):
            response = project_service.remove_project(99)
        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload, {"ok": False})

    def test_rag_debug_requires_query(self):
        with self.assertRaises(common.ServiceError) as ctx:
            project_service.rag_debug({}, Mock())
        self.assertEqual(ctx.exception.status, 400)

    def test_rag_debug_serializes_lanes(self):
        hit = Mock(
            rel_path="docs/a.md",
            chunk_idx=0,
            score=0.9,
            retrieval="vector",
            source_type="project",
            heading="A",
            page=None,
            text="preview text",
            metadata={"parser": "markdown", "chunk_profile": "default"},
        )
        hit.citation.return_value = {"relPath": "docs/a.md"}
        with patch.object(project_service.project_mod, "retrieve_debug", return_value={
            "query": "hello",
            "activeProject": {"id": 1},
            "skippedCurrentProject": False,
            "embedding": {"ok": True, "dims": 2, "model": "m", "error": ""},
            "rerank": {"attempted": False, "ok": True, "error": ""},
            "lanes": {"vector": [hit], "keyword": [], "title": [], "knowledge": []},
            "coarse": [hit],
            "final": [hit],
        }):
            payload = project_service.rag_debug({"query": "hello", "topK": 99}, Mock())

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["lanes"]["vector"][0]["parser"], "markdown")
        self.assertEqual(payload["final"][0]["preview"], "preview text")


class ConversationServiceTests(unittest.TestCase):
    def test_create_conversation_uses_active_project_when_project_root_missing(self):
        fake_conv = FakeRow({"id": 1, "projectRoot": "/tmp/project"})
        with patch.object(conversation_service.project_mod, "get_active", return_value={"path": "/tmp/project"}), \
             patch.object(conversation_service.conv_store, "create_conversation", return_value=fake_conv) as create:
            payload = conversation_service.create_conversation({"title": "hello"})
        create.assert_called_once_with(title="hello", project_root="/tmp/project")
        self.assertEqual(payload["projectRoot"], "/tmp/project")

    def test_conversation_detail_404_for_missing_conversation(self):
        with patch.object(conversation_service.conv_store, "get_conversation", return_value=None):
            with self.assertRaises(common.ServiceError) as ctx:
                conversation_service.conversation_detail(42)
        self.assertEqual(ctx.exception.status, 404)


class SettingsServiceTests(unittest.TestCase):
    def test_save_preferences_rejects_non_dict_body(self):
        with self.assertRaises(common.ServiceError) as ctx:
            settings_service.save_preferences(["bad"])
        self.assertEqual(ctx.exception.status, 400)

    def test_save_preferences_delegates_to_preferences_module(self):
        with patch.object(settings_service.prefs_mod, "save", return_value={"log_level": "debug"}) as save:
            payload = settings_service.save_preferences({"log_level": "debug"})
        save.assert_called_once_with({"log_level": "debug"})
        self.assertEqual(payload["log_level"], "debug")


class LibraryServiceTests(unittest.TestCase):
    def test_save_scratch_note_rejects_non_string_text(self):
        with self.assertRaises(common.ServiceError) as ctx:
            library_service.save_scratch_note({"text": 123})
        self.assertEqual(ctx.exception.status, 400)

    def test_delete_template_maps_false_to_400(self):
        with patch.object(library_service.template_lib, "delete", return_value=False):
            response = library_service.delete_template("/tmp/nope.docx")
        self.assertEqual(response.status, 400)
        self.assertEqual(response.payload, {"ok": False})


class ObservabilityServiceTests(unittest.TestCase):
    def test_logs_bounds_limit_and_days(self):
        with patch.object(observability_service.logger, "read_recent", return_value=[]) as read_recent, \
             patch.object(observability_service.logger, "stats", return_value={"errors": 0}):
            payload = observability_service.logs({"limit": ["9999"], "days": ["99"], "conv": ["bad"]})
        self.assertEqual(payload["stats"], {"errors": 0})
        read_recent.assert_called_once()
        self.assertEqual(read_recent.call_args.kwargs["limit"], 1000)
        self.assertEqual(read_recent.call_args.kwargs["days"], 14)
        self.assertIsNone(read_recent.call_args.kwargs["conversation_id"])

    def test_bounded_limit_uses_query_value(self):
        self.assertEqual(
            observability_service.bounded_limit({"limit": ["5"]}, default=100, maximum=1000),
            5,
        )
        self.assertEqual(
            observability_service.bounded_limit({"limit": ["9999"]}, default=100, maximum=1000),
            1000,
        )


class DiagnosticsServiceTests(unittest.TestCase):
    def test_doctor_aggregates_checks_and_issues(self):
        provider = Mock()
        provider.is_ready.return_value = True
        registry = Mock(providers={"bailian": provider})
        registry.validation_summary.return_value = {
            "ok": True,
            "readyProviderCount": 1,
            "issues": [],
        }
        context = diagnostics_service.DiagnosticContext(
            app_root=Path("/tmp"),
            port=8765,
            auth_required=True,
        )
        with patch.object(diagnostics_service, "_dependency_check", return_value={
            "name": "documentDependencies",
            "level": "warn",
            "ok": True,
            "message": "部分文档格式会被跳过",
            "data": {},
        }), \
             patch.object(diagnostics_service, "_rag_store_check", return_value={
                 "name": "ragStore",
                 "level": "ok",
                 "ok": True,
                 "message": "RAG 数据库可访问",
                 "data": {},
             }), \
             patch.object(diagnostics_service, "_active_project_check", return_value={
                 "name": "activeProject",
                 "level": "ok",
                 "ok": True,
                 "message": "当前项目可用",
                 "data": {},
             }), \
             patch.object(diagnostics_service, "_logs_check", return_value={
                 "name": "logs",
                 "level": "ok",
                 "ok": True,
                 "message": "最近日志正常",
                 "data": {},
             }):
            payload = diagnostics_service.doctor(registry, context)

        self.assertEqual(payload["level"], "warn")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["issues"][0]["check"], "documentDependencies")
        self.assertEqual(payload["context"]["port"], 8765)

    def test_index_inspector_reports_stale_and_missing_manifest_files(self):
        project_row = diagnostics_service.vectordb.ProjectRow(
            id=7,
            path="/tmp/project",
            name="project",
            created_at="now",
            indexed_at="later",
            embed_dims=1024,
            embed_model="text-embedding-v3",
            chunk_count=3,
        )
        manifest = {
            "old.md": diagnostics_service.vectordb.FileManifest(
                project_id=7,
                rel_path="old.md",
                size=10,
                mtime=1.0,
                content_hash="h1",
                text_hash="t1",
                chunk_count=2,
                embed_model="text-embedding-v3",
                indexed_at="later",
            )
        }
        store = Mock()
        store.list_manifest.return_value = manifest
        store.count_chunks.return_value = 2
        current_file = diagnostics_service.extract.FileRef(
            abs_path="/tmp/project/new.md",
            rel_path="new.md",
            size=20,
            mtime=2.0,
        )
        with patch.object(diagnostics_service.project_mod, "get_active", return_value={"path": "/tmp/project"}), \
             patch.object(diagnostics_service.project_mod, "status", return_value={"state": "done"}), \
             patch.object(diagnostics_service.vectordb, "get_project", return_value=project_row), \
             patch.object(diagnostics_service.rag_store, "default_store", return_value=store), \
             patch.object(diagnostics_service.extract, "walk_project", return_value=iter([current_file])):
            payload = diagnostics_service.index_inspector()

        self.assertEqual(payload["level"], "error")
        self.assertEqual(payload["manifest"]["staleFiles"], ["old.md"])
        self.assertEqual(payload["manifest"]["missingManifestFiles"], ["new.md"])
        self.assertEqual(payload["storage"]["chunkCount"], 2)


class ProviderServiceTests(unittest.TestCase):
    def test_providers_summary_keeps_payload_shape(self):
        registry = Mock(source="source.json", default_model="m1")
        registry.readiness_summary.return_value = [{"name": "kimi"}]
        payload = provider_service.providers_summary(registry)
        self.assertEqual(payload["source"], "source.json")
        self.assertEqual(payload["defaultModel"], "m1")
        self.assertEqual(payload["providers"], [{"name": "kimi"}])

    def test_sync_models_extracts_openai_compatible_model_ids(self):
        provider = Mock(base_url="https://example.test/v1")
        provider.is_ready.return_value = True
        provider.api_key.return_value = "secret"
        registry = Mock(providers={"test": provider})
        registry.update_models.return_value = True
        with patch("network.request_json", return_value={"data": [{"id": "m1"}, {"id": "m2"}]}):
            response = provider_service.sync_models(registry, "test")
        registry.update_models.assert_called_once_with("test", ["m1", "m2"])
        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["count"], 2)


class DocxServiceTests(unittest.TestCase):
    def test_fill_requires_template(self):
        with self.assertRaises(common.ServiceError) as ctx:
            docx_service.fill({})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.payload["error"], "template is required")

    def test_append_row_rejects_non_dict_body(self):
        with self.assertRaises(common.ServiceError) as ctx:
            docx_service.append_row(["bad"])
        self.assertEqual(ctx.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
