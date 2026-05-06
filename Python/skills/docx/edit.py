"""
docx_edit —— docx 文档编辑：完整插表 / 全文替换 / 标题改名 / 段落删除 / 合规审查

设计原则（跟 docx_grow / docx_fill 对齐）：
- 所有函数接收绝对路径 + 标题锚点（不用段落 index，保语义稳定）
- 原子保存：全部修改跑完再 save()，中途抛错不留残破文档
- 默认输出走 project.next_version_path()（steelg8-output/<任务>/v{N}.docx）
- 失败时返回带 hint 的 dict（给 LLM 自救用）

参考：toolkit 给人类脚本用的 DocxEditor 类，我们适配成 LLM 友好的纯函数。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


class DocxEditError(RuntimeError):
    pass


def _resolve_default_output(src: Path) -> Path:
    """优先走 <project>/steelg8-output/<src.stem>/v{N}.docx，否则退回原目录。"""
    try:
        import project as project_mod
    except ImportError:
        return src.with_name(src.stem + "-edited.docx")
    active = project_mod.get_active()
    if not active:
        return src.with_name(src.stem + "-edited.docx")
    path = project_mod.next_version_path(src.stem, ext=".docx")
    return path or src.with_name(src.stem + "-edited.docx")


def _open_copy(source_path: str, output_path: str | None):
    """打开目标副本。sibling write 模式：不污染原稿。"""
    try:
        from docx import Document
    except ImportError as exc:
        raise DocxEditError("需要 python-docx；venv 里没装上") from exc

    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise DocxEditError(f"文件不存在：{src}")

    if output_path:
        out = Path(output_path).expanduser().resolve()
    else:
        out = _resolve_default_output(src)

    out.parent.mkdir(parents=True, exist_ok=True)
    if out != src:
        shutil.copyfile(src, out)
    return Document(str(out)), out


def _find_heading_paragraph(doc, heading_text: str, level: int | None = None):
    """按文本精确匹配标题（可选限定级别）。"""
    target = (heading_text or "").strip()
    for p in doc.paragraphs:
        if (p.text or "").strip() != target:
            continue
        if level is not None:
            style_name = getattr(p.style, "name", "") or ""
            if not style_name.startswith(f"Heading {level}"):
                continue
        return p
    return None


def _list_available_headings(doc, limit: int = 50) -> list[str]:
    """给失败 hint 用：列出文档里真实存在的标题文本。"""
    out = []
    for p in doc.paragraphs:
        style_name = getattr(p.style, "name", "") or ""
        if style_name.startswith("Heading "):
            t = (p.text or "").strip()
            if t:
                out.append(t)
                if len(out) >= limit:
                    break
    return out


# ========================================================================
# 1. 完整带样式插入表格
# ========================================================================

def insert_table(
    source_path: str,
    *,
    after_heading: str,
    headers: list[str],
    rows: list[list[str]],
    caption: str | None = None,
    anchor_level: int | None = None,
    font_size: int = 10,
    output_path: str | None = None,
) -> dict[str, Any]:
    """
    在指定标题所在章节末尾插入一个完整格式化表格。

    headers: ["项目", "金额（万元）", "占比"]
    rows:    [["硬件", "120", "60%"], ["软件", "60", "30%"], ...]
    caption: 可选表题，如 "表 7-1 投资估算"
    font_size: 字号（pt）

    内部逻辑（复用 toolkit insert_table 的思路）：
    1. 先 `doc.add_table()` 在文档末尾造表 → 填头/数据/样式
    2. 把 table 的 lxml element 从末尾"剪"下来
    3. 定位 after_heading 所属章节末尾 anchor
    4. anchor.addnext(table_element)
    5. 如 caption，先 addnext 一个 Caption 段落、再把 table 接在 caption 之后
    """
    try:
        from docx.shared import Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.oxml.ns import qn
    except ImportError as exc:
        raise DocxEditError("需要 python-docx") from exc

    if not headers:
        raise DocxEditError("headers 不能为空")
    if not rows:
        raise DocxEditError("rows 不能为空")

    doc, out = _open_copy(source_path, output_path)

    anchor_p = _find_heading_paragraph(doc, after_heading, level=anchor_level)
    if anchor_p is None:
        return {
            "error": f"找不到锚点标题：{after_heading!r}",
            "hint": "after_heading 必须精确匹配文档里已有的标题文本（标点、空格都敏感）",
            "available_headings": _list_available_headings(doc),
        }

    # 1. 文档末尾先建表
    tbl = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

    # 表头：加粗 + 居中
    for j, h in enumerate(headers):
        cell = tbl.rows[0].cells[j]
        cell.text = str(h)
        for p in cell.paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(font_size)

    # 数据行
    for i, row in enumerate(rows):
        for j in range(len(headers)):
            val = row[j] if j < len(row) else ""
            cell = tbl.rows[i + 1].cells[j]
            cell.text = str(val)
            for p in cell.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(font_size)

    # 2. 从末尾剪下 table element
    tbl_elem = tbl._tbl
    tbl_elem.getparent().remove(tbl_elem)

    # 3. 找章节末尾 anchor（复用 docx_grow 的逻辑）
    from skills.docx import grow as docx_grow
    section_end = docx_grow._find_section_end_anchor(doc, anchor_p)

    # 4. 插入 caption（可选）+ 表格
    notes: list[str] = []
    if caption:
        cap_p = tbl_elem.makeelement(qn("w:p"), {})
        pPr = cap_p.makeelement(qn("w:pPr"), {})
        pStyle = pPr.makeelement(qn("w:pStyle"), {})
        pStyle.set(qn("w:val"), "Caption")
        pPr.append(pStyle)
        cap_p.append(pPr)
        r = cap_p.makeelement(qn("w:r"), {})
        t = r.makeelement(qn("w:t"), {})
        t.text = caption
        t.set(qn("xml:space"), "preserve")
        r.append(t)
        cap_p.append(r)
        section_end._p.addnext(cap_p)
        cap_p.addnext(tbl_elem)
        notes.append(f"在 {after_heading!r} 后插入表题 + 表格（{len(headers)}列×{len(rows)}行）")
    else:
        section_end._p.addnext(tbl_elem)
        notes.append(f"在 {after_heading!r} 后插入表格（{len(headers)}列×{len(rows)}行）")

    doc.save(str(out))
    return {
        "output_path": str(out),
        "cols": len(headers),
        "rows": len(rows),
        "caption": caption or "",
        "notes": notes,
    }


# ========================================================================
# 2. 全文文本替换
# ========================================================================

def replace_text(
    source_path: str,
    *,
    replacements: dict[str, str],
    scope: str = "all",  # "all" | "body" | "tables"
    output_path: str | None = None,
) -> dict[str, Any]:
    """
    全文批量替换文本。保留 run 样式（逐 run 扫描）。

    replacements: {"XX公司": "新疆油田公司", "XX项目": "智慧服务平台"}
    scope:
      - "all": 正文 + 表格都替换
      - "body": 仅段落
      - "tables": 仅表格单元格

    返回每个键的替换次数。
    """
    if not replacements:
        raise DocxEditError("replacements 不能为空")

    doc, out = _open_copy(source_path, output_path)

    counts: dict[str, int] = {k: 0 for k in replacements}

    def _replace_in_paragraph(para):
        for run in para.runs:
            for old, new in replacements.items():
                if old in run.text:
                    run.text = run.text.replace(old, new)
                    counts[old] += 1

    if scope in ("all", "body"):
        for p in doc.paragraphs:
            _replace_in_paragraph(p)

    if scope in ("all", "tables"):
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        _replace_in_paragraph(p)

    doc.save(str(out))
    return {
        "output_path": str(out),
        "counts": counts,
        "total_replaced": sum(counts.values()),
        "scope": scope,
    }


# ========================================================================
# 3. 标题重命名
# ========================================================================

def rename_heading(
    source_path: str,
    *,
    old_title: str,
    new_title: str,
    level: int | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """
    精准改一个标题的文本，保留原样式（Heading 级别不变）。
    """
    doc, out = _open_copy(source_path, output_path)

    target = _find_heading_paragraph(doc, old_title, level=level)
    if target is None:
        return {
            "error": f"找不到标题：{old_title!r}",
            "available_headings": _list_available_headings(doc),
        }

    # 保留第一个 run 的样式，把文本改了；其余 runs 清空
    if target.runs:
        target.runs[0].text = new_title
        for r in target.runs[1:]:
            r.text = ""
    else:
        # 没 run（极端情况）→ 写 XML
        from docx.oxml.ns import qn
        r = target._element.makeelement(qn("w:r"), {})
        t = r.makeelement(qn("w:t"), {})
        t.text = new_title
        t.set(qn("xml:space"), "preserve")
        r.append(t)
        target._element.append(r)

    doc.save(str(out))
    return {
        "output_path": str(out),
        "renamed": {"from": old_title, "to": new_title},
    }


# ========================================================================
# 4. 段落删除（按标题锚点）
# ========================================================================

def delete_section(
    source_path: str,
    *,
    heading: str,
    level: int | None = None,
    delete_range: str = "heading_only",  # "heading_only" | "heading_and_body"
    output_path: str | None = None,
) -> dict[str, Any]:
    """
    删除某个标题（可选一并删除它下面的章节内容）。

    delete_range:
      - "heading_only":     只删这一段标题
      - "heading_and_body": 删标题 + 它所属章节（到下一个同级标题前）
    """
    doc, out = _open_copy(source_path, output_path)

    target = _find_heading_paragraph(doc, heading, level=level)
    if target is None:
        return {
            "error": f"找不到标题：{heading!r}",
            "available_headings": _list_available_headings(doc),
        }

    removed_count = 0

    if delete_range == "heading_and_body":
        from skills.docx import grow as docx_grow
        # 从 target 开始，一直到下一个同级标题之前的所有 element 都删
        body = doc.element.body
        children = list(body.iterchildren())
        try:
            start_idx = children.index(target._element)
        except ValueError:
            return {"error": "定位失败"}

        target_level = docx_grow._heading_level_of(target) or 99
        para_by_p = {p._p: p for p in doc.paragraphs}

        end_idx = len(children)
        W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        for i in range(start_idx + 1, len(children)):
            child = children[i]
            if child.tag == f"{{{W_NS}}}p":
                p = para_by_p.get(child)
                if p is not None:
                    lvl = docx_grow._heading_level_of(p)
                    if lvl is not None and lvl <= target_level:
                        end_idx = i
                        break

        # 删除 [start_idx, end_idx) 的所有 element
        for child in children[start_idx:end_idx]:
            body.remove(child)
            removed_count += 1
    else:
        target._element.getparent().remove(target._element)
        removed_count = 1

    doc.save(str(out))
    return {
        "output_path": str(out),
        "removed_elements": removed_count,
        "deleted_heading": heading,
        "mode": delete_range,
    }


# ========================================================================
# 5. 文档合规审查（章节 + 表格清单）
# ========================================================================

def check_compliance(
    source_path: str,
    *,
    required_headings: list[str] | None = None,
    required_tables: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """
    按清单检查文档是否缺少章节 / 表格。写完方案终稿自检用。

    required_headings: ["一、项目概述", "七、投资估算"]  - 必须出现的标题
    required_tables: {"投资估算表": ["项目", "金额"]}   - 必须出现的表格（按表头关键词匹配）

    返回 {found, missing, warnings}。
    """
    try:
        from docx import Document
    except ImportError as exc:
        raise DocxEditError("需要 python-docx") from exc

    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise DocxEditError(f"文件不存在：{src}")

    doc = Document(str(src))

    report: dict[str, Any] = {
        "path": str(src),
        "headings": {"found": [], "missing": []},
        "tables": {"found": [], "missing": []},
        "warnings": [],
    }

    # 章节检查
    all_headings = _list_available_headings(doc, limit=500)
    all_text = "\n".join(p.text for p in doc.paragraphs)
    if required_headings:
        for h in required_headings:
            # 标题文本精确包含 (比 exact match 宽松点)
            if h in all_headings or h in all_text:
                report["headings"]["found"].append(h)
            else:
                report["headings"]["missing"].append(h)

    # 表格检查
    if required_tables:
        for tbl_name, required_cols in required_tables.items():
            matched = False
            for tbl in doc.tables:
                if not tbl.rows:
                    continue
                all_tbl_text = " ".join(c.text for r in tbl.rows for c in r.cells)
                if all(col in all_tbl_text for col in required_cols):
                    matched = True
                    break
            if matched:
                report["tables"]["found"].append(tbl_name)
            else:
                report["tables"]["missing"].append(tbl_name)

    # 给个总体健康度
    total_required = (len(required_headings or []) + len(required_tables or {}))
    total_missing = len(report["headings"]["missing"]) + len(report["tables"]["missing"])
    if total_required:
        report["completion_pct"] = round((total_required - total_missing) / total_required * 100, 1)
    else:
        report["completion_pct"] = None

    # 一些警告（经验规则）
    if not report["headings"]["found"] and not all_headings:
        report["warnings"].append("文档内没有任何 Heading 样式的标题，可能是纯正文文档")
    if len(doc.tables) == 0 and required_tables:
        report["warnings"].append("文档里完全没有表格")

    return report
