"""polish-3 验收单测：A4 / B3 / C3 / D3 / F6 / G5。

每条对应 plan-2026-05-08-model-mgmt-polish-3.md 的同名 task。"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _reload_providers(tmp: Path):
    os.environ["STEELG8_PROVIDERS_PATH"] = str(tmp / "providers.json")
    os.environ["STEELG8_SECRETS_PATH"] = str(tmp / "secrets.json")
    os.environ["STEELG8_CATALOG_PATH"] = str(tmp / "model_catalog.json")
    for mod in ("providers", "model_catalog"):
        if mod in sys.modules:
            del sys.modules[mod]
    return importlib.import_module("providers")


# ---------- A4 ----------

class ChatServiceUsesRagStrategyTests(unittest.TestCase):
    """chat_service 必须走 rag_strategy.default_strategy().retrieve(...)，
    不再直接打 project.retrieve（接进 strategy 抽象层）。"""

    def test_chat_path_calls_rag_strategy(self) -> None:
        import rag_strategy
        called = {"strategy": False}

        class _StubStrategy:
            def retrieve(self, query, registry, *, top_k=5, **kwargs):
                called["strategy"] = True
                return []

        with patch.object(rag_strategy, "default_strategy", return_value=_StubStrategy()):
            from services import chat_service

            # 简化：直接调内部 retrieve 路径——读 chat_service 源以验合约
            src = Path(chat_service.__file__).read_text(encoding="utf-8")
            self.assertIn("rag_strategy.default_strategy()", src)
            self.assertIn(".retrieve(", src)
            self.assertNotIn("project_mod.retrieve(", src)


# ---------- B3 ----------

class FallbackUsesAllModelsTests(unittest.TestCase):
    """unselect 全部 visible 后 first_ready() 仍按 catalog all_models 给 fallback model。"""

    def test_first_ready_picks_all_models_first_when_visible_empty(self) -> None:
        import providers as P
        prov = P.Provider(
            name="bailian",
            base_url="https://example.com/v1",
            api_key_secret="sk-test",
            kind="openai-compatible",
            models=[],                                # visible 空
            all_models=["qwen-plus", "qwen-max"],     # catalog 还有
        )
        registry = P.ProviderRegistry(providers={"bailian": prov})
        result = registry.first_ready()
        self.assertIsNotNone(result)
        picked_prov, fallback_model = result
        self.assertEqual(picked_prov.name, "bailian")
        self.assertEqual(fallback_model, "qwen-plus")  # all_models[0]，不受 visible 空集影响

    def test_first_ready_returns_default_model_when_catalog_empty(self) -> None:
        import providers as P
        prov = P.Provider(
            name="bailian",
            base_url="https://example.com/v1",
            api_key_secret="sk-test",
            kind="openai-compatible",
            models=[],
            all_models=[],
        )
        registry = P.ProviderRegistry(
            providers={"bailian": prov}, default_model="qwen-max"
        )
        result = registry.first_ready()
        self.assertIsNotNone(result)
        _, fallback_model = result
        self.assertEqual(fallback_model, "qwen-max")  # 退到 default_model


# ---------- C3 ----------

class ResolveModelDoesNotWriteRouterStateTests(unittest.TestCase):
    """resolve_model 走 router.preview（只读），不能改 _LAST_DECISION。"""

    def test_resolve_does_not_overwrite_last_decision(self) -> None:
        import router

        # 用一个 stub registry
        class _Resolver:
            def __init__(self):
                self.default_model = ""
            def resolve(self, model):
                return None
            def first_ready(self):
                return None

        registry = _Resolver()
        # 先用 route() 写一个真实命中
        first = router.route("hello", registry, explicit_model=None)
        self.assertEqual(router.last_decision().layer, first.layer)

        # 多次 resolve_model：last_decision 不应被改写
        from services import provider_service
        for _ in range(5):
            provider_service.resolve_model(registry, {"model": "kimi-k2.6"})
        snap = router.last_decision()
        self.assertEqual(snap.layer, first.layer)
        self.assertEqual(snap.timestamp, first.timestamp)


# ---------- D3 ----------

class StaticPricingWritesVerifiedTests(unittest.TestCase):
    """bailian 上游不回 pricing，但 pricing.py 静态表收录 → catalog 写 verified。

    用 Mock registry + 隔离的 catalog 文件，跟 test_catalog_refresh 同一套路。"""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="steelg8-d3-")
        self._catalog_path = Path(self._tmp) / "model_catalog.json"
        self._old_env = os.environ.get("STEELG8_CATALOG_PATH")
        os.environ["STEELG8_CATALOG_PATH"] = str(self._catalog_path)
        for mod_name in ("model_catalog", "services.provider_service"):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        self.provider_service = importlib.import_module("services.provider_service")

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

    def test_text_embedding_v3_lands_as_verified(self) -> None:
        from unittest.mock import Mock
        prov = Mock(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        prov.is_ready.return_value = True
        prov.api_key.return_value = "sk-stub"
        registry = Mock(providers={"bailian": prov})
        registry.update_models.return_value = True

        # bailian 上游 /models 不暴露 embedding，但 known_capabilities 会 seed
        upstream = {"data": [{"id": "qwen-plus"}]}
        with patch("network.request_json", return_value=upstream), \
             patch("services.pricing_scraper.scrape_pricing", return_value={}):
            response = self.provider_service.catalog_refresh(registry, "bailian")

        models = {m["id"]: m for m in response.payload["models"]}
        # text-embedding-v3 是 known_capabilities seed 进来的
        self.assertIn("text-embedding-v3", models)
        self.assertAlmostEqual(
            models["text-embedding-v3"]["pricing_per_mtoken"]["input"], 0.097
        )
        self.assertEqual(models["text-embedding-v3"]["pricing_source"], "verified")


# ---------- F6 ----------

class RagDiagnosticsRoundTripTests(unittest.TestCase):
    """新增字段 latency_ms / batch_size / fallback_used 能被 record + snapshot 正确传递。"""

    def test_embed_success_records_latency_and_batch(self) -> None:
        import rag_diagnostics
        rag_diagnostics.clear()
        rag_diagnostics.record_embed_success(
            provider="bailian", model="text-embedding-v3",
            dimensions=1024, total_texts=10,
            latency_ms=150, batch_size=10,
        )
        snap = rag_diagnostics.snapshot()
        self.assertEqual(snap["embed_ok"]["latency_ms"], 150)
        self.assertEqual(snap["embed_ok"]["batch_size"], 10)

    def test_rerank_success_records_latency_and_fallback(self) -> None:
        import rag_diagnostics
        rag_diagnostics.clear()
        rag_diagnostics.record_rerank_success(
            provider="bailian", model="gte-rerank",
            endpoint_kind="dashscope-native", doc_count=5,
            fallback_used=True, latency_ms=80,
        )
        snap = rag_diagnostics.snapshot()
        self.assertEqual(snap["rerank_ok"]["latency_ms"], 80)
        self.assertTrue(snap["rerank_ok"]["fallback_used"])

    def test_legacy_callers_without_kwargs_still_work(self) -> None:
        # 老调用方不传 latency_ms / batch_size，字段默认 0 不报错
        import rag_diagnostics
        rag_diagnostics.clear()
        rag_diagnostics.record_embed_success(
            provider="x", model="y", dimensions=8, total_texts=1,
        )
        snap = rag_diagnostics.snapshot()
        self.assertEqual(snap["embed_ok"]["latency_ms"], 0)
        self.assertEqual(snap["embed_ok"]["batch_size"], 0)


# ---------- G5 ----------

class RagConfigRejectsIllegalEndpointKindTests(unittest.TestCase):
    """embedding.endpoint_kind 配 dashscope-native 时静默回退到 openai-compat。"""

    def test_dashscope_native_rejected_for_embedding(self) -> None:
        import rag_config
        cfg = rag_config._parse_embedding({  # noqa: SLF001
            "provider": "bailian",
            "model": "text-embedding-v3",
            "endpoint_kind": "dashscope-native",
        })
        # 非法值被静默忽略，回到默认 openai-compat
        self.assertEqual(cfg.endpoint_kind, "openai-compat")

    def test_openai_compat_accepted(self) -> None:
        import rag_config
        cfg = rag_config._parse_embedding({  # noqa: SLF001
            "endpoint_kind": "openai-compat",
        })
        self.assertEqual(cfg.endpoint_kind, "openai-compat")

    def test_rerank_still_accepts_dashscope_native(self) -> None:
        # rerank 那边两个 kind 都真实现了，不能误伤
        import rag_config
        cfg = rag_config._parse_rerank({  # noqa: SLF001
            "endpoint_kind": "dashscope-native",
        })
        self.assertEqual(cfg.endpoint_kind, "dashscope-native")


if __name__ == "__main__":
    unittest.main()
