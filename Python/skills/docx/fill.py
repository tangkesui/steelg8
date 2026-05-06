"""
docx 模板填充 skill
--------------------

核心行为：加载一个已有的 .docx 模板，把所有 `{{key}}` 替换为 data[key]。

保留原段落 / 表格单元格的字体、字号、颜色、粗体等格式（替换只发生在 run
级别，一次命中一个 placeholder 只改一个 run 的 text，不动属性）。

支持嵌套路径：`{{project.name}}` → data["project"]["name"]。
不存在的 key：保留 placeholder 原样（便于迭代填充时先提交部分字段）。

不处理的情况（未来扩展）：
- placeholder 跨多个 run（Word 里改过字体颜色会把一段切成多个 run），当前
  代码会先 merge 同一段的 run 再替换，损失变化中的内嵌格式但保证可用
- 条件渲染 / 循环 —— 这是 jinja2 风格，要看用户需不需要
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _resolve_default_output(src: Path, task_name: str | None) -> Path:
    """默认输出：有激活项目就走 steelg8-output/<task>/v{N}.docx，否则模板同目录。"""
    try:
        import project as project_mod  # noqa: E402
    except ImportError:
        return src.with_name(src.stem + "-filled.docx")
    active = project_mod.get_active()
    if not active:
        return src.with_name(src.stem + "-filled.docx")
    task = task_name or src.stem
    path = project_mod.next_version_path(task, ext=".docx")
    if path:
        return path
    return src.with_name(src.stem + "-filled.docx")


PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}")


class DocxFillError(RuntimeError):
    pass


@dataclass
class FillResult:
    output_path: str
    replaced_count: int
    missing_keys: list[str]        # 模板里出现了但 data 没提供的 key
    leftover_placeholders: list[str]  # 替换后仍保留的 placeholder（嵌套失败等）


def _lookup(data: Any, path: str) -> Any:
    """按点分路径从 data 里取值。任一层不存在返回 None。"""
    if data is None:
        return None
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def _merge_runs(paragraph: "object") -> None:
    """把同一个段落里的所有 run 文本合并到第一个 run，清掉其余 run 的文本。
    这样 placeholder 跨 run 的边界问题就消失，代价是失去段内格式变化。
    """
    runs = list(paragraph.runs)
    if len(runs) <= 1:
        return
    first = runs[0]
    full = "".join(r.text or "" for r in runs)
    first.text = full
    for r in runs[1:]:
        r.text = ""


def _replace_in_text(text: str, data: Any, missing: set[str]) -> tuple[str, int]:
    """在字符串里替换 placeholder，返回 (新字符串, 成功替换数)。"""
    replaced = 0

    def sub(m: "re.Match[str]") -> str:
        nonlocal replaced
        key = m.group(1)
        val = _lookup(data, key)
        if val is None:
            missing.add(key)
            return m.group(0)  # 保持原样
        replaced += 1
        return str(val)

    new_text = PLACEHOLDER_RE.sub(sub, text)
    return new_text, replaced


def _fill_paragraph(paragraph: "object", data: Any, missing: set[str]) -> int:
    """就地替换段落里的 placeholder。返回本段替换次数。"""
    runs = list(paragraph.runs)
    if not runs:
        return 0
    # 先检查是否有 placeholder；没有就别费劲 merge（保留原格式）
    whole = "".join(r.text or "" for r in runs)
    if "{{" not in whole:
        return 0
    _merge_runs(paragraph)
    first = paragraph.runs[0]
    new_text, replaced = _replace_in_text(first.text or "", data, missing)
    first.text = new_text
    return replaced


def _fill_tables(doc: "object", data: Any, missing: set[str]) -> int:
    total = 0
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    total += _fill_paragraph(p, data, missing)
    return total


def fill(
    template_path: str,
    data: Any,
    output_path: str | None = None,
    *,
    task_name: str | None = None,
) -> FillResult:
    """在模板副本上做 placeholder 替换；不改原文件。

    输出路径优先级：
      1. 显式传入的 `output_path`
      2. 当前激活项目有 —— 走 <project>/steelg8-output/<task_name>/v{N}.docx
      3. 无激活项目 —— 退回到模板同目录 `*-filled.docx`（旧行为）
    """
    try:
        from docx import Document
    except ImportError as exc:
        raise DocxFillError("需要 python-docx；venv 里没装上。跑一次 ./bundle.sh") from exc

    src = Path(template_path).expanduser().resolve()
    if not src.exists():
        raise DocxFillError(f"模板不存在：{src}")

    if output_path:
        out = Path(output_path).expanduser().resolve()
    else:
        out = _resolve_default_output(src, task_name)

    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, out)

    doc = Document(str(out))
    missing: set[str] = set()
    replaced = 0

    for p in doc.paragraphs:
        replaced += _fill_paragraph(p, data, missing)
    replaced += _fill_tables(doc, data, missing)

    # 页眉页脚（section 级别）
    for section in doc.sections:
        for p in section.header.paragraphs:
            replaced += _fill_paragraph(p, data, missing)
        for p in section.footer.paragraphs:
            replaced += _fill_paragraph(p, data, missing)

    doc.save(str(out))

    # 扫一下剩余 placeholder 做审计
    leftover: set[str] = set()
    from docx import Document as _Doc
    check = _Doc(str(out))
    for p in check.paragraphs:
        for m in PLACEHOLDER_RE.finditer(p.text or ""):
            leftover.add(m.group(1))
    for tbl in check.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for m in PLACEHOLDER_RE.finditer(cell.text or ""):
                    leftover.add(m.group(1))

    return FillResult(
        output_path=str(out),
        replaced_count=replaced,
        missing_keys=sorted(missing),
        leftover_placeholders=sorted(leftover),
    )


def list_placeholders(template_path: str) -> list[str]:
    """读模板里所有 placeholder 名字（去重，排序）。"""
    try:
        from docx import Document
    except ImportError as exc:
        raise DocxFillError("需要 python-docx") from exc

    doc = Document(str(Path(template_path).expanduser().resolve()))
    names: set[str] = set()
    for p in doc.paragraphs:
        for m in PLACEHOLDER_RE.finditer(p.text or ""):
            names.add(m.group(1))
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for m in PLACEHOLDER_RE.finditer(p.text or ""):
                        names.add(m.group(1))
    return sorted(names)
