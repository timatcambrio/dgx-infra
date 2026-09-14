"""Model-free PDF layout reconstruction from character geometry.

Everything here is measurement: word positions, font sizes, ruling lines. No layout model,
no OCR, no inference of any kind — which is the point. It means the PDF path fetches nothing,
needs no ML runtime, produces identical output on every machine, and can be read and audited
by a client who could never audit a set of model weights.

The trade is real and worth stating plainly. A layout model understands that a run of text is
a caption and this other run is a footnote; geometry only knows that one is smaller and lower.
What geometry does well is exactly what a born-digital handbook needs: reading order, heading
levels from type size, ruled tables, and running headers that repeat in the same place on
every page. What it does badly is borderless tables and unusual column layouts, which is why
`analyse` reports both so a document that needs more can be escalated deliberately.

Coordinates follow pdfplumber's convention: `top` grows downward from the page top.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..config import Config
from .csv_table import render_table

#: Extra per-word attributes needed for heading detection.
_WORD_ATTRS = ["size", "fontname"]

#: Digits are masked when testing whether a line repeats across pages, so that "Page 3 of 47"
#: and "Page 4 of 47" are recognised as the same running footer.
_DIGITS = re.compile(r"\d+")

_WHITESPACE = re.compile(r"\s+")

#: PDF annotation contents use \r, sometimes \r\n, for their own line breaks.
_ANNOTATION_BREAK = re.compile(r"\r\n?|\n")


@dataclass(frozen=True)
class Line:
    top: float
    bottom: float
    x0: float
    size: float
    bold: bool
    text: str
    #: (x0, x1, text) per word, kept so aligned-column tables can be recovered.
    words: tuple[tuple[float, float, str], ...] = ()

    def cells(self, gap_ratio: float) -> list[tuple[float, str]]:
        """Split the line where the gap between words is far wider than a word space.

        Returns `(x0, text)` per cell. A single-cell result means the line is ordinary prose.
        """
        if not self.words:
            return [(self.x0, self.text)]
        threshold = max(self.size, 1.0) * gap_ratio
        cells: list[tuple[float, list[str]]] = []
        previous_x1: float | None = None
        for x0, x1, text in self.words:
            if previous_x1 is None or x0 - previous_x1 > threshold:
                cells.append((x0, [text]))
            else:
                cells[-1][1].append(text)
            previous_x1 = x1
        return [(x0, " ".join(parts).strip()) for x0, parts in cells]


#: Annotation subtypes that carry authored text a reader is meant to read.
#:
#: `FreeText` is the "Markup" callout -- the box someone types into when annotating a form in
#: Preview or Acrobat. `Text` is the sticky note. Deliberately NOT `Widget`: those are form
#: fields, whose values are already drawn onto the page and would come back twice.
_TEXT_ANNOTATION_SUBTYPES = frozenset({"FreeText", "Text"})

#: Prefix marking a line as annotation rather than printed page text.
#:
#: It has to be visible in the output, not just in metadata. An instruction someone drew onto
#: a form ("select the appropriate FISCAL YEAR") reads as though the document says it, and
#: downstream this text will be retrieved and cited with no access to the PDF. The difference
#: between what a form prints and what a colleague annotated onto it is exactly the kind of
#: thing a citation has to preserve.
ANNOTATION_PREFIX = "> **Annotation:** "


@dataclass(frozen=True)
class Annotation:
    """Authored text attached to a page rather than printed on it."""

    top: float
    x0: float
    text: str

    def render(self) -> str:
        # Multi-line annotation contents carry \r from the PDF; each line needs the
        # blockquote marker or markdown ends the quote at the first one.
        lines = [line.strip() for line in _ANNOTATION_BREAK.split(self.text) if line.strip()]
        if not lines:
            return ""
        first, *rest = lines
        return "\n".join([ANNOTATION_PREFIX + first] + [f"> {line}" for line in rest])


@dataclass(frozen=True)
class PageAnalysis:
    """What geometry can tell us about a page before converting it."""

    page_number: int
    columns: int
    ruled_tables: int
    candidate_text_tables: int


def _normalise(text: str) -> str:
    return _DIGITS.sub("#", _WHITESPACE.sub(" ", text).strip().lower())


def _group_words_into_lines(words: list[dict], tolerance: float) -> list[Line]:
    """Cluster words sharing a baseline into lines, left to right."""
    if not words:
        return []

    lines: list[Line] = []
    current: list[dict] = []
    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if current and abs(word["top"] - current[0]["top"]) > tolerance:
            lines.append(_build_line(current))
            current = []
        current.append(word)
    if current:
        lines.append(_build_line(current))
    return lines


def _build_line(words: list[dict]) -> Line:
    ordered = sorted(words, key=lambda w: w["x0"])
    sizes = [float(word.get("size") or 0.0) for word in ordered]

    # Weighted by characters, not by any-word-matches: a sentence containing one bold term is
    # a sentence, and treating it as a heading tears the paragraph in half mid-clause.
    bold_chars = sum(
        len(str(word["text"]))
        for word in ordered
        if any(
            marker in str(word.get("fontname") or "").lower()
            for marker in ("bold", "black", "heavy", "semibold")
        )
    )
    total_chars = sum(len(str(word["text"])) for word in ordered) or 1
    return Line(
        top=min(float(word["top"]) for word in ordered),
        bottom=max(float(word["bottom"]) for word in ordered),
        x0=min(float(word["x0"]) for word in ordered),
        size=round(max(sizes), 2) if sizes else 0.0,
        bold=bold_chars / total_chars >= 0.8,
        text=" ".join(str(word["text"]) for word in ordered).strip(),
        words=tuple(
            (float(word["x0"]), float(word["x1"]), str(word["text"])) for word in ordered
        ),
    )


def detect_columns(words: list[dict], page_width: float, gap_fraction: float) -> int:
    """Count text columns by looking for a vertical gutter with no words in it.

    A gutter is only believed if it sits near the middle of the page and both sides carry a
    real share of the words — otherwise an indented block or a wide margin reads as a column
    split and the page comes out interleaved, which is far worse than treating it as one
    column.
    """
    if len(words) < 20 or page_width <= 0:
        return 1

    minimum_gap = page_width * gap_fraction
    spans = sorted((float(w["x0"]), float(w["x1"])) for w in words)

    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    for left, right in zip(merged, merged[1:]):
        gap_start, gap_end = left[1], right[0]
        if gap_end - gap_start < minimum_gap:
            continue
        centre = (gap_start + gap_end) / 2
        if not 0.3 * page_width < centre < 0.7 * page_width:
            continue
        left_share = sum(1 for w in words if float(w["x1"]) <= gap_start) / len(words)
        right_share = sum(1 for w in words if float(w["x0"]) >= gap_end) / len(words)
        if left_share > 0.25 and right_share > 0.25:
            return 2
    return 1


def _table_regions(page) -> list[tuple[tuple[float, float, float, float], list[list[str]]]]:
    """Ruled tables only.

    pdfplumber's text-alignment strategy will happily find "tables" in ordinary prose, and a
    hallucinated table is worse than a missed one -- it destroys the paragraph it consumed.
    Borderless tables are therefore left to flow as text and reported by `analyse` instead.
    """
    regions = []
    for table in page.find_tables():
        rows = [
            [(cell or "").strip() for cell in row]
            for row in table.extract()
            if any((cell or "").strip() for cell in row)
        ]
        if len(rows) >= 2 and len(rows[0]) >= 2:
            regions.append((table.bbox, rows))
    return regions


def _outside_regions(words: list[dict], boxes: Iterable[tuple]) -> list[dict]:
    boxes = list(boxes)
    if not boxes:
        return words
    kept = []
    for word in words:
        centre_x = (float(word["x0"]) + float(word["x1"])) / 2
        centre_y = (float(word["top"]) + float(word["bottom"])) / 2
        if any(
            x0 <= centre_x <= x1 and top <= centre_y <= bottom
            for x0, top, x1, bottom in boxes
        ):
            continue
        kept.append(word)
    return kept


def find_repeated_margin_lines(
    pages_lines: list[list[Line]], page_heights: list[float], config: Config
) -> set[str]:
    """Normalised text of lines that recur in the top or bottom margin across pages.

    Detecting running headers and footers geometrically is one of the places where measurement
    beats classification: the same text at the same place on most pages is direct evidence,
    where a layout model has to infer it per page and can disagree with itself.
    """
    if len(pages_lines) < 3:
        return set()

    counts: dict[str, int] = {}
    for lines, height in zip(pages_lines, page_heights):
        margin = height * config.pdf_margin_fraction
        seen_on_page = {
            _normalise(line.text)
            for line in lines
            if line.text and (line.top <= margin or line.bottom >= height - margin)
        }
        for text in seen_on_page:
            counts[text] = counts.get(text, 0) + 1

    threshold = max(2, int(len(pages_lines) * config.pdf_repeat_page_fraction))
    return {text for text, count in counts.items() if count >= threshold and text}


def _body_size(pages_lines: list[list[Line]]) -> float:
    """The document's dominant type size, weighted by how much text is set in it."""
    weights: dict[float, int] = {}
    for lines in pages_lines:
        for line in lines:
            if line.text:
                weights[line.size] = weights.get(line.size, 0) + len(line.text)
    if not weights:
        return 0.0
    return max(weights.items(), key=lambda item: (item[1], -item[0]))[0]


def _heading_level(size: float, body: float, heading_sizes: list[float]) -> int | None:
    if body <= 0 or size <= body * 1.001:
        return None
    if heading_sizes:
        for index, candidate in enumerate(heading_sizes[:3]):
            if abs(size - candidate) < 0.01:
                return index + 1
    return 3


def extract_annotations(page, page_text: str) -> list[Annotation]:
    """Text annotations on one page, minus any whose text the page already prints.

    The de-duplication is not theoretical. An annotation that has been *flattened* into the
    page -- which is what "Print to PDF" or a Save As Flattened does -- leaves both the
    drawn text and the annotation object behind, and emitting both would make the document
    say everything twice.

    Fails soft on a malformed annotation dictionary: a PDF whose annotations cannot be read
    still converts, it just converts without them. A hard failure here would take out the
    document's printed text too, which is a strictly worse outcome.
    """
    annotations: list[Annotation] = []
    try:
        raw_annotations = page.annots or []
    except Exception:  # noqa: BLE001 - a broken annot table must not lose the page
        return []

    printed = _normalise(page_text)
    for annotation in raw_annotations:
        try:
            subtype = annotation.get("data", {}).get("Subtype")
            name = getattr(subtype, "name", None) or str(subtype or "")
            if name not in _TEXT_ANNOTATION_SUBTYPES:
                continue
            contents = annotation.get("contents")
            if isinstance(contents, bytes):
                contents = contents.decode("utf-8", "replace")
            text = (contents or "").strip()
            if not text or _normalise(text) in printed:
                continue
            annotations.append(
                Annotation(
                    top=float(annotation.get("top") or 0.0),
                    x0=float(annotation.get("x0") or 0.0),
                    text=text,
                )
            )
        except Exception:  # noqa: BLE001 - skip the annotation, keep the page
            continue
    return annotations


def analyse(path: Path, config: Config) -> list[PageAnalysis]:
    """Per-page geometry facts, for deciding whether this document needs more than geometry."""
    import pdfplumber  # noqa: PLC0415 - lazy: keeps `report` fast when it is not needed

    results: list[PageAnalysis] = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(extra_attrs=_WORD_ATTRS)
            ruled = len(_table_regions(page))
            text_tables = 0
            if not ruled:
                try:
                    text_tables = len(
                        page.find_tables(
                            {
                                "vertical_strategy": "text",
                                "horizontal_strategy": "text",
                            }
                        )
                    )
                except Exception:  # noqa: BLE001 - diagnostic only, never fails a run
                    text_tables = 0
            results.append(
                PageAnalysis(
                    page_number=number,
                    columns=detect_columns(
                        words, float(page.width), config.pdf_column_gap_fraction
                    ),
                    ruled_tables=ruled,
                    candidate_text_tables=text_tables,
                )
            )
    return results


def to_markdown(path: Path, config: Config) -> str:
    """Convert a PDF to markdown using geometry alone."""
    import pdfplumber  # noqa: PLC0415 - lazy by design

    with pdfplumber.open(path) as pdf:
        pages = [
            {
                "words": page.extract_words(extra_attrs=_WORD_ATTRS),
                "tables": _table_regions(page),
                "width": float(page.width),
                "height": float(page.height),
                "annotations": (
                    extract_annotations(page, page.extract_text() or "")
                    if config.pdf_annotations
                    else []
                ),
            }
            for page in pdf.pages
        ]

    for page in pages:
        page["body_words"] = _outside_regions(
            page["words"], [box for box, _ in page["tables"]]
        )
        page["lines"] = _group_words_into_lines(
            page["body_words"], config.pdf_line_tolerance
        )

    repeated = find_repeated_margin_lines(
        [page["lines"] for page in pages], [page["height"] for page in pages], config
    )
    body = _body_size([page["lines"] for page in pages])
    heading_sizes = sorted(
        {
            line.size
            for page in pages
            for line in page["lines"]
            if line.text and line.size > body * config.pdf_heading_size_ratio
        },
        reverse=True,
    )

    blocks: list[str] = []
    for page in pages:
        blocks.extend(_render_page(page, repeated, body, heading_sizes, config))

    return "\n\n".join(block for block in blocks if block.strip())


def _render_page(
    page: dict[str, Any],
    repeated: set[str],
    body: float,
    heading_sizes: list[float],
    config: Config,
) -> list[str]:
    keep = [
        line
        for line in page["lines"]
        if line.text and _normalise(line.text) not in repeated
    ]

    columns = detect_columns(
        page["body_words"], page["width"], config.pdf_column_gap_fraction
    )
    if columns == 2:
        midpoint = page["width"] / 2
        ordered = [line for line in keep if line.x0 < midpoint] + [
            line for line in keep if line.x0 >= midpoint
        ]
    else:
        ordered = keep

    elements: list[tuple[float, str]] = []
    for line_group in _paragraphs(ordered, body, heading_sizes, config):
        elements.append(line_group)
    for box, rows in page["tables"]:
        elements.append((float(box[1]), render_table(rows)))
    for annotation in page.get("annotations", ()):
        rendered = annotation.render()
        if rendered:
            elements.append((annotation.top, rendered))

    # Two-column pages are already in reading order; re-sorting by `top` would interleave
    # the columns again, which is the failure this whole branch exists to avoid. Tables and
    # annotations therefore land after the prose on those pages rather than at their own
    # vertical position -- the same trade the table path has always made, and preferable to
    # shuffling the columns back together.
    if columns == 1:
        elements.sort(key=lambda item: item[0])
    return [text for _, text in elements]


def find_aligned_table_runs(lines: list[Line], config: Config) -> list[tuple[int, int]]:
    """Index ranges of consecutive lines that form a borderless, column-aligned table.

    Ruled tables are pdfplumber's job; this recovers the ones drawn with nothing but
    whitespace, which is how most rate and reference tables in handbooks are typeset. Left
    as prose they are worse than useless — every row collapses into one run-on line.

    Three conditions must all hold, and they are deliberately strict, because inventing a
    table inside prose destroys the paragraph it consumes:

    * at least `PDF_MIN_TABLE_ROWS` consecutive lines,
    * every line splitting into the *same* number of cells, at least two,
    * cell left edges vertically aligned within `PDF_COLUMN_ALIGN_TOLERANCE` points.

    Justified prose can open a wide gap, but not the same number of gaps at the same
    horizontal positions on three consecutive lines.
    """
    runs: list[tuple[int, int]] = []
    start: int | None = None
    anchors: list[float] | None = None

    for index, line in enumerate(lines + [None]):  # sentinel closes a trailing run
        cells = line.cells(config.pdf_cell_gap_ratio) if line is not None else []
        positions = [x0 for x0, text in cells if text]

        matches = (
            len(positions) >= 2
            and anchors is not None
            and len(positions) == len(anchors)
            and all(
                abs(a - b) <= config.pdf_column_align_tolerance
                for a, b in zip(anchors, positions)
            )
        )

        if matches:
            continue

        if start is not None and index - start >= config.pdf_min_table_rows:
            runs.append((start, index))

        if len(positions) >= 2:
            start, anchors = index, positions
        else:
            start, anchors = None, None

    return runs


def _table_from_lines(lines: list[Line], config: Config) -> str:
    rows = [
        [text for _, text in line.cells(config.pdf_cell_gap_ratio)] for line in lines
    ]
    width = max(len(row) for row in rows)
    return render_table([row + [""] * (width - len(row)) for row in rows])


def _paragraphs(
    lines: list[Line], body: float, heading_sizes: list[float], config: Config
) -> list[tuple[float, str]]:
    """Group lines into headings, paragraphs and borderless tables."""
    if not lines:
        return []

    in_table = {}
    for start, end in find_aligned_table_runs(lines, config):
        for index in range(start, end):
            in_table[index] = (start, end)

    heights = [line.bottom - line.top for line in lines if line.bottom > line.top]
    line_height = statistics.median(heights) if heights else 0.0
    gap_limit = line_height * config.pdf_paragraph_gap_ratio

    output: list[tuple[float, str]] = []
    buffer: list[Line] = []
    previous_bottom = float("-inf")

    def flush() -> None:
        if not buffer:
            return
        text = " ".join(line.text for line in buffer).strip()
        if text:
            output.append((buffer[0].top, text))
        buffer.clear()

    for index, line in enumerate(lines):
        if index in in_table:
            start, end = in_table[index]
            if index == start:
                flush()
                output.append((line.top, _table_from_lines(lines[start:end], config)))
            continue

        level = _heading_level(line.size, body, heading_sizes)
        is_heading = level is not None or (
            line.bold and body > 0 and len(line.text) < 90 and line.size >= body
        )

        if is_heading:
            flush()
            marker = "#" * (level or 3)
            # A heading that wraps is one heading. Continuing the previous line rather than
            # emitting a second one keeps a long title intact instead of splitting it
            # mid-phrase, which reads as two unrelated sections downstream.
            if (
                output
                and output[-1][1].startswith(f"{marker} ")
                and line.top - previous_bottom <= max(line_height * 1.4, 1.0)
            ):
                output[-1] = (output[-1][0], f"{output[-1][1]} {line.text}")
            else:
                output.append((line.top, f"{marker} {line.text}"))
            previous_bottom = line.bottom
            continue

        if buffer and line.top - buffer[-1].bottom > gap_limit:
            flush()
        buffer.append(line)
        previous_bottom = line.bottom

    flush()
    return output
