from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DocumentBlock:
    """A structural unit extracted from a source document.

    Blocks are intentionally richer than plain paragraphs. Chunkers can use
    this metadata to avoid splitting tables, keep heading context, and build
    traceable citations.
    """

    type: str
    text: str
    start_char: int
    end_char: int
    page: int | None = None
    heading_path: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def heading(self) -> str:
        return self.heading_path[-1] if self.heading_path else ""


@dataclass(frozen=True)
class ParsedDocument:
    rel_path: str
    title: str
    blocks: list[DocumentBlock]
    parser: str
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        """Serialize blocks to the legacy text shape used by old callers."""
        parts: list[str] = []
        last_page: int | None = None
        for block in self.blocks:
            if block.page is not None and block.page != last_page:
                parts.append(f"<!-- page {block.page} -->")
                last_page = block.page
            if block.text.strip():
                parts.append(block.text.strip())
        return "\n\n".join(parts)


def fallback_title(rel_path: str) -> str:
    return Path(rel_path).stem or rel_path
