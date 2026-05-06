"""Phase 12.4：recommended_models 草稿清单。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RecommendedModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        import recommended_models
        self.mod = recommended_models

    def test_known_providers_covers_example_config(self) -> None:
        # 与 config/providers.example.json 的 4 个内置 provider 对齐
        expected = {"kimi", "deepseek", "bailian", "openrouter"}
        self.assertSetEqual(set(self.mod.known_providers()), expected)

    def test_for_provider_returns_non_empty_string_list(self) -> None:
        for pid in self.mod.known_providers():
            items = self.mod.for_provider(pid)
            self.assertGreater(len(items), 0, f"provider {pid} 推荐清单不能为空")
            for mid in items:
                self.assertIsInstance(mid, str)
                self.assertTrue(mid, f"provider {pid} 出现空字符串模型 id")

    def test_for_provider_returns_copy_not_reference(self) -> None:
        a = self.mod.for_provider("kimi")
        a.append("INJECTED")
        b = self.mod.for_provider("kimi")
        self.assertNotIn("INJECTED", b)

    def test_for_provider_unknown_returns_empty(self) -> None:
        self.assertEqual(self.mod.for_provider("nonexistent-provider"), [])

    def test_openrouter_models_use_vendor_prefix(self) -> None:
        # OpenRouter 的 model id 必须是 "vendor/name" 形式
        for mid in self.mod.for_provider("openrouter"):
            self.assertIn("/", mid, f"openrouter 推荐 {mid} 缺少 vendor 前缀")


if __name__ == "__main__":
    unittest.main()
