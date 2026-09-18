"""Chunks: small runs of blocks inside one section, the unit search matches against
(brief §5.4).

Four rules, in priority order:

1. A `table` block is glued to the section's heading block when it is the first content
   under it; otherwise it starts its own chunk. Annotations and boxed text that follow it
   stay in that chunk (rule 2); the next block of any other kind starts a new chunk. A
   table at or under `CHUNK_MAX` is never split. Above it, a pipe table is cut only
   between rows: every piece re-opens with the header row and its delimiter row, carries
   the previous piece's last row as one row of overlap, keeps the table's block ordinal,
   and holds at least one new row (so a single row longer than `CHUNK_MAX` stays whole).
   A `table` block with no pipe rows has no row boundaries and splits at blank lines like
   a paragraph (rule 3). Why: the embedding model reads a bounded prefix of a chunk
   (`embed.py`), so a chunk longer than that is ranked on its head alone; the row is the
   unit the GFM table format guarantees, and the repeated header keeps every piece
   readable as a table. Section fetch still returns the whole table; only finding is at
   stake.
2. `annotation` and `boxed_text` blocks never start a new chunk; they stay with the block
   before them even if that pushes the chunk past `CHUNK_TARGET`.
3. Only `paragraph` or `list` blocks longer than `CHUNK_MAX` may be split, at blank lines;
   a piece keeps the same block ordinal for `block_first`/`block_last`.
4. Every chunk records `block_first`, `block_last`, `page_first`, `page_last`,
   `section_index`, and `text` = block texts joined with `\n\n`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

from .kbfiles import Block
from .sections import Section

CHUNK_TARGET = 1200
CHUNK_MAX = 2500

_BLANK_LINE_RE = re.compile(r"\n\s*\n")
#: GFM delimiter row: cells of one or more hyphens, optional alignment colons.
_DELIMITER_ROW_RE = re.compile(r"^\s*\|?(\s*:?-+:?\s*\|)*\s*:?-+:?\s*\|?\s*$")


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


def _split_table_rows(text: str, max_chars: int) -> Optional[list[str]]:
    """Cut a pipe table between rows (rule 1). Returns None when `text` is not a pipe
    table (no header row followed by a delimiter row), so the caller can fall back to
    blank-line splitting. A line that does not start with `|` continues the row before it
    and is never a cut point. Each piece = header + delimiter + [overlap row] + rows,
    filled greedily to `max_chars`; a piece always takes at least one new row."""
    lines = text.split("\n")
    if len(lines) < 3 or not lines[0].lstrip().startswith("|"):
        return None
    if not _DELIMITER_ROW_RE.match(lines[1]):
        return None
    header = f"{lines[0]}\n{lines[1]}"
    rows: list[str] = []
    for line in lines[2:]:
        if rows and not line.lstrip().startswith("|"):
            rows[-1] += "\n" + line
        else:
            rows.append(line)
    if not rows:
        return [text]
    pieces: list[str] = []
    overlap: Optional[str] = None
    i = 0
    while i < len(rows):
        cur = header if overlap is None else f"{header}\n{overlap}"
        cur = f"{cur}\n{rows[i]}"
        i += 1
        while i < len(rows) and len(cur) + 1 + len(rows[i]) <= max_chars:
            cur = f"{cur}\n{rows[i]}"
            i += 1
        pieces.append(cur)
        overlap = rows[i - 1]
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
            pieces = [block.text]
            if len(block.text) > max_chars:
                pieces = _split_table_rows(block.text, max_chars) or _split_at_blank_lines(
                    block.text, max_chars
                )
            # Every piece keeps the table's ordinal; only the last stays open so that the
            # annotations and boxed text after the table glue to it (rule 2).
            for piece in pieces[:-1]:
                cur.append(replace(block, text=piece))
                flush()
            cur.append(replace(block, text=pieces[-1]))
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
