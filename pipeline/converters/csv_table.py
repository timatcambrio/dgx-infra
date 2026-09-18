"""CSV -> markdown table. Table mode only.

Read with the stdlib `csv` module; pandas is not an approved dependency and this does not
need it.

The guardrails exist because a giant markdown table is worthless downstream: it cannot be
chunked meaningfully and it retrieves badly. Whether a large table should become one
document per row, a grouped set of documents, or stay tabular is a retrieval design
decision, not something a converter gets to decide by default. So an oversized CSV stops
the run instead of emitting.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .. import StopAndAsk
from ..config import Config
from . import render_block_provenance

#: Enough bytes for `csv.Sniffer` to see the delimiter without reading a huge file.
_SNIFF_BYTES = 8192

CONVERTER_NAME = "stdlib-csv (table mode)"


def prettify_filename(path: Path) -> str:
    """`travel_rates-2024.csv` -> `Travel Rates 2024`. Only a fallback for a missing title."""
    stem = path.stem.replace("_", " ").replace("-", " ")
    words = [word for word in stem.split() if word]
    return " ".join(word if word.isupper() else word.capitalize() for word in words)


def read_rows(path: Path) -> list[list[str]]:
    """Rows as strings, dialect sniffed. Blank trailing lines are dropped."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise StopAndAsk(
            f"{path.name} is empty. An empty CSV is either a mistake or a placeholder; "
            "decide which before it becomes a kb/ file."
        )
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            text[:_SNIFF_BYTES], delimiters=",;\t|"
        )
    except csv.Error:
        # A single-column file gives the sniffer nothing to detect; comma is the safe
        # assumption and the column-count guardrail below will catch it anyway.
        dialect = csv.excel
    rows = [row for row in csv.reader(text.splitlines(), dialect) if any(cell.strip() for cell in row)]
    if not rows:
        raise StopAndAsk(f"{path.name} contains no non-blank rows.")
    return rows


def escape_cell(value: str) -> str:
    """Make a cell safe inside a markdown table.

    Newlines collapse to spaces rather than `<br>`: the output contract forbids raw HTML.
    """
    return value.replace("|", r"\|").replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()


def render_table(rows: list[list[str]]) -> str:
    header, *body = rows
    width = len(header)
    lines = [
        "| " + " | ".join(escape_cell(cell) for cell in header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in body:
        # Ragged rows are padded or truncated to the header width so the table stays valid
        # markdown. Truncation is reported by the caller via the row/column guardrails only
        # when it changes the shape, so keep this lossless for well-formed files.
        cells = list(row[:width]) + [""] * max(0, width - len(row))
        lines.append("| " + " | ".join(escape_cell(cell) for cell in cells) + " |")
    return "\n".join(lines)


def check_guardrails(path: Path, rows: list[list[str]], config: Config) -> None:
    header = rows[0]
    data_rows = len(rows) - 1
    columns = len(header)

    if columns < 2:
        raise StopAndAsk(
            f"{path.name} has {columns} column(s). A single-column CSV is a list, not a "
            "reference table -- decide whether it becomes prose, one document per line, or "
            "is skipped."
        )
    if data_rows > config.csv_max_rows:
        raise StopAndAsk(
            f"{path.name} has {data_rows} data rows (limit CSV_MAX_ROWS="
            f"{config.csv_max_rows}). A table this size is worthless as one markdown blob. "
            "Decide the retrieval shape -- one doc per row, grouped docs, or keep it "
            "tabular outside kb/ -- before converting it."
        )
    if columns > config.csv_max_cols:
        raise StopAndAsk(
            f"{path.name} has {columns} columns (limit CSV_MAX_COLS="
            f"{config.csv_max_cols}). Decide the retrieval shape before converting it."
        )


def convert_with_provenance(
    path: Path,
    config: Config,
    *,
    provenance_slug: str | None = None,
    title: str | None = None,
    description: str | None = None,
    csv_mode: str = "table",
) -> tuple[str, str, list[dict[str, Any]]]:
    """Convert one CSV. Returns `(markdown_body, converter_name, provenance)`.

    Blocks, in order: `heading` (the `# ` line), `paragraph` (the description, only if one
    was given), `table` (the rendered rows). Ids `b001`, `b002`, `b003` (or `b001`, `b002`
    without a description).
    """
    if csv_mode != "table":
        raise StopAndAsk(
            f"{path.name} declares csv_mode={csv_mode!r}. Only 'table' is implemented; "
            "'record' mode is an open retrieval design question, not a converter default."
        )

    rows = read_rows(path)
    check_guardrails(path, rows, config)

    heading = title or prettify_filename(path)
    blocks: list[tuple[str, str, str]] = [(f"# {heading}", "heading", "structural")]
    if description:
        blocks.append((description.strip(), "paragraph", "structural"))
    blocks.append((render_table(rows), "table", "structural"))

    if provenance_slug is None:
        body = "\n\n".join(text for text, _, _ in blocks)
        provenance: list[dict[str, Any]] = []
    else:
        body, provenance = render_block_provenance(blocks, provenance_slug)
    return body, CONVERTER_NAME, provenance


def convert(
    path: Path,
    config: Config,
    *,
    title: str | None = None,
    description: str | None = None,
    csv_mode: str = "table",
) -> tuple[str, str]:
    """Convert one CSV. Returns `(markdown_body, converter_name)`."""
    body, converter, _ = convert_with_provenance(
        path, config, title=title, description=description, csv_mode=csv_mode
    )
    return body, converter
