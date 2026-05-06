"""Phase 12.1：_safe_path workspace allowlist 行为。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.path_safety import safe_path  # noqa: E402


class SafePathAllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        # 在 home 下建一个测试目录，确保旧行为基线
        self._home_tmp = tempfile.mkdtemp(prefix="steelg8-home-", dir=str(Path.home()))
        # 在 /tmp 下（home 之外）建一个 allowlist 候选目录
        self._extern_tmp = tempfile.mkdtemp(prefix="steelg8-extern-")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._home_tmp, ignore_errors=True)
        shutil.rmtree(self._extern_tmp, ignore_errors=True)

    # ---- 基线：allowlist 空 ----

    def test_home_path_passes_when_allowlist_empty(self) -> None:
        target = Path(self._home_tmp) / "a.docx"
        result = safe_path(str(target), allowlist=[])
        self.assertEqual(result, str(target.resolve()))

    def test_non_home_path_rejected_when_allowlist_empty(self) -> None:
        target = Path(self._extern_tmp) / "a.docx"
        with self.assertRaises(ValueError):
            safe_path(str(target), allowlist=[])

    # ---- 启用 allowlist ----

    def test_allowlist_dir_writable(self) -> None:
        target = Path(self._extern_tmp) / "a.docx"
        result = safe_path(
            str(target),
            access="write",
            suffixes={".docx"},
            allowlist=[self._extern_tmp],
        )
        self.assertEqual(result, str(target.resolve()))

    def test_path_outside_home_and_allowlist_rejected(self) -> None:
        # 用一个肯定既不在 home 也不在 allowlist 的目录
        outside = tempfile.mkdtemp(prefix="steelg8-outside-")
        try:
            target = Path(outside) / "a.docx"
            with self.assertRaises(ValueError):
                safe_path(
                    str(target),
                    suffixes={".docx"},
                    allowlist=[self._extern_tmp],
                )
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)

    def test_write_deny_dirs_only_apply_to_home_root(self) -> None:
        # home 下的 .ssh 仍然被拒
        ssh_like = Path(self._home_tmp) / ".ssh"
        ssh_like.mkdir(parents=True, exist_ok=True)
        target = ssh_like / "id_rsa"
        # 该路径会先 resolve；".ssh" 出现在 rel_parts 应触发 deny
        with self.assertRaises(ValueError):
            safe_path(str(target), access="write")

        # 在 allowlist 目录下创建同名 .ssh：不应被拦
        extern_ssh = Path(self._extern_tmp) / ".ssh"
        extern_ssh.mkdir(parents=True, exist_ok=True)
        target2 = extern_ssh / "id_rsa"
        result = safe_path(
            str(target2),
            access="write",
            allowlist=[self._extern_tmp],
        )
        self.assertEqual(result, str(target2.resolve()))

    def test_allowlist_with_nonexistent_dir_is_ignored(self) -> None:
        # allowlist 项必须是 is_dir，否则忽略；该目录不存在 → 行为同空 allowlist
        target = Path(self._extern_tmp) / "a.docx"
        with self.assertRaises(ValueError):
            safe_path(
                str(target),
                allowlist=["/this/does/not/exist/anywhere/zz"],
            )


if __name__ == "__main__":
    unittest.main()
