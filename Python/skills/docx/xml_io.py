"""
docx XML 底层解包 / 重打包 / 校验

抽自 Anthropic 官方 skills/docx/ 的 unpack.py + pack.py + validate.py 思路。
.docx 本质是 ZIP + XML，python-docx 只覆盖一部分能力（如无法精细处理
tracked changes）。直接操作 XML 能做更多事。

这是**内部工具**，不直接暴露给 LLM。上层的 edit / grow / fill 模块按需调用。

关键能力：
  - unpack(docx_path, dest_dir): .docx → 解包成文件树
  - pack(src_dir, docx_path): 文件树 → .docx（自动修复）
  - validate(docx_path): 结构 / 样式 / 修订状态检查
  - iter_tracked_changes(docx_path): 扫描所有 <w:ins> / <w:del>
  - accept_all_changes(docx_path): 接受所有修订
  - reject_all_changes(docx_path): 拒绝所有修订
"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


# Word 命名空间（所有 docx XML 都用这个 ns）
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_W_NS}}}"
ET.register_namespace("w", _W_NS)


# ---------- unpack / pack ----------

def unpack(docx_path: str | Path, dest_dir: str | Path) -> Path:
    """把 .docx 解包成文件树。返回解包后的根目录。"""
    src = Path(docx_path).expanduser().resolve()
    dst = Path(dest_dir).expanduser().resolve()
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src, "r") as zf:
        zf.extractall(dst)
    return dst


def pack(src_dir: str | Path, docx_path: str | Path, *, auto_repair: bool = True) -> Path:
    """
    把文件树重新打包成 .docx。

    auto_repair=True 时做 Anthropic 同款修复：
      - 给所有带前导/尾随空白的 <w:t> 自动加 xml:space="preserve"
      - （未来可加）durableId 溢出修复
    """
    src = Path(src_dir).expanduser().resolve()
    out = Path(docx_path).expanduser().resolve()

    if auto_repair:
        _repair_xml_files(src)

    # ZIP 必须按 docx 期望的顺序（[Content_Types].xml 一定要最先）
    content_types = src / "[Content_Types].xml"
    if not content_types.exists():
        raise XmlIoError(f"源目录缺 [Content_Types].xml: {src}")

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(content_types, "[Content_Types].xml")
        for path in sorted(src.rglob("*")):
            if path.is_file() and path != content_types:
                arcname = str(path.relative_to(src))
                zf.write(path, arcname)
    return out


def _repair_xml_files(src_dir: Path) -> None:
    """
    扫所有 .xml 文件做微型修复：
    1. 带前导/尾随空格的 <w:t> 自动加 xml:space="preserve"
       （否则 Word 会吞掉空格，出现 "你好世界" 被写成 "你好世界" 的怪象）
    """
    pattern_open = re.compile(r'<w:t(?!\b:)(?=[ >])(?![^>]*xml:space)')

    for xml_file in src_dir.rglob("*.xml"):
        try:
            content = xml_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        original = content
        # 找内容有前导或尾随空格的 <w:t>（但没 xml:space 属性的）
        def _add_space_preserve(match_text: str) -> str:
            # 只处理 <w:t>xxx</w:t> 或 <w:t attrs>xxx</w:t> 情况
            m = re.match(r'(<w:t)([^>]*)(>)([^<]*)(</w:t>)', match_text)
            if not m:
                return match_text
            open_, attrs, gt, text, close = m.groups()
            if text and (text[0].isspace() or text[-1].isspace()):
                if "xml:space" not in attrs:
                    return f'{open_}{attrs} xml:space="preserve"{gt}{text}{close}'
            return match_text

        content = re.sub(
            r'<w:t[^/>]*>[^<]*</w:t>',
            lambda m: _add_space_preserve(m.group(0)),
            content,
        )

        if content != original:
            xml_file.write_text(content, encoding="utf-8")


# ---------- validate ----------

class XmlIoError(RuntimeError):
    pass


def validate(docx_path: str | Path) -> dict[str, Any]:
    """
    结构 + 样式 + 修订状态检查。

    返回：{
        ok: bool,
        issues: [str, ...],     # 错误（文档可能损坏）
        warnings: [str, ...],   # 警告（能打开但可能有怪问题）
        stats: {
            paragraph_count, heading_count, table_count,
            tracked_changes_count, comment_count,
            image_count, hyperlink_count,
        }
    }
    """
    src = Path(docx_path).expanduser().resolve()
    if not src.exists():
        return {"ok": False, "issues": [f"文件不存在: {src}"], "warnings": [], "stats": {}}

    issues: list[str] = []
    warnings: list[str] = []
    stats = {
        "paragraph_count": 0,
        "heading_count": 0,
        "table_count": 0,
        "tracked_changes_count": 0,
        "comment_count": 0,
        "image_count": 0,
        "hyperlink_count": 0,
    }

    try:
        with zipfile.ZipFile(src, "r") as zf:
            names = set(zf.namelist())

            # 1. 必需文件检查
            for required in ("[Content_Types].xml", "word/document.xml", "_rels/.rels"):
                if required not in names:
                    issues.append(f"缺少必需文件: {required}")

            # 2. document.xml 结构扫描
            if "word/document.xml" in names:
                with zf.open("word/document.xml") as f:
                    try:
                        tree = ET.parse(f)
                        root = tree.getroot()
                    except ET.ParseError as e:
                        issues.append(f"document.xml 解析失败: {e}")
                        root = None

                    if root is not None:
                        # 段落
                        paragraphs = root.findall(f".//{_W}p")
                        stats["paragraph_count"] = len(paragraphs)

                        # 标题（有 pStyle 且值以 Heading 开头）
                        heading_count = 0
                        for p in paragraphs:
                            pStyle = p.find(f".//{_W}pStyle")
                            if pStyle is not None:
                                val = pStyle.get(f"{_W}val", "")
                                if val.startswith("Heading") or val.startswith("\u6807\u9898"):
                                    heading_count += 1
                        stats["heading_count"] = heading_count

                        stats["table_count"] = len(root.findall(f".//{_W}tbl"))
                        stats["tracked_changes_count"] = (
                            len(root.findall(f".//{_W}ins")) + len(root.findall(f".//{_W}del"))
                        )
                        stats["hyperlink_count"] = len(root.findall(f".//{_W}hyperlink"))

            # 3. 图片
            stats["image_count"] = sum(
                1 for n in names if n.startswith("word/media/")
            )

            # 4. 评论
            if "word/comments.xml" in names:
                with zf.open("word/comments.xml") as f:
                    try:
                        tree = ET.parse(f)
                        stats["comment_count"] = len(tree.getroot().findall(f".//{_W}comment"))
                    except ET.ParseError:
                        warnings.append("comments.xml 解析失败但不影响文档")

            # 5. 警告：修订没接受
            if stats["tracked_changes_count"] > 0:
                warnings.append(
                    f"文档还有 {stats['tracked_changes_count']} 个未决修订（tracked changes），"
                    "发版前建议 accept 或 reject"
                )

            # 6. 警告：评论残留
            if stats["comment_count"] > 0:
                warnings.append(
                    f"文档还有 {stats['comment_count']} 个评论/批注，对外发版前注意是否需要清理"
                )

    except zipfile.BadZipFile:
        issues.append("ZIP 格式损坏，不是有效的 .docx")
    except OSError as e:
        issues.append(f"读取失败: {e}")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "stats": stats,
    }


# ---------- tracked changes ----------

def iter_tracked_changes(docx_path: str | Path) -> list[dict[str, Any]]:
    """
    扫描文档里所有 <w:ins> 和 <w:del>。
    返回 [{type, author, date, text, preview_before, preview_after}, ...]
    """
    src = Path(docx_path).expanduser().resolve()
    changes: list[dict[str, Any]] = []

    with zipfile.ZipFile(src, "r") as zf:
        if "word/document.xml" not in zf.namelist():
            return changes
        with zf.open("word/document.xml") as f:
            tree = ET.parse(f)

    root = tree.getroot()

    # 插入：w:ins
    for ins in root.findall(f".//{_W}ins"):
        author = ins.get(f"{_W}author", "")
        date = ins.get(f"{_W}date", "")
        text = _collect_text(ins)
        changes.append({
            "type": "insert",
            "author": author,
            "date": date,
            "text": text[:300],
        })

    # 删除：w:del（被删除的文本用 <w:delText> 而非 <w:t>）
    for dele in root.findall(f".//{_W}del"):
        author = dele.get(f"{_W}author", "")
        date = dele.get(f"{_W}date", "")
        text = _collect_text(dele, del_text=True)
        changes.append({
            "type": "delete",
            "author": author,
            "date": date,
            "text": text[:300],
        })

    return changes


def _collect_text(element, *, del_text: bool = False) -> str:
    tag = f"{_W}delText" if del_text else f"{_W}t"
    parts = []
    for t in element.findall(f".//{tag}"):
        if t.text:
            parts.append(t.text)
    # 也收集 w:t 里的（del 里有时混有 w:t）
    if del_text:
        for t in element.findall(f".//{_W}t"):
            if t.text:
                parts.append(t.text)
    return "".join(parts)


def accept_all_changes(docx_path: str | Path, *, output_path: str | Path | None = None) -> Path:
    """
    接受文档里所有 tracked changes。
    - <w:ins>：保留其中的文本，去掉外层标记
    - <w:del>：整段删除（连同里面的 delText）
    """
    src = Path(docx_path).expanduser().resolve()
    out = Path(output_path).expanduser().resolve() if output_path else src

    with tempfile.TemporaryDirectory() as tmp:
        unpacked = unpack(src, tmp)
        doc_xml = unpacked / "word" / "document.xml"
        if doc_xml.exists():
            tree = ET.parse(doc_xml)
            _accept_changes_in_tree(tree.getroot())
            tree.write(doc_xml, xml_declaration=True, encoding="UTF-8", default_namespace=None)

        # 重打包
        out.parent.mkdir(parents=True, exist_ok=True)
        pack(unpacked, out)

    return out


def reject_all_changes(docx_path: str | Path, *, output_path: str | Path | None = None) -> Path:
    """
    拒绝文档里所有 tracked changes。
    - <w:ins>：删除插入的内容（整个 ins 节点连同内部 w:r/w:t 一起删）
    - <w:del>：保留被删内容（把 <w:delText> 转回 <w:t>，去掉外层 del 标记）
    """
    src = Path(docx_path).expanduser().resolve()
    out = Path(output_path).expanduser().resolve() if output_path else src

    with tempfile.TemporaryDirectory() as tmp:
        unpacked = unpack(src, tmp)
        doc_xml = unpacked / "word" / "document.xml"
        if doc_xml.exists():
            tree = ET.parse(doc_xml)
            _reject_changes_in_tree(tree.getroot())
            tree.write(doc_xml, xml_declaration=True, encoding="UTF-8", default_namespace=None)

        out.parent.mkdir(parents=True, exist_ok=True)
        pack(unpacked, out)

    return out


def _accept_changes_in_tree(root) -> None:
    """Accept 逻辑：ins 提子 / del 整除。"""
    # Python 的 ET 没有 parent 指针，要自己建映射
    parent_map = {c: p for p in root.iter() for c in p}

    # 1. 删所有 w:del
    for dele in list(root.iter(f"{_W}del")):
        parent = parent_map.get(dele)
        if parent is not None:
            parent.remove(dele)

    # 2. 把所有 w:ins 的子节点提到它父节点里（替换 ins 本身）
    for ins in list(root.iter(f"{_W}ins")):
        parent = parent_map.get(ins)
        if parent is None:
            continue
        idx = list(parent).index(ins)
        children = list(ins)
        parent.remove(ins)
        for offset, child in enumerate(children):
            parent.insert(idx + offset, child)


def _reject_changes_in_tree(root) -> None:
    """Reject 逻辑：ins 整除 / del 恢复。"""
    parent_map = {c: p for p in root.iter() for c in p}

    # 1. 删所有 w:ins
    for ins in list(root.iter(f"{_W}ins")):
        parent = parent_map.get(ins)
        if parent is not None:
            parent.remove(ins)

    # 2. del 里的 delText 转回 t，并把内容提到父节点
    for dele in list(root.iter(f"{_W}del")):
        # 把 delText → t
        for dt in dele.findall(f".//{_W}delText"):
            new_t = ET.SubElement(dt.getparent() if hasattr(dt, "getparent") else dele,
                                   f"{_W}t")
            new_t.text = dt.text
            # ET 没有 getparent 兜底：复用 parent_map
            dt_parent = parent_map.get(dt)
            if dt_parent is not None:
                idx = list(dt_parent).index(dt)
                dt_parent.remove(dt)
                dt_parent.insert(idx, new_t)

        # 把 del 的子节点提到父
        parent = parent_map.get(dele)
        if parent is None:
            continue
        idx = list(parent).index(dele)
        children = list(dele)
        parent.remove(dele)
        for offset, child in enumerate(children):
            parent.insert(idx + offset, child)
