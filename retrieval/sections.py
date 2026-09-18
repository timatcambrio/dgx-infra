"""Sections: a heading block plus everything under it (brief §5.4).

`build_sections(doc)` walks `doc.blocks` in order and groups them under the nearest
heading of level 1-3. `####` and deeper stay inside the enclosing section. A document with
content before its first heading gets a genuine section 0 with `heading=None`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .kbfiles import Block, Document

_HASHES_RE = re.compile(r"^#{1,6}\s*")


@dataclass
class Section:
    index: int
    level: int
    heading: Optional[str]
    heading_path: str
    block_first: int
    block_last: int
    page_first: Optional[int]
    page_last: Optional[int]
    blocks: list[Block]


def _heading_text(block: Block) -> str:
    first_line = block.text.splitlines()[0] if block.text else ""
    return _HASHES_RE.sub("", first_line).strip()


def build_sections(doc: Document) -> list[Section]:
    title = doc.title

    # Pass 1: group blocks into raw (level, heading_text|None, blocks) runs.
    raw: list[tuple[int, Optional[str], list[Block]]] = []
    cur_level = 0
    cur_heading: Optional[str] = None
    cur_blocks: list[Block] = []

    def flush() -> None:
        if cur_blocks:
            raw.append((cur_level, cur_heading, list(cur_blocks)))

    for block in doc.blocks:
        level = block.level if block.level is not None else None
        if block.kind == "heading" and level is not None and level <= 3:
            flush()
            cur_level = level
            cur_heading = _heading_text(block)
            cur_blocks = [block]
        else:
            cur_blocks.append(block)
    flush()

    # Pass 2: heading_path from a per-level stack (levels 1-3).
    sections: list[Section] = []
    stack: dict[int, str] = {}
    for idx, (level, heading, blocks) in enumerate(raw):
        if heading is None:
            heading_path = title
        else:
            stack[level] = heading
            for lvl in [l for l in stack if l > level]:
                del stack[lvl]
            heading_path = " › ".join([title] + [stack[l] for l in sorted(stack)])

        pages = [b.page for b in blocks if b.page is not None]
        sections.append(
            Section(
                index=idx,
                level=level,
                heading=heading,
                heading_path=heading_path,
                block_first=blocks[0].ordinal,
                block_last=blocks[-1].ordinal,
                page_first=min(pages) if pages else None,
                page_last=max(pages) if pages else None,
                blocks=blocks,
            )
        )
    return sections
