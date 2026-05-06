"""Phase 12.1：model_catalog 数据访问层。"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ModelCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="steelg8-cat-")
        self._catalog_path = Path(self._tmp) / "model_catalog.json"
        self._old_env = os.environ.get("STEELG8_CATALOG_PATH")
        os.environ["STEELG8_CATALOG_PATH"] = str(self._catalog_path)
        if "model_catalog" in sys.modules:
            del sys.modules["model_catalog"]
        self.mc = importlib.import_module("model_catalog")

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("STEELG8_CATALOG_PATH", None)
        else:
            os.environ["STEELG8_CATALOG_PATH"] = self._old_env
        if "model_catalog" in sys.modules:
            del sys.modules["model_catalog"]
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_load_when_missing_returns_empty_doc(self) -> None:
        doc = self.mc.load()
        self.assertEqual(doc, {"version": 1, "providers": {}})

    def test_selected_models_filters_unselected(self) -> None:
        self._catalog_path.write_text(
            json.dumps({
                "version": 1,
                "providers": {
                    "kimi": {
                        "fetched_at": None,
                        "models": [
                            {"id": "k2", "selected": True},
                            {"id": "k1", "selected": False},
                            {"id": "k0"},  # 缺 selected → 默认 selected
                        ],
                    }
                },
            }),
            encoding="utf-8",
        )
        sel = self.mc.selected_models("kimi")
        self.assertIn("k2", sel)
        self.assertIn("k0", sel)
        self.assertNotIn("k1", sel)

    def test_set_selected_models_preserves_pricing(self) -> None:
        self._catalog_path.write_text(
            json.dumps({
                "version": 1,
                "providers": {
                    "kimi": {
                        "fetched_at": None,
                        "models": [
                            {
                                "id": "k2",
                                "selected": True,
                                "pricing_per_mtoken": {"input": 0.5, "output": 1.0},
                                "source": "manual",
                            }
                        ],
                    }
                },
            }),
            encoding="utf-8",
        )
        self.mc.set_selected_models("kimi", ["k2", "k3"], source="upstream")
        doc = self.mc.load()
        models = {m["id"]: m for m in doc["providers"]["kimi"]["models"]}
        self.assertEqual(models["k2"]["pricing_per_mtoken"], {"input": 0.5, "output": 1.0})
        self.assertEqual(models["k3"]["pricing_per_mtoken"], {"input": None, "output": None})
        self.assertEqual(models["k3"]["source"], "upstream")

    def test_manual_selection_preserves_unselected_catalog_entries(self) -> None:
        self.mc.set_selected_models("kimi", ["k1", "k2"], source="upstream")
        self.mc.set_selected_models("kimi", ["k2"], source="manual")
        models = {
            m["id"]: m for m in self.mc.all_models("kimi")
        }
        self.assertFalse(models["k1"]["selected"])
        self.assertTrue(models["k2"]["selected"])
        self.assertEqual(self.mc.selected_models("kimi"), ["k2"])

    def test_upstream_refresh_prunes_removed_catalog_entries(self) -> None:
        self.mc.set_selected_models("kimi", ["k1", "k2"], source="upstream")
        self.mc.set_selected_models("kimi", ["k2"], source="upstream")
        models = [m["id"] for m in self.mc.all_models("kimi")]
        self.assertEqual(models, ["k2"])

    def test_record_pricing_updates_existing(self) -> None:
        self.mc.set_selected_models("kimi", ["k2"])
        self.mc.record_pricing("kimi", "k2", {"input": 0.3, "output": 0.6})
        pr = self.mc.model_pricing("kimi")
        self.assertEqual(pr["k2"], {"input": 0.3, "output": 0.6})

    def test_record_pricing_appends_when_missing(self) -> None:
        self.mc.record_pricing("new-prov", "new-model", {"input": 1.0, "output": 2.0})
        models = self.mc.all_models("new-prov")
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["id"], "new-model")
        self.assertEqual(models[0]["pricing_per_mtoken"]["input"], 1.0)

    def test_mark_fetched(self) -> None:
        self.mc.mark_fetched("kimi", "2026-05-02T10:00:00Z")
        doc = self.mc.load()
        self.assertEqual(doc["providers"]["kimi"]["fetched_at"], "2026-05-02T10:00:00Z")

    def test_corrupted_file_falls_back_to_empty(self) -> None:
        self._catalog_path.write_text("{not json", encoding="utf-8")
        doc = self.mc.load()
        self.assertEqual(doc, {"version": 1, "providers": {}})


if __name__ == "__main__":
    unittest.main()
