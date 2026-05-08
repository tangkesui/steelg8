"""Phase 12.1：v2 providers + secrets + catalog 联合加载 / api_key 优先级。"""
from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _reload_providers(tmp: Path):
    os.environ["STEELG8_PROVIDERS_PATH"] = str(tmp / "providers.json")
    os.environ["STEELG8_SECRETS_PATH"] = str(tmp / "secrets.json")
    os.environ["STEELG8_CATALOG_PATH"] = str(tmp / "model_catalog.json")
    for mod in ("providers", "model_catalog"):
        if mod in sys.modules:
            del sys.modules[mod]
    return importlib.import_module("providers")


class ProvidersV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = {
            k: os.environ.get(k)
            for k in (
                "STEELG8_PROVIDERS_PATH",
                "STEELG8_SECRETS_PATH",
                "STEELG8_CATALOG_PATH",
                "DEEPSEEK_API_KEY",
                "KIMI_API_KEY",
            )
        }
        # 清空相关 env 防止污染
        for k in ("DEEPSEEK_API_KEY", "KIMI_API_KEY"):
            os.environ.pop(k, None)
        self._tmp = tempfile.mkdtemp(prefix="steelg8-pv-")
        self.tmp = Path(self._tmp)
        self._write_v2_files()
        self.providers = _reload_providers(self.tmp)

    def tearDown(self) -> None:
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for mod in ("providers", "model_catalog"):
            if mod in sys.modules:
                del sys.modules[mod]
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_v2_files(self) -> None:
        (self.tmp / "providers.json").write_text(
            json.dumps({
                "version": 2,
                "default_provider": "deepseek",
                "default_model": "deepseek-chat",
                "providers": [
                    {
                        "id": "kimi", "name": "Kimi",
                        "base_url": "https://api.moonshot.cn/v1",
                        "api_key_env": "KIMI_API_KEY",
                        "kind": "openai-compatible",
                    },
                    {
                        "id": "deepseek", "name": "DeepSeek",
                        "base_url": "https://api.deepseek.com",
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "kind": "openai-compatible",
                    },
                ],
            }),
            encoding="utf-8",
        )
        (self.tmp / "secrets.json").write_text(
            json.dumps({"version": 1, "keys": {"deepseek": "sk-from-secrets"}}),
            encoding="utf-8",
        )
        (self.tmp / "model_catalog.json").write_text(
            json.dumps({
                "version": 1,
                "providers": {
                    "kimi": {
                        "fetched_at": None,
                        "models": [
                            {"id": "moonshot-v1-8k", "selected": True,
                             "pricing_per_mtoken": {"input": None, "output": None}},
                            {"id": "kimi-hidden", "selected": False,
                             "pricing_per_mtoken": {"input": None, "output": None}},
                        ],
                    },
                    "deepseek": {
                        "fetched_at": None,
                        "models": [
                            {"id": "deepseek-chat", "selected": True,
                             "pricing_per_mtoken": {"input": 0.14, "output": 0.28}},
                        ],
                    },
                },
            }),
            encoding="utf-8",
        )

    def test_loads_three_files_into_registry(self) -> None:
        reg = self.providers.load_registry()
        self.assertEqual(set(reg.providers.keys()), {"kimi", "deepseek"})
        self.assertEqual(reg.default_model, "deepseek-chat")
        self.assertEqual(reg.default_provider, "deepseek")

        kimi = reg.providers["kimi"]
        self.assertEqual(kimi.display_name, "Kimi")
        self.assertEqual(kimi.kind, "openai-compatible")
        # selected=False 的模型不暴露
        self.assertEqual(kimi.models, ["moonshot-v1-8k"])

        ds = reg.providers["deepseek"]
        self.assertEqual(ds.api_key_secret, "sk-from-secrets")
        self.assertEqual(ds.api_key(), "sk-from-secrets")
        self.assertEqual(ds.api_key_source(), "secrets")

    def test_api_key_priority_secrets_over_env(self) -> None:
        os.environ["DEEPSEEK_API_KEY"] = "sk-from-env"
        reg = self.providers.load_registry()
        ds = reg.providers["deepseek"]
        self.assertEqual(ds.api_key(), "sk-from-secrets")
        self.assertEqual(ds.api_key_source(), "secrets")

    def test_api_key_falls_back_to_env_when_secrets_missing(self) -> None:
        # 把 secrets.json 置空
        (self.tmp / "secrets.json").write_text(
            json.dumps({"version": 1, "keys": {}}),
            encoding="utf-8",
        )
        os.environ["DEEPSEEK_API_KEY"] = "sk-env-fallback"
        providers = _reload_providers(self.tmp)
        reg = providers.load_registry()
        ds = reg.providers["deepseek"]
        self.assertEqual(ds.api_key(), "sk-env-fallback")
        self.assertEqual(ds.api_key_source(), "env:DEEPSEEK_API_KEY")

    def test_inline_api_key_falls_back_with_warning(self) -> None:
        # secrets 空，env 空，但 providers.json 有 inline api_key（用户违规手改）
        (self.tmp / "secrets.json").write_text(
            json.dumps({"version": 1, "keys": {}}),
            encoding="utf-8",
        )
        doc = json.loads((self.tmp / "providers.json").read_text(encoding="utf-8"))
        for entry in doc["providers"]:
            if entry["id"] == "deepseek":
                entry["api_key"] = "sk-inline-deprecated"
        (self.tmp / "providers.json").write_text(
            json.dumps(doc), encoding="utf-8"
        )

        providers = _reload_providers(self.tmp)
        # 抓 logger 警告
        captured: list[logging.LogRecord] = []

        class _ListHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        h = _ListHandler(level=logging.WARNING)
        logging.getLogger("steelg8.providers").addHandler(h)
        try:
            reg = providers.load_registry()
            ds = reg.providers["deepseek"]
            self.assertEqual(ds.api_key(), "sk-inline-deprecated")
            self.assertEqual(ds.api_key_source(), "inline-deprecated")
        finally:
            logging.getLogger("steelg8.providers").removeHandler(h)

        self.assertTrue(
            any("api_key_inline" in r.getMessage() for r in captured),
            "应输出 inline 弃用 warning",
        )

    def test_readiness_summary_includes_pricing_and_selected(self) -> None:
        reg = self.providers.load_registry()
        summary = reg.readiness_summary()
        ds_row = next(r for r in summary if r["name"] == "deepseek")
        self.assertEqual(ds_row["selected_models"], ["deepseek-chat"])
        # 新 schema：pricing dict 多 source 字段
        self.assertEqual(ds_row["pricing"]["deepseek-chat"]["input"], 0.14)
        self.assertEqual(ds_row["pricing"]["deepseek-chat"]["output"], 0.28)
        self.assertEqual(ds_row["pricing"]["deepseek-chat"]["source"], "fallback")
        # 新增：all_models / created_at 都在响应里
        self.assertIn("deepseek-chat", ds_row["all_models"])
        self.assertIn("deepseek-chat", ds_row["created_at"])
        self.assertEqual(ds_row["displayName"], "DeepSeek")
        self.assertEqual(ds_row["kind"], "openai-compatible")

    def test_visible_vs_all_models_split(self) -> None:
        """selected:false 的模型应进 all_models 不进 models（topbar 可见）。"""
        # 修改 catalog：deepseek-reasoner 不存在我加一条 selected=False
        catalog_path = self.tmp / "model_catalog.json"
        doc = json.loads(catalog_path.read_text(encoding="utf-8"))
        doc["providers"]["kimi"]["models"].append({
            "id": "kimi-archived",
            "selected": False,
            "pricing_per_mtoken": {"input": None, "output": None},
        })
        catalog_path.write_text(json.dumps(doc), encoding="utf-8")

        providers = _reload_providers(self.tmp)
        reg = providers.load_registry()
        kimi = reg.providers["kimi"]
        self.assertNotIn("kimi-archived", kimi.models)         # visible 集不包含
        self.assertIn("kimi-archived", kimi.all_models)         # 全量集包含

    def test_orphan_default_model_auto_added_when_catalog_unselected(self) -> None:
        """providers.json 的 default_model 在 catalog 里 selected:false 时（孤儿状态），
        registry 应自动把它注入对应 provider.models，避免 /providers 返回空列表导致
        UI 显示「选择模型」。"""
        # 把 catalog 中 deepseek-chat 改成 selected:false（其它一切不变）
        catalog_path = self.tmp / "model_catalog.json"
        doc = json.loads(catalog_path.read_text(encoding="utf-8"))
        for m in doc["providers"]["deepseek"]["models"]:
            if m["id"] == "deepseek-chat":
                m["selected"] = False
        catalog_path.write_text(json.dumps(doc), encoding="utf-8")

        providers = _reload_providers(self.tmp)
        reg = providers.load_registry()
        # 兜底逻辑应把 deepseek-chat 加回去
        self.assertIn("deepseek-chat", reg.providers["deepseek"].models)
        # default_model 仍解析得到 (provider, model)
        resolved = reg.resolve(None)
        self.assertIsNotNone(resolved)
        provider, model = resolved
        self.assertEqual(provider.name, "deepseek")
        self.assertEqual(model, "deepseek-chat")

    def test_resolve_prefers_default_provider_for_duplicate_model_id(self) -> None:
        providers = self.providers
        reg = providers.ProviderRegistry(
            providers={
                "kimi": providers.Provider(
                    name="kimi",
                    base_url="https://kimi.test/v1",
                    api_key_secret="sk",
                    models=["shared-model"],
                ),
                "deepseek": providers.Provider(
                    name="deepseek",
                    base_url="https://deepseek.test/v1",
                    api_key_secret="sk",
                    models=["shared-model"],
                ),
            },
            default_model="shared-model",
            default_provider="deepseek",
        )

        resolved = reg.resolve(None)
        self.assertIsNotNone(resolved)
        provider, model = resolved
        self.assertEqual(provider.name, "deepseek")
        self.assertEqual(model, "shared-model")


if __name__ == "__main__":
    unittest.main()
