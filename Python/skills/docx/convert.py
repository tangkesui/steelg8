"""
docx 格式转换：用 LibreOffice headless 把 .doc/.rtf/.odt 转成 .docx

**依赖**：系统需装 LibreOffice 4.x+（macOS: brew install --cask libreoffice；
Linux: apt install libreoffice-core；Windows: 官网下载）。

查找 soffice 可执行文件的顺序：
  1. 环境变量 STEELG8_SOFFICE_PATH（用户手动指定）
  2. PATH 里的 `soffice`
  3. macOS 常见路径 /Applications/LibreOffice.app/Contents/MacOS/soffice
  4. Linux 常见路径 /usr/bin/soffice / /usr/local/bin/soffice
找不到就返回清晰的错误 + 安装指引。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class DocxConvertError(RuntimeError):
    pass


_SUPPORTED_IN = {".doc", ".rtf", ".odt", ".wps", ".docx", ".html", ".htm", ".txt", ".md"}


def _find_soffice() -> str | None:
    """按优先级找 LibreOffice 的 soffice 可执行文件。"""
    # 1. 环境变量
    env_path = os.environ.get("STEELG8_SOFFICE_PATH")
    if env_path and Path(env_path).is_file() and os.access(env_path, os.X_OK):
        return env_path

    # 2. PATH
    found = shutil.which("soffice")
    if found:
        return found

    # 3. 常见安装位置
    candidates = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/opt/homebrew/bin/soffice",
        "/snap/bin/libreoffice",
    ]
    for c in candidates:
        if Path(c).is_file() and os.access(c, os.X_OK):
            return c
    return None


def _install_hint() -> str:
    import platform
    sys_name = platform.system()
    if sys_name == "Darwin":
        return "请安装 LibreOffice：`brew install --cask libreoffice` 或 https://www.libreoffice.org/download/"
    if sys_name == "Linux":
        return "请安装 LibreOffice：`sudo apt install libreoffice-core`（Debian/Ubuntu）或 `sudo dnf install libreoffice`（Fedora）"
    return "请从 https://www.libreoffice.org/download/ 下载安装 LibreOffice"


def check_available() -> dict[str, Any]:
    """探测 LibreOffice 是否可用，返回状态 + 版本信息（不产生副作用）。"""
    soffice = _find_soffice()
    if not soffice:
        return {
            "available": False,
            "hint": _install_hint(),
        }
    try:
        result = subprocess.run(
            [soffice, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return {
            "available": True,
            "path": soffice,
            "version": (result.stdout or "").strip(),
        }
    except (subprocess.TimeoutExpired, OSError):
        return {
            "available": False,
            "path": soffice,
            "hint": "soffice 被找到但无法执行 --version，可能是安装损坏",
        }


def convert_to_docx(
    source_path: str,
    *,
    output_dir: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """
    把 .doc / .rtf / .odt / .html 等转成 .docx。

    output_dir：不传时用源文件同目录。
    返回 {output_path, input_format, converted: bool, stderr: str}
    """
    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise DocxConvertError(f"文件不存在：{src}")

    ext = src.suffix.lower()
    if ext not in _SUPPORTED_IN:
        raise DocxConvertError(f"不支持的输入格式：{ext}。支持：{sorted(_SUPPORTED_IN)}")

    # 已经是 docx 直接复制（偶尔有用户传错的）
    if ext == ".docx":
        return {
            "output_path": str(src),
            "input_format": ext,
            "converted": False,
            "note": "已经是 .docx，直接返回",
        }

    soffice = _find_soffice()
    if not soffice:
        raise DocxConvertError(f"找不到 LibreOffice (soffice)。{_install_hint()}")

    out_dir = Path(output_dir).expanduser().resolve() if output_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # soffice 命令
    cmd = [
        soffice,
        "--headless",
        "--convert-to", "docx:MS Word 2007 XML",
        "--outdir", str(out_dir),
        str(src),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DocxConvertError(f"LibreOffice 转换超时 ({timeout}s)") from exc

    if result.returncode != 0:
        raise DocxConvertError(
            f"LibreOffice 转换失败 (exit={result.returncode})：{result.stderr[:500]}"
        )

    # 输出文件名 = 原文件 stem + .docx
    expected_out = out_dir / f"{src.stem}.docx"
    if not expected_out.exists():
        raise DocxConvertError(
            f"转换完成但找不到输出文件：{expected_out}\nstderr: {result.stderr[:200]}"
        )

    return {
        "output_path": str(expected_out),
        "input_format": ext,
        "converted": True,
        "soffice": soffice,
    }
