"""
项目文件抽取与切块
--------------------

Phase 2 Step 1：只处理 .md / .txt。docx/pdf 留给 Step 2。

切块策略：
- 先按段落（连续空行）粗分
- 按目标 token 数聚合：默认 500（粗估 1 token ≈ 2 中文字 / 4 英文字符）
- 段落本身超长的，再按句号 / 换行二次切
- 相邻 chunk 之间留约 100 字符重叠，减少"信息落在边界被切断"

输出一个 Chunk dataclass 列表，供 embedding 和入库。

关键设计：只靠 stdlib。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


# ---- 配置常量 ----

# 默认允许的扩展名 Step 1 只开 md/txt
SUPPORTED_EXT: set[str] = {".md", ".markdown", ".txt", ".mdx"}

# 目录黑名单（绝对跳过）
SKIP_DIRS: set[str] = {
    ".git", ".svn", ".hg",
    "node_modules", "__pycache__", ".venv", "venv", ".env",
    ".build", ".cache", ".home", ".local-test",
    ".idea", ".vscode", ".claude",
    "dist", "build", ".next", ".nuxt",
    ".steelg8",
}

# 文件大小上限（字节）：超过视为不是常规文本
MAX_FILE_BYTES = 500_000

# 粗估 token 数：每个字约 0.5 token
def _approx_tokens(s: str) -> int:
    return max(1, len(s) // 2)


@dataclass(frozen=True)
class FileRef:
    abs_path: str
    rel_path: str           # 相对 project root
    size: int
    mtime: float


@dataclass
class Chunk:
    rel_path: str
    chunk_idx: int
    text: str
    approx_tokens: int = field(default=0)

    def __post_init__(self) -> None:
        if self.approx_tokens == 0:
            object.__setattr__(self, "approx_tokens", _approx_tokens(self.text))


# ---- 公开 API ----


def walk_project(root: str) -> Iterator[FileRef]:
    """按 SUPPORTED_EXT 过滤 + SKIP_DIRS 黑名单遍历。按 rel_path 自然排序。"""
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        return

    results: list[FileRef] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        # in-place 修改 dirnames 让 os.walk 不递归黑名单目录
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext not in SUPPORTED_EXT:
                continue
            abs_path = Path(dirpath) / fname
            try:
                stat = abs_path.stat()
            except OSError:
                continue
            if stat.st_size > MAX_FILE_BYTES:
                continue
            if stat.st_size == 0:
                continue
            try:
                rel = abs_path.relative_to(root_path)
            except ValueError:
                continue
            results.append(
                FileRef(
                    abs_path=str(abs_path),
                    rel_path=str(rel),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                )
            )

    results.sort(key=lambda r: r.rel_path)
    yield from results


def read_text(abs_path: str) -> str:
    """按 utf-8 读文本，失败回退 latin-1。"""
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(abs_path, "r", encoding="latin-1") as f:
            return f.read()


def chunk_text(
    text: str,
    rel_path: str,
    *,
    target_tokens: int = 500,
    overlap_chars: int = 100,
) -> list[Chunk]:
    """把单个文件的全文切成 Chunk 列表。"""
    if not text.strip():
        return []

    # 先按"两个及以上换行"拆成段
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0

    def flush() -> None:
        nonlocal buf, buf_tokens
        if not buf:
            return
        body = "\n\n".join(buf).strip()
        if body:
            chunks.append(Chunk(rel_path=rel_path, chunk_idx=len(chunks), text=body))
        buf = []
        buf_tokens = 0

    for para in paragraphs:
        para_tokens = _approx_tokens(para)

        # 段落自身超长 → 按句号拆
        if para_tokens > target_tokens * 2:
            flush()
            for sub in _split_long_paragraph(para, target_tokens):
                chunks.append(Chunk(rel_path=rel_path, chunk_idx=len(chunks), text=sub))
            continue

        if buf_tokens + para_tokens > target_tokens and buf:
            flush()
        buf.append(para)
        buf_tokens += para_tokens

    flush()

    # 添加重叠：在每一块末尾接一段下一块的前缀
    if overlap_chars > 0 and len(chunks) > 1:
        overlapped: list[Chunk] = []
        for i, c in enumerate(chunks):
            text_with_overlap = c.text
            if i + 1 < len(chunks):
                next_start = chunks[i + 1].text[:overlap_chars]
                if next_start:
                    text_with_overlap = c.text + "\n\n…(continued)…\n" + next_start
            overlapped.append(Chunk(rel_path=c.rel_path, chunk_idx=i, text=text_with_overlap))
        return overlapped

    return chunks


def _split_long_paragraph(para: str, target_tokens: int) -> Iterable[str]:
    """按中英文句尾分隔符切长段落。"""
    sentinels = ["。", "！", "？", ".", "!", "?", "\n"]
    # 简单按字符扫描，在 sentinel 后断开
    pieces: list[str] = []
    current = []
    cur_tokens = 0
    for ch in para:
        current.append(ch)
        cur_tokens += 1 if not ch.isspace() else 0
        if ch in sentinels and cur_tokens >= target_tokens * 2:
            pieces.append("".join(current).strip())
            current = []
            cur_tokens = 0
    if current:
        tail = "".join(current).strip()
        if tail:
            pieces.append(tail)
    return pieces


# ---- 一把梭便捷函数 ----


def extract_and_chunk(root: str, *, target_tokens: int = 500) -> list[Chunk]:
    """遍历 root 下所有支持的文件，切成 chunk 列表。"""
    out: list[Chunk] = []
    for fref in walk_project(root):
        text = read_text(fref.abs_path)
        chunks = chunk_text(text, fref.rel_path, target_tokens=target_tokens)
        out.extend(chunks)
    return out
