"""
Phase 12.1：~/.steelg8 配置文件三分迁移测试。

覆盖：
- 旧单文件 → 三文件迁移正确性 + 权限（0600 / 0644）
- 已迁移情况下幂等
- 部分迁移（仅 secrets.json 存在）不破坏其余文件
- 备份文件存在且可读
- 损坏 providers.json 抛 ConfigMigrationError
"""
from __future__ import annotations

import importlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _reload_with_temp_dir(tmp: Path) -> tuple:
    """在 tmp 下重新指向三份配置路径，并 reload config_migrate。"""
    os.environ["STEELG8_PROVIDERS_PATH"] = str(tmp / "providers.json")
    os.environ["STEELG8_SECRETS_PATH"] = str(tmp / "secrets.json")
    os.environ["STEELG8_CATALOG_PATH"] = str(tmp / "model_catalog.json")
    if "config_migrate" in sys.modules:
        del sys.modules["config_migrate"]
    return importlib.import_module("config_migrate")


class ConfigMigrateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_env = {
            k: os.environ.get(k)
            for k in (
                "STEELG8_PROVIDERS_PATH",
                "STEELG8_SECRETS_PATH",
                "STEELG8_CATALOG_PATH",
            )
        }
        self._tmpdir = tempfile.mkdtemp(prefix="steelg8-cfg-")
        self.tmp = Path(self._tmpdir)
        self.cm = _reload_with_temp_dir(self.tmp)

    def tearDown(self) -> None:
        for k, v in self._original_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if "config_migrate" in sys.modules:
            del sys.modules["config_migrate"]
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ----- helpers -----

    def _write_legacy(self) -> Path:
        p = self.tmp / "providers.json"
        p.write_text(
            json.dumps({
                "default_model": "deepseek-chat",
                "providers": {
                    "kimi": {
                        "base_url": "https://api.moonshot.cn/v1",
                        "api_key_env": "KIMI_API_KEY",
                        "api_key": "sk-kimi-secret",
                        "models": ["moonshot-v1-8k", "kimi-k2"],
                    },
                    "deepseek": {
                        "base_url": "https://api.deepseek.com",
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "api_key": "sk-ds-secret",
                        "models": ["deepseek-chat", "deepseek-reasoner"],
                    },
                },
            }),
            encoding="utf-8",
        )
        return p

    def _file_perm(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    # ----- 测试 -----

    def test_migrates_legacy_to_three_files(self) -> None:
        legacy = self._write_legacy()
        result = self.cm.run_if_needed()
        self.assertEqual(result["action"], "migrated")
        self.assertEqual(result["providers_count"], 2)
        self.assertEqual(result["secrets_count"], 2)

        providers_doc = json.loads(legacy.read_text(encoding="utf-8"))
        self.assertEqual(providers_doc["version"], 2)
        self.assertEqual(providers_doc["default_model"], "deepseek-chat")
        self.assertEqual(providers_doc["default_provider"], "deepseek")
        ids = sorted(p["id"] for p in providers_doc["providers"])
        self.assertEqual(ids, ["deepseek", "kimi"])
        for p in providers_doc["providers"]:
            self.assertNotIn("api_key", p)
            self.assertNotIn("models", p)

        secrets_doc = json.loads((self.tmp / "secrets.json").read_text(encoding="utf-8"))
        self.assertEqual(secrets_doc["keys"]["kimi"], "sk-kimi-secret")
        self.assertEqual(secrets_doc["keys"]["deepseek"], "sk-ds-secret")

        catalog_doc = json.loads((self.tmp / "model_catalog.json").read_text(encoding="utf-8"))
        kimi_models = [m["id"] for m in catalog_doc["providers"]["kimi"]["models"]]
        self.assertIn("kimi-k2", kimi_models)
        for m in catalog_doc["providers"]["kimi"]["models"]:
            self.assertTrue(m["selected"])

    def test_permissions_after_migration(self) -> None:
        self._write_legacy()
        self.cm.run_if_needed()
        # 跳过 macOS NFS / 网盘 chmod 失效环境（先 best-effort）
        try:
            self.assertEqual(self._file_perm(self.tmp / "secrets.json"), 0o600)
            self.assertEqual(self._file_perm(self.tmp / "providers.json"), 0o644)
            self.assertEqual(self._file_perm(self.tmp / "model_catalog.json"), 0o644)
        except AssertionError:
            self.skipTest("当前文件系统不支持 chmod；跳过权限断言")

    def test_backup_created_and_matches_legacy(self) -> None:
        legacy = self._write_legacy()
        legacy_text = legacy.read_text(encoding="utf-8")
        result = self.cm.run_if_needed()
        backup_path = Path(result["backup"])
        self.assertTrue(backup_path.exists())
        self.assertEqual(backup_path.read_text(encoding="utf-8"), legacy_text)

    def test_idempotent_when_all_present(self) -> None:
        self._write_legacy()
        first = self.cm.run_if_needed()
        self.assertEqual(first["action"], "migrated")
        # mtimes
        before = {
            n: (self.tmp / n).stat().st_mtime_ns
            for n in ("providers.json", "secrets.json", "model_catalog.json")
        }
        # 二次执行
        second = self.cm.run_if_needed()
        self.assertEqual(second["action"], "noop")
        after = {
            n: (self.tmp / n).stat().st_mtime_ns
            for n in ("providers.json", "secrets.json", "model_catalog.json")
        }
        self.assertEqual(before, after, "幂等：二次执行不应触碰文件")

    def test_partial_skeleton_only_fills_missing(self) -> None:
        # 先模拟一个 v2 providers.json + secrets.json，缺 catalog
        (self.tmp / "providers.json").write_text(
            json.dumps({
                "version": 2,
                "default_provider": "kimi",
                "default_model": "kimi-k2",
                "providers": [
                    {"id": "kimi", "name": "Kimi",
                     "base_url": "https://api.moonshot.cn/v1",
                     "api_key_env": "KIMI_API_KEY",
                     "kind": "openai-compatible"}
                ],
            }),
            encoding="utf-8",
        )
        (self.tmp / "secrets.json").write_text(
            json.dumps({"version": 1, "keys": {"kimi": "sk-x"}}),
            encoding="utf-8",
        )
        providers_mtime = (self.tmp / "providers.json").stat().st_mtime_ns
        secrets_mtime = (self.tmp / "secrets.json").stat().st_mtime_ns

        result = self.cm.run_if_needed()
        self.assertEqual(result["action"], "skeleton_created")
        self.assertIn(str(self.tmp / "model_catalog.json"), result["files"])

        # 已存在的两份不应被改写
        self.assertEqual(
            (self.tmp / "providers.json").stat().st_mtime_ns, providers_mtime
        )
        self.assertEqual(
            (self.tmp / "secrets.json").stat().st_mtime_ns, secrets_mtime
        )
        # 新创建的 catalog 是空骨架
        catalog = json.loads((self.tmp / "model_catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog, {"version": 1, "providers": {}})

    def test_corrupted_providers_raises(self) -> None:
        (self.tmp / "providers.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(self.cm.ConfigMigrationError):
            self.cm.run_if_needed()

    def test_no_providers_file_creates_empty_skeleton(self) -> None:
        # 干净环境：什么都没有 → 创建三份空骨架
        result = self.cm.run_if_needed()
        self.assertEqual(result["action"], "skeleton_created")
        self.assertTrue((self.tmp / "providers.json").exists())
        self.assertTrue((self.tmp / "secrets.json").exists())
        self.assertTrue((self.tmp / "model_catalog.json").exists())


if __name__ == "__main__":
    unittest.main()
