from __future__ import annotations

from .blocks import ParsedDocument
from .parsers import DEFAULT_MAX_CHARS
from . import parsers


def parse_file(abs_path: str, *, rel_path: str | None = None, max_chars: int = DEFAULT_MAX_CHARS) -> ParsedDocument:
    return parsers.parse_file(abs_path, rel_path=rel_path, max_chars=max_chars)


def parse_text(text: str, *, rel_path: str, parser: str = "text") -> ParsedDocument:
    return parsers.parse_text(text, rel_path=rel_path, parser=parser)
