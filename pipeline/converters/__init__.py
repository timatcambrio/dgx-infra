"""Format-specific converters. Add no converters beyond the ones prescribed here."""

from __future__ import annotations

import re
from typing import Any

_BLANK_RUN = re.compile(r"\n{3,}")


def _is_table_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def _is_block_anchor(line: str) -> bool:
    return line.strip().startswith("<!-- dgx:block=")


def normalize_markdown(text: str) -> str:
    """Tidy converter output into markdown that parsers actually accept.

    Two fixes, both for real defects seen in Docling's output rather than for taste:

    * **A blank line before a table.** Docling emits a table immediately after a bullet
      list, and GFM will not recognise a table that starts on the line after list content —
      it renders as literal pipe characters, which then chunk and embed as noise.
    * **Runs of blank lines collapsed to one.** Cosmetic, but it keeps output stable across
      Docling versions that differ only in vertical whitespace, so a golden diff means
      something changed.

    Applied to every converter's output so all formats produce markdown of the same shape.
    """
    lines: list[str] = []
    for line in text.split("\n"):
        if (
            _is_table_row(line)
            and lines
            and lines[-1].strip()
            and not _is_table_row(lines[-1])
            and not _is_block_anchor(lines[-1])
        ):
            lines.append("")
        lines.append(line)
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip("\n")


def render_block_provenance(
    blocks: list[tuple[str, str, str]], slug: str
) -> tuple[str, list[dict[str, Any]]]:
    """Anchor a flat block list and build its sidecar records. Shared by DOCX/DOC and CSV.

    Neither format has a page concept, so every record gets `page: null` and the block's
    position in the list is the only locator -- ids are `<slug>:p000:b<NNN>`, `NNN` starting
    at 1 and zero-padded to 3 (4 once a document has 1000+ blocks). Mirrors the anchor and id
    conventions in `pdf._render_blocks` (which also count from 1) for the one thing PDF has that these formats do not:
    pages.
    """
    width = 4 if len(blocks) >= 1000 else 3
    parts: list[str] = []
    provenance: list[dict[str, Any]] = []
    for index, (text, kind, confidence) in enumerate(blocks, start=1):
        block_id = f"{slug}:p000:b{index:0{width}d}"
        parts.append(f"<!-- dgx:block={block_id} -->\n{text}")
        provenance.append(
            {
                "block_id": block_id,
                "page": None,
                "kind": kind,
                "confidence": confidence,
            }
        )
    return "\n\n".join(parts), provenance
