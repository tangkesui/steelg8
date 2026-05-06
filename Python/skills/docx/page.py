"""
docx 页面级能力：页眉 / 页脚 / 目录（TOC）

- 页眉页脚：python-docx 原生支持 section.header / section.footer，直接用
- TOC：python-docx 没封装，我们手写 w:sdt + w:fldChar 指令
  Word 打开时会弹提示"要更新目录吗？"，按 Yes 就能自动扫描所有 Heading 生成
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


class DocxPageError(RuntimeError):
    pass


def _resolve_default_output(src: Path, tag: str = "edited") -> Path:
    try:
        import project as project_mod
    except ImportError:
        return src.with_name(src.stem + f"-{tag}.docx")
    active = project_mod.get_active()
    if not active:
        return src.with_name(src.stem + f"-{tag}.docx")
    path = project_mod.next_version_path(src.stem, ext=".docx", label=tag)
    return path or src.with_name(src.stem + f"-{tag}.docx")


def _open_copy(source_path: str, output_path: str | None, *, tag: str):
    try:
        from docx import Document
    except ImportError as exc:
        raise DocxPageError("需要 python-docx") from exc

    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise DocxPageError(f"文件不存在：{src}")

    out = Path(output_path).expanduser().resolve() if output_path else _resolve_default_output(src, tag)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out != src:
        shutil.copyfile(src, out)
    return Document(str(out)), out


# ========================================================================
# 1. 设置页眉 / 页脚
# ========================================================================

def set_header_footer(
    source_path: str,
    *,
    header_text: str | None = None,
    footer_text: str | None = None,
    footer_with_page_number: bool = False,
    section_index: int = 0,
    output_path: str | None = None,
) -> dict[str, Any]:
    """
    设置页眉 / 页脚。默认动第一个 section。

    footer_with_page_number=True 时，页脚 = footer_text + "  第 X 页 共 Y 页"
    """
    try:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
    except ImportError as exc:
        raise DocxPageError("需要 python-docx") from exc

    doc, out = _open_copy(source_path, output_path, tag="header-footer")
    sections = doc.sections
    if section_index >= len(sections):
        return {"error": f"section 索引越界：{section_index}（文档只有 {len(sections)} 个 section）"}
    section = sections[section_index]

    changed: list[str] = []

    # 页眉
    if header_text is not None:
        header = section.header
        for p in header.paragraphs:
            p.text = ""
        hp = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
        hp.text = header_text
        hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        changed.append(f"页眉已设为 {header_text!r}")

    # 页脚
    if footer_text is not None or footer_with_page_number:
        footer = section.footer
        for p in footer.paragraphs:
            p.text = ""
        fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER

        if footer_text:
            fp.add_run(footer_text)

        if footer_with_page_number:
            if footer_text:
                fp.add_run("    ")
            # 插 "第 X 页 共 Y 页" 字段
            # w:fldChar + w:instrText 组合
            run = fp.add_run("第 ")
            _add_simple_field(fp, "PAGE", OxmlElement, qn)
            fp.add_run(" 页 共 ")
            _add_simple_field(fp, "NUMPAGES", OxmlElement, qn)
            fp.add_run(" 页")
            changed.append("页码字段已插入")

        if footer_text:
            changed.append(f"页脚已设为 {footer_text!r}")

    if not changed:
        return {"error": "什么都没改——请至少传 header_text / footer_text / footer_with_page_number 之一"}

    doc.save(str(out))
    return {
        "output_path": str(out),
        "section_index": section_index,
        "changes": changed,
    }


def _add_simple_field(paragraph, instr: str, OxmlElement, qn):
    """在 paragraph 末尾插 <w:fldSimple> 字段（如 PAGE / NUMPAGES）。"""
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), instr)
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "1"  # 占位，Word 打开时刷新
    r.append(t)
    fld.append(r)
    paragraph._p.append(fld)


# ========================================================================
# 2. 插入目录 TOC
# ========================================================================

def insert_toc(
    source_path: str,
    *,
    title: str = "目录",
    levels: str = "1-3",
    after_heading: str | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """
    在文档里插入自动目录（TOC）。

    title: 目录标题文本，默认 "目录"
    levels: 取哪些级别的标题，格式 "1-3"
    after_heading: 插在哪个标题之后；不填就插到文档开头
    注意：Word 打开时会提示"要更新目录吗？"→ 点 Yes 扫描所有 Heading 填充。
         如果用户想立即看到目录（不弹提示），需要在 Word 里右键 → 更新域。
    """
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
    except ImportError as exc:
        raise DocxPageError("需要 python-docx") from exc

    doc, out = _open_copy(source_path, output_path, tag="with-toc")

    # 构造 TOC 相关 XML
    def _build_toc_elements():
        elements = []

        # 1. 标题段落
        title_p = OxmlElement("w:p")
        pPr = OxmlElement("w:pPr")
        pStyle = OxmlElement("w:pStyle")
        pStyle.set(qn("w:val"), "TOCHeading")
        pPr.append(pStyle)
        title_p.append(pPr)
        r = OxmlElement("w:r")
        t = OxmlElement("w:t")
        t.text = title
        t.set(qn("xml:space"), "preserve")
        r.append(t)
        title_p.append(r)
        elements.append(title_p)

        # 2. sdt 包起来的 TOC 字段
        sdt = OxmlElement("w:sdt")
        sdtPr = OxmlElement("w:sdtPr")
        docPartObj = OxmlElement("w:docPartObj")
        docPartGallery = OxmlElement("w:docPartGallery")
        docPartGallery.set(qn("w:val"), "Table of Contents")
        docPartObj.append(docPartGallery)
        docPartUnique = OxmlElement("w:docPartUnique")
        docPartObj.append(docPartUnique)
        sdtPr.append(docPartObj)
        sdt.append(sdtPr)

        sdtContent = OxmlElement("w:sdtContent")
        # 内部放一段含 fldChar 的段落
        tocField_p = OxmlElement("w:p")

        # begin
        begin_r = OxmlElement("w:r")
        begin_fld = OxmlElement("w:fldChar")
        begin_fld.set(qn("w:fldCharType"), "begin")
        begin_fld.set(qn("w:dirty"), "true")  # 打开时自动刷新
        begin_r.append(begin_fld)
        tocField_p.append(begin_r)

        # instrText
        instr_r = OxmlElement("w:r")
        instr_t = OxmlElement("w:instrText")
        instr_t.set(qn("xml:space"), "preserve")
        instr_t.text = f' TOC \\o "{levels}" \\h \\z \\u '
        instr_r.append(instr_t)
        tocField_p.append(instr_r)

        # separator
        sep_r = OxmlElement("w:r")
        sep_fld = OxmlElement("w:fldChar")
        sep_fld.set(qn("w:fldCharType"), "separate")
        sep_r.append(sep_fld)
        tocField_p.append(sep_r)

        # 中间放一个占位文字，Word 打开时会被替换
        hold_r = OxmlElement("w:r")
        hold_t = OxmlElement("w:t")
        hold_t.text = "（目录将在 Word 中打开时自动生成）"
        hold_r.append(hold_t)
        tocField_p.append(hold_r)

        # end
        end_r = OxmlElement("w:r")
        end_fld = OxmlElement("w:fldChar")
        end_fld.set(qn("w:fldCharType"), "end")
        end_r.append(end_fld)
        tocField_p.append(end_r)

        sdtContent.append(tocField_p)
        sdt.append(sdtContent)
        elements.append(sdt)

        return elements

    toc_elements = _build_toc_elements()

    # 选插入位置
    body = doc.element.body
    if after_heading:
        # 找锚点
        anchor_p = None
        for p in doc.paragraphs:
            if (p.text or "").strip() == after_heading.strip():
                anchor_p = p
                break
        if anchor_p is None:
            return {"error": f"找不到锚点标题：{after_heading!r}"}
        # 反向 addnext 保证顺序 (anchor / TOC 标题 / sdt)
        for el in reversed(toc_elements):
            anchor_p._element.addnext(el)
    else:
        # 插到 body 开头（在 sectPr 之前）
        for el in toc_elements:
            body.insert(0, el)
        # 反转下顺序保证 title 在前
        first = body[0]  # sdt
        second = body[1]  # title
        body.remove(first)
        body.remove(second)
        body.insert(0, second)
        body.insert(1, first)

    doc.save(str(out))
    return {
        "output_path": str(out),
        "title": title,
        "levels": levels,
        "position": f"after '{after_heading}'" if after_heading else "document start",
        "note": "Word 打开时会提示'更新目录'—— 点 Yes 即可自动填充",
    }
