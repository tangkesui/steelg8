"""
文档 diff：对比两份文件的文本差异，返回结构化 diff 给 LLM 解读。

支持：.md / .txt / .docx / .pdf / .pptx / .doc —— 都先走 extract.read_text
抽成纯文本（带 markdown 结构标记），再走 Python 自带的 difflib 做 unified diff。

这是给 agent tool 用的，不是给用户读 diff 本身 —— 返回的 diff 会让 LLM 解读
成"改了什么 / 加了什么 / 删了什么"。
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

import extract


MAX_LINES = 2000   # 对比后最长保留的 diff 行数
PREVIEW_LINES = 500  # 出口截断


def diff_files(path_a: str, path_b: str, *, context: int = 2) -> dict[str, Any]:
    pa, pb = Path(path_a).expanduser(), Path(path_b).expanduser()
    if not pa.exists():
        return {"error": f"path_a 不存在：{pa}"}
    if not pb.exists():
        return {"error": f"path_b 不存在：{pb}"}

    a = _safe_extract(str(pa))
    b = _safe_extract(str(pb))

    a_lines = a.splitlines(keepends=False)
    b_lines = b.splitlines(keepends=False)

    diff = list(difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile=str(pa.name),
        tofile=str(pb.name),
        lineterm="",
        n=context,
    ))

    # 统计
    added = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))

    truncated = False
    if len(diff) > PREVIEW_LINES:
        diff = diff[:PREVIEW_LINES]
        truncated = True

    return {
        "before": str(pa),
        "after": str(pb),
        "added_lines": added,
        "removed_lines": removed,
        "total_lines_a": len(a_lines),
        "total_lines_b": len(b_lines),
        "diff": "\n".join(diff),
        "truncated": truncated,
    }


def _safe_extract(path: str) -> str:
    try:
        return extract.read_text(path)
    except Exception:
        return ""
