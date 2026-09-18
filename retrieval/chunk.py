"""Chunks: small runs of blocks inside one section, the unit search matches against
(brief §5.4).

Four rules, in priority order:

1. A `table` block is never split. It is glued to the section's heading block when it is
   the first content under it; otherwise it starts its own chunk. Annotations and boxed
   text that follow it stay in that chunk (rule 2); the next block of any other kind
   starts a new chunk. An oversized table is one oversized chunk.
2. `annotation` and `boxed_text` blocks never start a new chunk; they stay with the block
   before them even if that pushes the chunk past `CHUNK_TARGET`.
3. Only `paragraph` or `list` blocks longer than `CHUNK_MAX` may be split, at blank lines;
   a piece keeps the same block ordinal for `block_first`/`block_last`.
4. Every chunk records `block_first`, `block_last`, `page_first`, `page_last`,
   `section_index`, and `text` = block texts joined with `\n\n`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .kbfiles import Block
from .sections import Section

CHUNK_TARGET = 1200
CHUNK_MAX = 2500

_BLANK_LINE_RE = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    section_index: int
    block_first: int
    block_last: int
    page_first: Optional[int]
    page_last: Optional[int]
    text: str


def _split_at_blank_lines(text: str, max_chars: int) -> list[str]:
    """Split `text` into pieces at blank lines, greedily filling each piece up to
    `max_chars`. A single paragraph with no blank line to split at stays whole even if it
    still exceeds `max_chars` — the brief authorises splitting only at blank lines."""
    paras = [p for p in _BLANK_LINE_RE.split(text) if p.strip()]
    if not paras:
        return [text]
    pieces: list[str] = []
    cur = ""
    for p in paras:
        candidate = f"{cur}\n\n{p}" if cur else p
        if cur and len(candidate) > max_chars:
            pieces.append(cur)
            cur = p
        else:
            cur = candidate
    if cur:
        pieces.append(cur)
    return pieces


def _make_chunk(section: Section, blocks: list[Block]) -> Chunk:
    pages = [b.page for b in blocks if b.page is not None]
    return Chunk(
        section_index=section.index,
        block_first=blocks[0].ordinal,
        block_last=blocks[-1].ordinal,
        page_first=min(pages) if pages else None,
        page_last=max(pages) if pages else None,
        text="\n\n".join(b.text for b in blocks),
    )


def _make_piece_chunk(section: Section, block: Block, text: str) -> Chunk:
    return Chunk(
        section_index=section.index,
        block_first=block.ordinal,
        block_last=block.ordinal,
        page_first=block.page,
        page_last=block.page,
        text=text,
    )


def _chunk_size(blocks: list[Block]) -> int:
    if not blocks:
        return 0
    return sum(len(b.text) for b in blocks) + 2 * (len(blocks) - 1)


def _chunk_section(section: Section, target: int, max_chars: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur: list[Block] = []

    def flush() -> None:
        nonlocal cur
        if cur:
            chunks.append(_make_chunk(section, cur))
            cur = []

    after_table = False
    for block in section.blocks:
        glued = block.kind in ("annotation", "boxed_text")

        # A table closes its chunk, but only once something other than an annotation or
        # boxed note follows it: on a form, the notes beside a table are the evidence a
        # reader needs to make sense of the table, and they must travel with it.
        if after_table and not glued:
            flush()
            after_table = False

        if block.kind == "table":
            if cur and not (len(cur) == 1 and cur[0].kind == "heading"):
                flush()
            cur.append(block)
            after_table = True
            continue

        if not glued and cur and _chunk_size(cur) + len(block.text) > target:
            flush()

        if len(block.text) > max_chars and block.kind in ("paragraph", "list"):
            flush()
            for piece in _split_at_blank_lines(block.text, max_chars):
                chunks.append(_make_piece_chunk(section, block, piece))
            continue

        cur.append(block)

    flush()
    return chunks


def build_chunks(
    sections: list[Section], target: int = CHUNK_TARGET, max_chars: int = CHUNK_MAX
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in sections:
        chunks.extend(_chunk_section(section, target, max_chars))
    return chunks
