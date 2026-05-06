"""
docx 图片插入。

python-docx 原生支持 add_picture(），但只能追加到文档末尾。
我们复用 docx/grow.py 同款套路：先 add_picture 造段落，再用 lxml
搬到指定锚点标题之后。

图片插入涉及的 .docx 内部结构：
  - word/media/imageN.png   图片二进制
  - word/_rels/document.xml.rels  注册关系 rIdN → media/image.png
  - [Content_Types].xml     声明 content type
  - word/document.xml       插入 <w:drawing> + <pic:pic> 引用
python-docx 会自动处理所有这些，我们只需把生成的段落搬到目标位置。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


class DocxMediaError(RuntimeError):
    pass


_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}


def _resolve_default_output(src: Path) -> Path:
    """沿用 steelg8 项目输出目录。"""
    try:
        import project as project_mod
    except ImportError:
        return src.with_name(src.stem + "-with-image.docx")
    active = project_mod.get_active()
    if not active:
        return src.with_name(src.stem + "-with-image.docx")
    path = project_mod.next_version_path(src.stem, ext=".docx")
    return path or src.with_name(src.stem + "-with-image.docx")


def insert_image(
    source_path: str,
    *,
    image_path: str,
    after_heading: str,
    width_cm: float | None = None,
    height_cm: float | None = None,
    caption: str | None = None,
    anchor_level: int | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """
    在指定标题章节末尾插入一张图片（可选带图题 Caption）。

    image_path: 图片文件绝对路径（png/jpg/jpeg/gif/bmp/tiff/webp）
    width_cm / height_cm: 尺寸（厘米），不填走原图尺寸；只填一个会等比缩放
    caption: 可选图题 '图 3-1 系统架构图'
    """
    try:
        from docx import Document
        from docx.shared import Cm
        from docx.oxml.ns import qn
    except ImportError as exc:
        raise DocxMediaError("需要 python-docx") from exc

    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise DocxMediaError(f"文件不存在：{src}")

    img = Path(image_path).expanduser().resolve()
    if not img.exists():
        raise DocxMediaError(f"图片不存在：{img}")
    if img.suffix.lower() not in _IMG_EXTS:
        raise DocxMediaError(f"不支持的图片格式：{img.suffix}。支持：{sorted(_IMG_EXTS)}")

    out = Path(output_path).expanduser().resolve() if output_path else _resolve_default_output(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out != src:
        shutil.copyfile(src, out)

    doc = Document(str(out))

    # 找锚点
    from skills.docx import grow as docx_grow
    anchor_p = None
    for p in doc.paragraphs:
        if (p.text or "").strip() == after_heading.strip():
            if anchor_level is not None:
                style_name = getattr(p.style, "name", "") or ""
                if not style_name.startswith(f"Heading {anchor_level}"):
                    continue
            anchor_p = p
            break
    if anchor_p is None:
        available = []
        for p in doc.paragraphs:
            style_name = getattr(p.style, "name", "") or ""
            if style_name.startswith("Heading "):
                t = (p.text or "").strip()
                if t:
                    available.append(t)
        return {
            "error": f"找不到锚点标题：{after_heading!r}",
            "available_headings": available[:40],
        }

    # 找章节末尾
    section_end = docx_grow._find_section_end_anchor(doc, anchor_p)

    # 1. 文档末尾 add_picture
    size_kwargs: dict[str, Any] = {}
    if width_cm is not None:
        size_kwargs["width"] = Cm(float(width_cm))
    if height_cm is not None:
        size_kwargs["height"] = Cm(float(height_cm))
    pic_para = doc.add_paragraph()
    run = pic_para.add_run()
    run.add_picture(str(img), **size_kwargs)

    # 2. 取出 XML 元素
    pic_elem = pic_para._p
    pic_elem.getparent().remove(pic_elem)

    # 3. 插到 section_end 之后
    section_end._p.addnext(pic_elem)

    # 4. 可选 caption
    cap_added = False
    if caption:
        cap_p = pic_elem.makeelement(qn("w:p"), {})
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
        pic_elem.addnext(cap_p)
        cap_added = True

    doc.save(str(out))
    return {
        "output_path": str(out),
        "image_file": str(img),
        "after_heading": after_heading,
        "caption": caption or "",
        "caption_added": cap_added,
        "width_cm": width_cm,
        "height_cm": height_cm,
    }
