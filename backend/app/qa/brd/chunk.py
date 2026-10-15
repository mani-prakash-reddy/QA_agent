"""Section-aware chunking: each chunk knows its section path and pages, and never splits a table row."""

import re
from dataclasses import dataclass

from app.qa.brd.parse import Block, ParsedDoc

MAX_CHARS = 1800


@dataclass
class Chunk:
    ordinal: int
    section_path: str
    page_from: int | None
    page_to: int | None
    text: str
    kind: str  # text | table | list


def _split_table(text: str) -> list[str]:
    lines = text.split("\n")
    header, rows = lines[:2], lines[2:]
    parts, cur = [], list(header)
    for r in rows:
        if sum(len(x) for x in cur) + len(r) > MAX_CHARS and len(cur) > 2:
            parts.append("\n".join(cur))
            cur = list(header)
        cur.append(r)
    parts.append("\n".join(cur))
    return parts


def _split_text(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    parts, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > MAX_CHARS:
            parts.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        parts.append(cur)
    return parts


def chunk_document(doc: ParsedDoc) -> list[Chunk]:
    stack: list[tuple[int, str]] = []
    chunks: list[Chunk] = []
    buf: list[Block] = []

    def section() -> str:
        return " > ".join(t for _, t in stack)

    def flush() -> None:
        if not buf:
            return
        pages = [b.page for b in buf if b.page is not None]
        kinds = {b.kind for b in buf}
        text = "\n\n".join(b.text for b in buf)
        kind = (
            "table" if kinds == {"table"} else "list" if kinds == {"list"} else "text"
        )
        chunks.append(
            Chunk(
                len(chunks),
                section(),
                min(pages) if pages else None,
                max(pages) if pages else None,
                text,
                kind,
            )
        )
        buf.clear()

    def size() -> int:
        return sum(len(b.text) for b in buf)

    for block in doc.blocks:
        if block.kind == "heading":
            flush()
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            stack.append((block.level, block.text))
            continue
        pieces = (
            _split_table(block.text)
            if block.kind == "table"
            else _split_text(block.text)
        )
        for piece in pieces:
            b = Block(block.kind, piece, page=block.page)
            if size() + len(piece) > MAX_CHARS or (block.kind == "table" and buf):
                flush()
            buf.append(b)
            if block.kind == "table":
                flush()
    flush()
    return chunks
