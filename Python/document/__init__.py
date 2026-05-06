"""Structured document parsing and chunking primitives for steelg8 RAG."""

from .blocks import DocumentBlock, ParsedDocument
from .chunkers import (
    BlockChunk,
    ChunkingOptions,
    TableAwareChunker,
    TemplateChunker,
    chunk_document,
    options_for_profile,
)
from .registry import parse_file, parse_text

__all__ = [
    "BlockChunk",
    "ChunkingOptions",
    "DocumentBlock",
    "ParsedDocument",
    "TableAwareChunker",
    "TemplateChunker",
    "chunk_document",
    "options_for_profile",
    "parse_file",
    "parse_text",
]
