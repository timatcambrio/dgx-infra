"""Sections: a heading block plus everything under it.

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
    #: run index -> the headings it swallowed, in order, as (level, text).
    subsumed: dict[int, list[tuple[int, str]]] = {}
    cur_level = 0
    cur_heading: Optional[str] = None
    cur_blocks: list[Block] = []

    def flush() -> None:
        if cur_blocks:
            raw.append((cur_level, cur_heading, list(cur_blocks)))

    # A heading that introduces nothing does not open a section. A caption whose body is
    # empty and whose data is titled by the next heading puts the searchable words in one
    # section and the answer in the next; this keeps them together without moving a single
    # character of text. The subsumed heading stays in the section's blocks, so its words
    # are still indexed, still searchable and still rendered on fetch.
    heading_only = False
    for block in doc.blocks:
        level = block.level if block.level is not None else None
        if block.kind == "heading" and level is not None and level <= 3:
            if cur_blocks and heading_only:
                subsumed.setdefault(len(raw), []).append((level, _heading_text(block)))
                cur_blocks.append(block)
                continue
            flush()
            cur_level = level
            cur_heading = _heading_text(block)
            cur_blocks = [block]
            heading_only = True
        else:
            cur_blocks.append(block)
            heading_only = False
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
            # A swallowed heading deepens the path when it is a genuine child (`4` then
            # `4.1`), and is left in the body when it is a sibling -- a form's field
            # labels are all set the same size, and letting one of those name the section
            # would restore exactly the heading this is meant to remove.
            for sub_level, sub_heading in subsumed.get(idx, []):
                if sub_level > level:
                    stack[sub_level] = sub_heading
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
