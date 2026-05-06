from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable

from .blocks import DocumentBlock, ParsedDocument


def approx_tokens(text: str) -> int:
    return max(1, len(text or "") // 2)


@dataclass(frozen=True)
class ChunkingOptions:
    target_tokens: int = 500
    overlap_chars: int = 100
    keep_tables_atomic: bool = True
    split_on_headings: bool = True
    table_max_tokens: int = 800
    table_rows_per_chunk: int = 24
    boundary_keywords: tuple[str, ...] = ()
    profile_name: str = "custom"


PROFILES: dict[str, ChunkingOptions] = {
    "default": ChunkingOptions(profile_name="default"),
    "report": ChunkingOptions(
        target_tokens=650,
        overlap_chars=120,
        boundary_keywords=(
            "项目概况",
            "建设内容",
            "投资估算",
            "资金来源",
            "经济效益",
            "风险",
            "结论",
        ),
        profile_name="report",
    ),
    "policy": ChunkingOptions(
        target_tokens=420,
        overlap_chars=80,
        boundary_keywords=("第一条", "第二条", "第三条", "第四条", "第五条", "附则"),
        profile_name="policy",
    ),
    "meeting": ChunkingOptions(
        target_tokens=380,
        overlap_chars=60,
        boundary_keywords=("会议时间", "会议地点", "参会", "议题", "决议", "待办", "行动项"),
        profile_name="meeting",
    ),
    "table-heavy": ChunkingOptions(
        target_tokens=520,
        overlap_chars=40,
        keep_tables_atomic=False,
        table_max_tokens=360,
        table_rows_per_chunk=12,
        profile_name="table-heavy",
    ),
}


@dataclass(frozen=True)
class BlockChunk:
    rel_path: str
    chunk_idx: int
    text: str
    approx_tokens: int
    source_path: str
    page: int | None
    heading: str
    paragraph_idx: int
    start_char: int
    end_char: int
    content_hash: str
    block_types: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def chunk_document(
    document: ParsedDocument,
    *,
    target_tokens: int | None = None,
    overlap_chars: int | None = None,
    options: ChunkingOptions | None = None,
    profile: str = "default",
) -> list[BlockChunk]:
    opts = options or options_for_profile(
        profile,
        target_tokens=target_tokens,
        overlap_chars=overlap_chars,
    )
    return TemplateChunker(opts).chunk(document)


def options_for_profile(
    profile: str,
    *,
    target_tokens: int | None = None,
    overlap_chars: int | None = None,
) -> ChunkingOptions:
    base = PROFILES.get(profile, PROFILES["default"])
    return ChunkingOptions(
        target_tokens=target_tokens if target_tokens is not None else base.target_tokens,
        overlap_chars=overlap_chars if overlap_chars is not None else base.overlap_chars,
        keep_tables_atomic=base.keep_tables_atomic,
        split_on_headings=base.split_on_headings,
        table_max_tokens=base.table_max_tokens,
        table_rows_per_chunk=base.table_rows_per_chunk,
        boundary_keywords=base.boundary_keywords,
        profile_name=base.profile_name,
    )


class TemplateChunker:
    """Profile-driven chunker for common document templates.

    The first version is deterministic: it uses headings, block types, and
    template keywords rather than an LLM. That keeps indexing fast and
    repeatable while still giving reports, policies, meetings, and tables
    different chunk boundaries.
    """

    def __init__(self, options: ChunkingOptions | None = None):
        self.options = options or PROFILES["default"]
        self.table_chunker = TableAwareChunker(self.options)

    @classmethod
    def from_profile(cls, profile: str = "default") -> "TemplateChunker":
        return cls(options_for_profile(profile))

    def chunk(self, document: ParsedDocument) -> list[BlockChunk]:
        opts = self.options
        if not document.blocks:
            return []

        chunks: list[BlockChunk] = []
        buf: list[DocumentBlock] = []
        buf_tokens = 0

        def flush() -> None:
            nonlocal buf, buf_tokens
            if not buf:
                return
            chunks.append(_chunk_from_blocks(document, len(chunks), buf, opts))
            buf = []
            buf_tokens = 0

        for block in self._expand_tables(document.blocks):
            text = block.text.strip()
            if not text:
                continue
            block_tokens = approx_tokens(text)

            if self._is_boundary(block) and buf:
                flush()

            if block.type == "table" and block.metadata.get("table_split"):
                flush()
                chunks.append(_chunk_from_blocks(document, len(chunks), [block], opts))
                continue

            if block_tokens > opts.target_tokens * 2 and not (opts.keep_tables_atomic and block.type == "table"):
                flush()
                for sub_block in _split_long_block(block, opts.target_tokens):
                    chunks.append(_chunk_from_blocks(document, len(chunks), [sub_block], opts))
                continue

            if buf and buf_tokens + block_tokens > opts.target_tokens:
                flush()
            buf.append(block)
            buf_tokens += block_tokens

        flush()
        if opts.overlap_chars > 0 and len(chunks) > 1:
            return _with_overlap(chunks, opts.overlap_chars)
        return chunks

    def _expand_tables(self, blocks: list[DocumentBlock]) -> list[DocumentBlock]:
        out: list[DocumentBlock] = []
        for block in blocks:
            if block.type == "table":
                out.extend(self.table_chunker.split(block))
            else:
                out.append(block)
        return out

    def _is_boundary(self, block: DocumentBlock) -> bool:
        if self.options.split_on_headings and block.type == "heading":
            return True
        if not self.options.boundary_keywords:
            return False
        first_line = block.text.strip().splitlines()[0] if block.text.strip() else ""
        normalized = first_line.strip().lstrip("#").strip()
        return any(normalized.startswith(keyword) for keyword in self.options.boundary_keywords)


class TableAwareChunker:
    """Split oversized markdown tables while preserving their header rows."""

    def __init__(self, options: ChunkingOptions | None = None):
        self.options = options or PROFILES["default"]

    def split(self, block: DocumentBlock) -> list[DocumentBlock]:
        if block.type != "table":
            return [block]
        if self.options.keep_tables_atomic and approx_tokens(block.text) <= self.options.table_max_tokens:
            return [block]

        lines = [line.strip() for line in block.text.splitlines() if line.strip()]
        header_count = _table_header_count(lines)
        if len(lines) <= header_count + 1:
            return [block]

        header = lines[:header_count]
        rows = lines[header_count:]
        pieces: list[DocumentBlock] = []
        current: list[str] = []
        cursor = 0

        for row in rows:
            candidate = header + current + [row]
            if current and (
                len(current) >= self.options.table_rows_per_chunk
                or approx_tokens("\n".join(candidate)) > self.options.table_max_tokens
            ):
                pieces.append(self._piece(block, header, current, len(pieces), cursor))
                cursor = _advance_cursor(block.text, current[-1], cursor)
                current = [row]
            else:
                current.append(row)
        if current:
            pieces.append(self._piece(block, header, current, len(pieces), cursor))
        return pieces or [block]

    def _piece(
        self,
        block: DocumentBlock,
        header: list[str],
        rows: list[str],
        part_index: int,
        cursor: int,
    ) -> DocumentBlock:
        text = "\n".join(header + rows)
        first_row = rows[0] if rows else header[0]
        last_row = rows[-1] if rows else header[-1]
        rel_start = block.text.find(first_row, cursor)
        if rel_start < 0:
            rel_start = cursor
        rel_end = block.text.find(last_row, rel_start)
        if rel_end < 0:
            rel_end = rel_start + len(first_row)
        rel_end += len(last_row)
        metadata = dict(block.metadata)
        metadata.update({
            "table_split": True,
            "table_part": part_index,
            "table_header_repeated": True,
            "table_rows": len(rows),
        })
        return DocumentBlock(
            type="table",
            text=text,
            start_char=block.start_char + rel_start,
            end_char=block.start_char + rel_end,
            page=block.page,
            heading_path=list(block.heading_path),
            metadata=metadata,
        )


def _chunk_from_blocks(
    document: ParsedDocument,
    chunk_idx: int,
    blocks: list[DocumentBlock],
    opts: ChunkingOptions,
) -> BlockChunk:
    text = "\n\n".join(block.text.strip() for block in blocks if block.text.strip())
    first = blocks[0]
    last = blocks[-1]
    heading = ""
    for block in reversed(blocks):
        if block.heading:
            heading = block.heading
            break
    return BlockChunk(
        rel_path=document.rel_path,
        chunk_idx=chunk_idx,
        text=text,
        approx_tokens=approx_tokens(text),
        source_path=document.rel_path,
        page=first.page,
        heading=heading,
        paragraph_idx=int(first.metadata.get("paragraph_idx", chunk_idx)),
        start_char=first.start_char,
        end_char=last.end_char,
        content_hash=_hash_text(text),
        block_types=[block.type for block in blocks],
        metadata={
            "parser": document.parser,
            "title": document.title,
            "chunk_profile": opts.profile_name,
            "heading_path": list(last.heading_path),
            "block_types": [block.type for block in blocks],
            "contains_table": any(block.type == "table" for block in blocks),
            "contains_table_split": any(block.metadata.get("table_split") for block in blocks),
            "table_parts": [
                block.metadata.get("table_part")
                for block in blocks
                if block.metadata.get("table_split")
            ],
        },
    )


def _with_overlap(chunks: list[BlockChunk], overlap_chars: int) -> list[BlockChunk]:
    out: list[BlockChunk] = []
    for idx, chunk in enumerate(chunks):
        text = chunk.text
        if idx + 1 < len(chunks) and not _skip_overlap(chunk, chunks[idx + 1]):
            next_start = chunks[idx + 1].text[:overlap_chars]
            if next_start:
                text = chunk.text + "\n\n…(continued)…\n" + next_start
        out.append(
            BlockChunk(
                rel_path=chunk.rel_path,
                chunk_idx=idx,
                text=text,
                approx_tokens=approx_tokens(text),
                source_path=chunk.source_path,
                page=chunk.page,
                heading=chunk.heading,
                paragraph_idx=chunk.paragraph_idx,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
                content_hash=_hash_text(text),
                block_types=list(chunk.block_types),
                metadata=dict(chunk.metadata),
            )
        )
    return out


def _skip_overlap(left: BlockChunk, right: BlockChunk) -> bool:
    return bool(left.metadata.get("contains_table") or right.metadata.get("contains_table"))


def _table_header_count(lines: list[str]) -> int:
    if len(lines) >= 2 and _is_table_separator(lines[1]):
        return 2
    return 1


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(cell and set(cell) <= {"-", ":"} for cell in cells)


def _advance_cursor(text: str, needle: str, cursor: int) -> int:
    pos = text.find(needle, cursor)
    if pos < 0:
        return cursor
    return pos + len(needle)


def _split_long_block(block: DocumentBlock, target_tokens: int) -> Iterable[DocumentBlock]:
    sentinels = ["。", "！", "？", ".", "!", "?", "\n"]
    current: list[str] = []
    current_weight = 0
    cursor = 0
    for char in block.text:
        current.append(char)
        current_weight += 1 if not char.isspace() else 0
        if char in sentinels and current_weight >= target_tokens * 2:
            piece = "".join(current).strip()
            if piece:
                rel_start = block.text.find(piece, cursor)
                if rel_start < 0:
                    rel_start = cursor
                rel_end = rel_start + len(piece)
                cursor = rel_end
                yield _copy_block_with_span(block, piece, rel_start, rel_end)
            current = []
            current_weight = 0
    tail = "".join(current).strip()
    if tail:
        rel_start = block.text.find(tail, cursor)
        if rel_start < 0:
            rel_start = cursor
        yield _copy_block_with_span(block, tail, rel_start, rel_start + len(tail))


def _copy_block_with_span(block: DocumentBlock, text: str, rel_start: int, rel_end: int) -> DocumentBlock:
    return DocumentBlock(
        type=block.type,
        text=text,
        start_char=block.start_char + rel_start,
        end_char=block.start_char + rel_end,
        page=block.page,
        heading_path=list(block.heading_path),
        metadata=dict(block.metadata),
    )


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
