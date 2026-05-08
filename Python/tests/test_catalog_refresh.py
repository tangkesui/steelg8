"""Phase 12.4：provider_service.catalog_refresh / read_catalog 业务路径。"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_provider(base_url: str = "https://api.example.test/v1") -> Mock:
    prov = Mock(base_url=base_url)
    prov.is_ready.return_value = True
    prov.api_key.return_value = "sk-stub"
    return prov


def _make_registry(name: str, prov: Mock) -> Mock:
    reg = Mock(providers={name: prov})
    reg.update_models.return_value = True
    return reg


class CatalogRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="steelg8-cat-refresh-")
        self._catalog_path = Path(self._tmp) / "model_catalog.json"
        self._old_env = os.environ.get("STEELG8_CATALOG_PATH")
        os.environ["STEELG8_CATALOG_PATH"] = str(self._catalog_path)
        # 强制 reload 让 model_catalog 重新读 env var
        for mod_name in ("model_catalog", "services.provider_service"):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        from services import provider_service  # noqa: F401
        self.provider_service = importlib.import_module("services.provider_service")
        self.model_catalog = importlib.import_module("model_catalog")

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("STEELG8_CATALOG_PATH", None)
        else:
            os.environ["STEELG8_CATALOG_PATH"] = self._old_env
        for mod_name in ("model_catalog", "services.provider_service"):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---------- catalog_refresh ----------

    def test_refresh_openrouter_uses_upstream_pricing(self) -> None:
        upstream = {
            "data": [
                {
                    "id": "anthropic/claude-sonnet-4.5",
                    "pricing": {"prompt": "0.000003", "completion": "0.000015"},
                },
                {
                    "id": "openai/gpt-4o-mini",
                    "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
                },
            ]
        }
        prov = _make_provider("https://openrouter.ai/api/v1")
        registry = _make_registry("openrouter", prov)

        with patch("network.request_json", return_value=upstream):
            response = self.provider_service.catalog_refresh(registry, "openrouter")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["count"], 2)
        models = {m["id"]: m for m in response.payload["models"]}
        self.assertAlmostEqual(
            models["anthropic/claude-sonnet-4.5"]["pricing_per_mtoken"]["input"], 3.0
        )
        self.assertAlmostEqual(
            models["anthropic/claude-sonnet-4.5"]["pricing_per_mtoken"]["output"], 15.0
        )
        self.assertAlmostEqual(
            models["openai/gpt-4o-mini"]["pricing_per_mtoken"]["input"], 0.15
        )

        # 写盘验证
        doc = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        self.assertIn("openrouter", doc["providers"])
        self.assertIsNotNone(doc["providers"]["openrouter"]["fetched_at"])

    def test_refresh_kimi_falls_back_to_pricing_table(self) -> None:
        # kimi 上游返回不带 pricing 字段。polish-3 D 起：pricing.py 静态表
        # （来源是官方文档的高置信价格）→ verified；表里没收录的才 fallback null。
        upstream = {
            "data": [
                {"id": "kimi-k2-0905-preview"},   # pricing.py 收录 → verified
                {"id": "totally-unknown-model"},  # 未收录 → fallback
            ]
        }
        prov = _make_provider("https://api.moonshot.cn/v1")
        registry = _make_registry("kimi", prov)

        with patch("network.request_json", return_value=upstream), \
             patch("services.pricing_scraper.scrape_pricing", return_value={}):
            response = self.provider_service.catalog_refresh(registry, "kimi")

        models = {m["id"]: m for m in response.payload["models"]}
        self.assertAlmostEqual(
            models["kimi-k2-0905-preview"]["pricing_per_mtoken"]["input"], 0.56
        )
        self.assertEqual(models["kimi-k2-0905-preview"]["pricing_source"], "verified")
        self.assertIsNone(models["totally-unknown-model"]["pricing_per_mtoken"]["input"])
        self.assertEqual(models["totally-unknown-model"]["pricing_source"], "fallback")

    def test_refresh_preserves_user_unselected_models(self) -> None:
        # 用户先把 model-a 标 unselected
        self._catalog_path.write_text(
            json.dumps({
                "version": 1,
                "providers": {
                    "kimi": {
                        "fetched_at": None,
                        "models": [
                            {
                                "id": "model-a",
                                "selected": False,
                                "pricing_per_mtoken": {"input": None, "output": None},
                                "source": "manual",
                            },
                            {
                                "id": "model-b",
                                "selected": True,
                                "pricing_per_mtoken": {"input": None, "output": None},
                                "source": "manual",
                            },
                        ],
                    }
                },
            }),
            encoding="utf-8",
        )

        upstream = {"data": [{"id": "model-a"}, {"id": "model-b"}, {"id": "model-c"}]}
        prov = _make_provider("https://api.moonshot.cn/v1")
        registry = _make_registry("kimi", prov)

        with patch("network.request_json", return_value=upstream):
            response = self.provider_service.catalog_refresh(registry, "kimi")

        by_id = {m["id"]: m for m in response.payload["models"]}
        self.assertFalse(by_id["model-a"]["selected"])  # 原 unselected → 仍 unselected
        self.assertTrue(by_id["model-b"]["selected"])
        self.assertTrue(by_id["model-c"]["selected"])

        # update_models 只传 selected 模型给内存 registry
        registry.update_models.assert_called_once_with("kimi", ["model-b", "model-c"])

    def test_refresh_upstream_failure_raises_502(self) -> None:
        prov = _make_provider()
        registry = _make_registry("kimi", prov)

        import network

        def _boom(*_args, **_kwargs):
            raise network.NetworkError("connection refused")

        from services.common import ServiceError
        with patch("network.request_json", side_effect=_boom):
            with self.assertRaises(ServiceError) as ctx:
                self.provider_service.catalog_refresh(registry, "kimi")
        self.assertEqual(ctx.exception.status, 502)
        # 失败时 catalog 文件不该被创建
        self.assertFalse(self._catalog_path.exists())

    def test_refresh_unknown_provider_returns_404(self) -> None:
        registry = Mock(providers={})
        from services.common import ServiceError
        with self.assertRaises(ServiceError) as ctx:
            self.provider_service.catalog_refresh(registry, "missing")
        self.assertEqual(ctx.exception.status, 404)

    def test_refresh_unready_provider_returns_400(self) -> None:
        prov = _make_provider()
        prov.is_ready.return_value = False
        registry = _make_registry("kimi", prov)
        from services.common import ServiceError
        with self.assertRaises(ServiceError) as ctx:
                self.provider_service.catalog_refresh(registry, "kimi")
        self.assertEqual(ctx.exception.status, 400)

    def test_refresh_local_runtime_does_not_send_authorization_header(self) -> None:
        from providers import Provider

        prov = Provider(
            name="ollama",
            base_url="http://127.0.0.1:11434/v1",
            kind="local-runtime",
        )
        registry = _make_registry("ollama", prov)

        with patch("network.request_json", return_value={"data": [{"id": "llama3.1"}]}) as req:
            response = self.provider_service.catalog_refresh(registry, "ollama")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["models"][0]["id"], "llama3.1")
        self.assertEqual(req.call_args.kwargs["headers"], {})

    def test_refresh_empty_upstream_returns_500(self) -> None:
        prov = _make_provider()
        registry = _make_registry("kimi", prov)
        from services.common import ServiceError
        with patch("network.request_json", return_value={"data": []}):
            with self.assertRaises(ServiceError) as ctx:
                self.provider_service.catalog_refresh(registry, "kimi")
        self.assertEqual(ctx.exception.status, 500)

    # ---------- read_catalog ----------

    def test_read_catalog_returns_404_when_missing(self) -> None:
        from services.common import ServiceError
        with self.assertRaises(ServiceError) as ctx:
            self.provider_service.read_catalog("kimi")
        self.assertEqual(ctx.exception.status, 404)

    def test_read_catalog_returns_slice_after_refresh(self) -> None:
        upstream = {"data": [{"id": "deepseek-chat"}]}
        prov = _make_provider("https://api.deepseek.com")
        registry = _make_registry("deepseek", prov)
        with patch("network.request_json", return_value=upstream):
            self.provider_service.catalog_refresh(registry, "deepseek")
        response = self.provider_service.read_catalog("deepseek")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["name"], "deepseek")
        self.assertIsNotNone(response.payload["fetched_at"])
        self.assertEqual(len(response.payload["models"]), 1)

    # ---------- update_catalog_selection ----------

    def test_update_catalog_selection_preserves_unselected_catalog_models(self) -> None:
        self.model_catalog.set_selected_models(
            "kimi",
            ["model-a", "model-b"],
            source="upstream",
        )
        response = self.provider_service.update_catalog_selection(
            "kimi",
            {"model_ids": ["model-b", "model-c", "model-b", ""]},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            [m["id"] for m in response.payload["models"]],
            ["model-a", "model-b", "model-c"],
        )
        selected_by_id = {
            m["id"]: m["selected"] for m in response.payload["models"]
        }
        self.assertFalse(selected_by_id["model-a"])
        self.assertTrue(selected_by_id["model-b"])
        self.assertTrue(selected_by_id["model-c"])
        self.assertEqual(self.model_catalog.selected_models("kimi"), ["model-b", "model-c"])

    def test_update_catalog_selection_rejects_non_list_payload(self) -> None:
        from services.common import ServiceError
        with self.assertRaises(ServiceError) as ctx:
            self.provider_service.update_catalog_selection("kimi", {"model_ids": "model-a"})
        self.assertEqual(ctx.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
