"""
docx 评论（批注）读写。

python-docx 原生不支持评论，要直接操作 XML。涉及 5 个文件：
  word/comments.xml          — 评论内容
  word/document.xml          — 段落里插 commentRangeStart/End + commentReference
  word/_rels/document.xml.rels — 注册 comments.xml 关系
  [Content_Types].xml        — 声明 comments.xml 的 content type

list_comments: 只读 comments.xml，解析出所有评论
add_comment:   多文件协同：确保 comments.xml 存在 + 注册关系 + 插引用
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


class DocxCommentError(RuntimeError):
    pass


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_W_NS}}}"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_R = f"{{{_R_NS}}}"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_CT = f"{{{_CT_NS}}}"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL = f"{{{_REL_NS}}}"

ET.register_namespace("w", _W_NS)
ET.register_namespace("r", _R_NS)
# Content_Types.xml 用默认 ns，ET 会自动处理


def _resolve_default_output(src: Path, tag: str = "with-comment") -> Path:
    try:
        import project as project_mod
    except ImportError:
        return src.with_name(src.stem + f"-{tag}.docx")
    active = project_mod.get_active()
    if not active:
        return src.with_name(src.stem + f"-{tag}.docx")
    path = project_mod.next_version_path(src.stem, ext=".docx", label=tag)
    return path or src.with_name(src.stem + f"-{tag}.docx")


# ========================================================================
# 1. 列评论
# ========================================================================

def list_comments(source_path: str) -> list[dict[str, Any]]:
    """
    扫 word/comments.xml，返回所有评论：
    [{id, author, date, text, initials}, ...]
    没有 comments.xml 就返回空数组。
    """
    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise DocxCommentError(f"文件不存在：{src}")

    comments: list[dict[str, Any]] = []
    with zipfile.ZipFile(src, "r") as zf:
        if "word/comments.xml" not in zf.namelist():
            return comments
        with zf.open("word/comments.xml") as f:
            try:
                tree = ET.parse(f)
            except ET.ParseError:
                return comments

    root = tree.getroot()
    for c in root.findall(f"{_W}comment"):
        comment_id = c.get(f"{_W}id", "")
        author = c.get(f"{_W}author", "")
        date = c.get(f"{_W}date", "")
        initials = c.get(f"{_W}initials", "")
        # 收集所有 w:t
        text_parts = []
        for t in c.findall(f".//{_W}t"):
            if t.text:
                text_parts.append(t.text)
        comments.append({
            "id": comment_id,
            "author": author,
            "date": date,
            "initials": initials,
            "text": "".join(text_parts),
        })
    return comments


# ========================================================================
# 2. 加评论
# ========================================================================

def add_comment(
    source_path: str,
    *,
    target_text: str,
    comment_text: str,
    author: str = "steelg8",
    initials: str = "s8",
    output_path: str | None = None,
) -> dict[str, Any]:
    """
    给 document 里第一次出现 target_text 的段落加一条评论。

    target_text: 段落里要高亮引用的文本（精确匹配，会被 commentRange 包住）
    comment_text: 评论内容
    author / initials: 评论者信息

    返回 {output_path, comment_id, author, target_text, matched: bool}
    """
    import datetime

    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise DocxCommentError(f"文件不存在：{src}")
    out = Path(output_path).expanduser().resolve() if output_path else _resolve_default_output(src)
    out.parent.mkdir(parents=True, exist_ok=True)

    # 用临时目录解包操作再重打包
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(src, "r") as zf:
            zf.extractall(tmp)
        tmp_path = Path(tmp)
        doc_xml = tmp_path / "word" / "document.xml"
        comments_xml = tmp_path / "word" / "comments.xml"
        rels_xml = tmp_path / "word" / "_rels" / "document.xml.rels"
        content_types = tmp_path / "[Content_Types].xml"

        if not doc_xml.exists():
            raise DocxCommentError("docx 损坏：缺 word/document.xml")

        # 分配新 comment id：扫现有 comments.xml（如果有的话）
        existing_ids = []
        if comments_xml.exists():
            try:
                tree = ET.parse(comments_xml)
                for c in tree.getroot().findall(f"{_W}comment"):
                    cid = c.get(f"{_W}id", "")
                    if cid.isdigit():
                        existing_ids.append(int(cid))
            except ET.ParseError:
                pass
        new_id = str(max(existing_ids) + 1 if existing_ids else 1)

        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

        # 1. 写 comments.xml（新建或追加）
        if comments_xml.exists():
            tree = ET.parse(comments_xml)
            comments_root = tree.getroot()
        else:
            comments_xml.parent.mkdir(parents=True, exist_ok=True)
            # 注意：不用 f"xmlns:w" 当属性，register_namespace 会自动插
            comments_root = ET.Element(f"{_W}comments")
            tree = ET.ElementTree(comments_root)

        new_comment = ET.SubElement(comments_root, f"{_W}comment", {
            f"{_W}id": new_id,
            f"{_W}author": author,
            f"{_W}date": now,
            f"{_W}initials": initials,
        })
        comment_p = ET.SubElement(new_comment, f"{_W}p")
        comment_r = ET.SubElement(comment_p, f"{_W}r")
        comment_t = ET.SubElement(comment_r, f"{_W}t")
        comment_t.text = comment_text

        tree.write(comments_xml, xml_declaration=True, encoding="UTF-8")

        # 2. 改 document.xml 插 commentRange 标记
        doc_tree = ET.parse(doc_xml)
        doc_root = doc_tree.getroot()

        matched = _inject_comment_range(doc_root, target_text, new_id)
        if not matched:
            return {
                "error": f"文档里找不到目标文本：{target_text!r}",
                "matched": False,
            }

        doc_tree.write(doc_xml, xml_declaration=True, encoding="UTF-8")

        # 3. _rels/document.xml.rels 注册（如果未注册）
        _ensure_comments_rel(rels_xml)

        # 4. [Content_Types].xml 声明（如果未声明）
        _ensure_comments_content_type(content_types)

        # 5. 重打包
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(tmp_path.rglob("*")):
                if p.is_file():
                    zf.write(p, str(p.relative_to(tmp_path)))

    return {
        "output_path": str(out),
        "comment_id": new_id,
        "author": author,
        "target_text": target_text,
        "matched": True,
    }


def _inject_comment_range(doc_root, target_text: str, comment_id: str) -> bool:
    """
    在 document.xml 里找第一个包含 target_text 的段落，给它包 commentRangeStart/End +
    commentReference。
    """
    for p in doc_root.iter(f"{_W}p"):
        # 把 p 里所有 w:t 的文本拼起来看包不包含
        texts = []
        for t in p.findall(f".//{_W}t"):
            if t.text:
                texts.append(t.text)
        full = "".join(texts)
        if target_text not in full:
            continue

        # 在 p 的第一个 w:r 前插 commentRangeStart，最后一个 w:r 后插 commentRangeEnd + reference
        runs = list(p.findall(f".//{_W}r"))
        if not runs:
            continue

        # commentRangeStart 在第一个 r 之前
        start = ET.Element(f"{_W}commentRangeStart", {f"{_W}id": comment_id})
        first_r = runs[0]
        parent = _find_parent(doc_root, first_r)
        if parent is not None:
            idx = list(parent).index(first_r)
            parent.insert(idx, start)

        # commentRangeEnd + w:r > w:commentReference 在最后一个 r 之后
        last_r = runs[-1]
        parent2 = _find_parent(doc_root, last_r)
        if parent2 is None:
            return True  # 至少 start 插上了
        end = ET.Element(f"{_W}commentRangeEnd", {f"{_W}id": comment_id})
        ref_r = ET.Element(f"{_W}r")
        ref = ET.SubElement(ref_r, f"{_W}commentReference", {f"{_W}id": comment_id})

        idx2 = list(parent2).index(last_r)
        parent2.insert(idx2 + 1, end)
        parent2.insert(idx2 + 2, ref_r)
        return True

    return False


def _find_parent(root, child):
    for p in root.iter():
        if child in list(p):
            return p
    return None


def _ensure_comments_rel(rels_xml: Path) -> None:
    """确保 _rels/document.xml.rels 里有 comments.xml 的关系。"""
    if not rels_xml.exists():
        return  # 文档级 rels 应该一定有，没有就是损坏

    tree = ET.parse(rels_xml)
    root = tree.getroot()

    # 查是否已有 comments 关系
    for r in root.findall(f"{_REL}Relationship"):
        if r.get("Type", "").endswith("/comments"):
            return  # 已有

    # 找下一个可用 rId
    existing_ids = set()
    for r in root.findall(f"{_REL}Relationship"):
        rid = r.get("Id", "")
        m = re.match(r"^rId(\d+)$", rid)
        if m:
            existing_ids.add(int(m.group(1)))
    next_num = max(existing_ids) + 1 if existing_ids else 1
    new_rid = f"rId{next_num}"

    new_rel = ET.SubElement(root, f"{_REL}Relationship", {
        "Id": new_rid,
        "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
        "Target": "comments.xml",
    })
    tree.write(rels_xml, xml_declaration=True, encoding="UTF-8")


def _ensure_comments_content_type(ct_xml: Path) -> None:
    """确保 [Content_Types].xml 里有 comments.xml 的 content type 声明。"""
    if not ct_xml.exists():
        return

    tree = ET.parse(ct_xml)
    root = tree.getroot()

    # 查是否已有 comments 声明
    for o in root.findall(f"{_CT}Override"):
        if o.get("PartName", "") == "/word/comments.xml":
            return

    ET.SubElement(root, f"{_CT}Override", {
        "PartName": "/word/comments.xml",
        "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
    })
    tree.write(ct_xml, xml_declaration=True, encoding="UTF-8")
