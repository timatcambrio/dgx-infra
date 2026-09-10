"""Format-specific converters. Add no converters beyond the ones prescribed here."""

from __future__ import annotations

import re

_BLANK_RUN = re.compile(r"\n{3,}")


def _is_table_row(line: str) -> bool:
    return line.lstrip().startswith("|")


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
        ):
            lines.append("")
        lines.append(line)
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip("\n")
