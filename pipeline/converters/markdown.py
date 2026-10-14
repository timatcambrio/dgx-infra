"""Markdown sources, kept verbatim.

Markdown is already the output format, so nothing is rebuilt: the source is cut into
top-level blocks and each block is copied line for line. The parser (markdown-it-py, MIT,
already installed through typer's `rich`) is used only to find where blocks start; the text
of every block is a slice of the source, never the parser's rendering of it.

Blocks run from one top-level block's first line to the next one's, so lines the parser
produces no token for (link reference definitions) stay with the block before them instead
of falling between the cracks. Only blank lines between blocks are dropped: the anchors are
joined with one blank line, and between blocks a blank line carries no meaning.

Exactly two things are changed, both recorded:

* A source front matter block becomes a fenced `yaml` block at the top of the body. A second
  `---` block would otherwise render as a heading and could be read as our own front matter.
  Its `title` and an ISO `date` are offered to the caller for the `kb/` front matter.
* A line that starts like one of our block anchors gets one leading space, so the indexer
  cannot read it as a block boundary. Still an HTML comment, still invisible when rendered.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import yaml

from .. import StopAndAsk
from . import render_block_provenance

CONVERTER_MARKDOWN = "markdown-it (verbatim)"

ANCHOR_PREFIX = "<!-- dgx:block="

_FRONT_MATTER_OPEN = "---"
_FRONT_MATTER_CLOSE = ("---", "...")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BACKTICK_RUN = re.compile(r"`+")

#: Top-level token type -> (block kind, confidence). Kinds outside the shared vocabulary are
#: recorded as `paragraph` with the markdown construct in `confidence`, the same convention
#: the Word path uses for unfamiliar docling labels, so search treats them as prose.
_KIND_BY_TOKEN = {
    "heading_open": ("heading", "structural"),
    "paragraph_open": ("paragraph", "structural"),
    "bullet_list_open": ("list", "structural"),
    "ordered_list_open": ("list", "structural"),
    "table_open": ("table", "structural"),
    "fence": ("paragraph", "markdown:fence"),
    "code_block": ("paragraph", "markdown:code_block"),
    "blockquote_open": ("paragraph", "markdown:blockquote"),
    "html_block": ("paragraph", "markdown:html_block"),
    "hr": ("paragraph", "markdown:hr"),
}
_UNPARSED = ("paragraph", "markdown:unparsed")


def _parser():
    from markdown_it import MarkdownIt  # noqa: PLC0415 - lazy, like the other converters

    return MarkdownIt("commonmark").enable("table")


def _read_text(path: Path) -> str:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StopAndAsk(
            f"{path.name} is not UTF-8 text (byte {exc.start} cannot be decoded). Re-save it "
            "as UTF-8 and run conversion again; guessing the encoding would corrupt every "
            "non-ASCII character."
        ) from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_front_matter(lines: list[str]) -> tuple[list[str] | None, list[str]]:
    """`(front matter lines without delimiters, remaining lines)`; `None` when there is none."""
    if not lines or lines[0].rstrip() != _FRONT_MATTER_OPEN:
        return None, lines
    for index in range(1, len(lines)):
        if lines[index].rstrip() in _FRONT_MATTER_CLOSE:
            return lines[1:index], lines[index + 1 :]
    return None, lines


def _declared(front_matter: list[str]) -> dict[str, str]:
    """The `title` and ISO `date` an author declared. Anything unreadable declares nothing."""
    try:
        data = yaml.safe_load("\n".join(front_matter))
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    declared: dict[str, str] = {}
    title = data.get("title")
    if isinstance(title, str) and title.strip():
        declared["title"] = title.strip()
    date = data.get("date")
    if isinstance(date, dt.datetime):
        date = date.date()
    if isinstance(date, dt.date):
        declared["doc_date"] = date.isoformat()
    elif isinstance(date, str) and _ISO_DATE.match(date.strip()):
        declared["doc_date"] = date.strip()
    return declared


def _fenced_yaml(front_matter: list[str]) -> list[str]:
    longest = max((len(run) for run in _BACKTICK_RUN.findall("\n".join(front_matter))), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}yaml", *front_matter, fence]


def _count_images(tokens) -> int:
    count = 0
    for token in tokens:
        if token.type == "image":
            count += 1
        if token.children:
            count += _count_images(token.children)
    return count


def _strip_blank_tail(lines: list[str]) -> list[str]:
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    return lines[:end]


def _split_blocks(lines: list[str], tokens) -> list[tuple[list[str], str, str]]:
    """Cut `lines` at every top-level token's first line. Every non-blank line is kept."""
    starts: list[tuple[int, tuple[str, str]]] = []
    for token in tokens:
        if token.level == 0 and token.nesting in (0, 1) and token.map is not None:
            starts.append((token.map[0], _KIND_BY_TOKEN.get(token.type, _UNPARSED)))

    pieces: list[tuple[list[str], str, str]] = []
    first = starts[0][0] if starts else len(lines)
    if any(line.strip() for line in lines[:first]):
        pieces.append((lines[:first], *_UNPARSED))
    for index, (start, (kind, confidence)) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        pieces.append((lines[start:end], kind, confidence))

    blocks = []
    for block_lines, kind, confidence in pieces:
        trimmed = _strip_blank_tail(block_lines)
        while trimmed and not trimmed[0].strip():
            trimmed = trimmed[1:]
        if trimmed:
            blocks.append((trimmed, kind, confidence))
    return blocks


def _escape_anchors(lines: list[str]) -> tuple[list[str], int]:
    escaped = 0
    out = []
    for line in lines:
        if line.startswith(ANCHOR_PREFIX):
            line = " " + line
            escaped += 1
        out.append(line)
    return out, escaped


def convert_with_provenance(
    path: Path, *, provenance_slug: str
) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
    """Convert a Markdown file. Returns `(body, converter, provenance, extras)`.

    `extras` carries `images` and `escaped_anchors` for the manifest, and under `declared`
    the title and date from the source's front matter, which the caller removes before
    recording the rest.
    """
    lines = _read_text(path).split("\n")
    front_matter, lines = _split_front_matter(lines)
    tokens = _parser().parse("\n".join(lines))

    blocks: list[tuple[list[str], str, str]] = []
    declared: dict[str, str] = {}
    if front_matter is not None:
        declared = _declared(front_matter)
        blocks.append((_fenced_yaml(front_matter), "paragraph", "markdown:front_matter"))
    blocks.extend(_split_blocks(lines, tokens))

    rendered: list[tuple[str, str, str]] = []
    escaped_total = 0
    for block_lines, kind, confidence in blocks:
        block_lines, escaped = _escape_anchors(block_lines)
        if escaped:
            escaped_total += escaped
            confidence = "markdown:anchor-escaped"
        rendered.append(("\n".join(block_lines), kind, confidence))

    body, provenance = render_block_provenance(rendered, provenance_slug)

    images = _count_images(tokens)
    if images:
        note = (
            f"> **INCOMPLETE — this document references {images} image(s), and their "
            "content is not in the text.** No OCR was attempted. Each stays in place below "
            "as the author wrote it."
        )
        body = f"{note}\n\n{body}" if body else note

    extras = {"images": images, "escaped_anchors": escaped_total, "declared": declared}
    return body, CONVERTER_MARKDOWN, provenance, extras
