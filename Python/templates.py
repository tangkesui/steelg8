"""
模板库：统一管理 ~/Documents/steelg8/templates/ 下的 .docx / .xlsx / .pptx 模板。

暴露：
  default_dir() → Path      默认目录（没有就按需创建）
  list() → list[Info]       列出所有模板，带占位符预览
  info(path) → Info          单个模板详情（占位符 / 大小 / mtime）
  delete(path) → bool        物理删除（仅允许 default_dir 下）

为什么定在 ~/Documents/steelg8/templates/：
- 用户在 Finder 里直接看得到，可以手拖 .docx 进去
- iCloud Drive 开启 Documents 同步时会自动跨设备
- 不藏在 ~/.steelg8 里那种"半隐形"位置
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


SUPPORTED_EXT = {".docx", ".xlsx", ".pptx"}


def default_dir() -> Path:
    # 优先级：env var > preferences.json > hardcoded default
    env = os.environ.get("STEELG8_TEMPLATES_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
    else:
        try:
            import preferences
            p = Path(preferences.get("templates_dir"))
        except Exception:
            p = Path.home() / "Documents" / "steelg8" / "templates"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class TemplateInfo:
    name: str            # 文件名（不含目录）
    path: str            # 绝对路径
    ext: str
    size: int
    mtime: float
    placeholders: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _list_placeholders(path: Path) -> list[str]:
    """根据扩展名读占位符。.docx 用 docx_fill.list_placeholders；xlsx/pptx 暂时返回空。"""
    if path.suffix.lower() != ".docx":
        return []
    try:
        from skills import docx_fill
        return docx_fill.list_placeholders(str(path))
    except Exception:
        return []


def info(path: str) -> TemplateInfo | None:
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        return None
    stat = p.stat()
    return TemplateInfo(
        name=p.name,
        path=str(p),
        ext=p.suffix.lower(),
        size=stat.st_size,
        mtime=stat.st_mtime,
        placeholders=_list_placeholders(p),
    )


def list_all() -> list[TemplateInfo]:
    root = default_dir()
    out: list[TemplateInfo] = []
    for child in sorted(root.iterdir()):
        # 跳 ~$ 锁文件、点文件、子目录
        if child.name.startswith("~$") or child.name.startswith("."):
            continue
        if child.is_dir():
            continue
        if child.suffix.lower() not in SUPPORTED_EXT:
            continue
        i = info(str(child))
        if i:
            out.append(i)
    return out


def delete(path: str) -> bool:
    """安全删除：只允许删 default_dir 下的 SUPPORTED_EXT 文件。"""
    p = Path(path).expanduser().resolve()
    root = default_dir().resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return False
    if p.suffix.lower() not in SUPPORTED_EXT:
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False


def add(src_path: str) -> TemplateInfo | None:
    """把外部 .docx/.xlsx/.pptx 拷贝进模板库。返回拷贝后的 Info。"""
    import shutil
    src = Path(src_path).expanduser().resolve()
    if not src.exists() or src.suffix.lower() not in SUPPORTED_EXT:
        return None
    dst = default_dir() / src.name
    # 重名加 -2 -3 后缀
    if dst.exists():
        stem, ext = src.stem, src.suffix
        idx = 2
        while (default_dir() / f"{stem}-{idx}{ext}").exists():
            idx += 1
        dst = default_dir() / f"{stem}-{idx}{ext}"
    try:
        shutil.copy2(src, dst)
    except OSError:
        return None
    return info(str(dst))
