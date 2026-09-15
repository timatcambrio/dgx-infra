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
from dataclasses import dataclass, replace
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

#: Longest a heading can be. Past this a line is a sentence, whatever size it is set in.
#:
#: Applied to the *whole* heading, wrapped continuation lines included, because the failure
#: it prevents is a run of emphasised prose collapsing onto one `###` line and swallowing
#: everything that should have been under it.
_MAX_HEADING_CHARS = 90

#: A leading bullet or number. Requires whitespace and then something after it, so `--`
#: (which is what an unfilled form field prints) and a lone dash are not list markers.
_LIST_MARKER = re.compile(
    r"^(?P<marker>[-\u2013\u2014\u2022\u2023\u25aa\u25cf\u25e6\u00b7*]|\(?\d{1,2}[.)])\s+(?=\S)"
)

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

#: An AcroForm field. Not authored text -- a Widget's value is already drawn on the page,
#: which is why `_TEXT_ANNOTATION_SUBTYPES` excludes it -- but its rectangle and its `/T`
#: field name are usually exactly what a callout is pointing at.
_WIDGET_SUBTYPE = "Widget"

#: A line that opens like a numbered form field: `1.`, `4)`, `*7.`.
#:
#: Deliberately loose. It only ever *ranks* candidates that geometry has already found, so a
#: false positive costs a slightly worse label and never a wrong binding.
_FIELD_ANCHOR = re.compile(r"^\*?\s*\d{1,2}[.)]")

#: Longest a target label may be before it is elided. A label is an anchor, not a quotation;
#: past this it stops being scannable in the margin of a converted page.
_LABEL_MAX_CHARS = 60

#: Prefix marking a line as annotation rather than printed page text.
#:
#: It has to be visible in the output, not just in metadata. An instruction someone drew onto
#: a form ("select the appropriate FISCAL YEAR") reads as though the document says it, and
#: downstream this text will be retrieved and cited with no access to the PDF. The difference
#: between what a form prints and what a colleague annotated onto it is exactly the kind of
#: thing a citation has to preserve.
ANNOTATION_PREFIX = "> **Annotation:** "


@dataclass(frozen=True)
class Widget:
    """A form field: a rectangle on the page and the name the form gives it."""

    top: float
    bottom: float
    x0: float
    x1: float
    name: str


@dataclass(frozen=True)
class Cell:
    """One cell of a ruled table, kept as a target a callout can point into.

    `row_label` is the first cell in the same row that carries text. A blank form field is
    an empty cell, and the row it sits in is what names it.
    """

    top: float
    bottom: float
    x0: float
    x1: float
    text: str
    row_label: str
    #: Top of the table this cell belongs to, so a note can be placed after the whole table.
    table_top: float


#: How a target was found. These are the only two, and both name a measurement rather than a
#: conclusion: an arrow tip landed here, or this shares a row with that.
#:
#: What deliberately does NOT appear is a claim about which *field* a note belongs to. A PDF's
#: ruling is a layout grid, not a map of the form's logical fields, so the cell under an arrow
#: need not be the field the note is about -- observed on a real form, where a note about a
#: checkbox in field 1 points into a cell naming a different field. The reader downstream has
#: the whole form and can reconcile that; this module has coordinates and cannot.
TARGET_POINTS_TO = "points to"
TARGET_BESIDE = "beside"


@dataclass(frozen=True)
class Target:
    """What an annotation was found near, and by which measurement.

    `kind` has to survive into the output: downstream sees the rendered text and nothing
    else, so an arrow the annotator drew and two things that happen to share a row must not
    arrive looking alike.
    """

    label: str
    kind: str
    top: float


@dataclass(frozen=True)
class Annotation:
    """Authored text attached to a page rather than printed on it."""

    top: float
    x0: float
    text: str
    bottom: float = 0.0
    x1: float = 0.0
    #: Tip of the annotation's callout line (`/CL`), in pdfplumber coordinates, if it has one.
    callout: tuple[float, float] | None = None
    #: The field this annotation describes, once resolved.
    target: Target | None = None

    @property
    def anchor(self) -> float:
        """Vertical position to emit at: the target's, if it has one, else its own."""
        if self.target is None:
            return self.top
        # Just past the target so the note lands under the line it belongs to rather than
        # in place of it. Any epsilon does; page coordinates are points, not counts.
        return self.target.top + 0.01

    def _prefix(self) -> str:
        if self.target is None:
            return ANNOTATION_PREFIX
        return f"> **Annotation** [{self.target.kind}: {self.target.label}]: "

    def render(self) -> str:
        # Multi-line annotation contents carry \r from the PDF; each line needs the
        # blockquote marker or markdown ends the quote at the first one.
        lines = [line.strip() for line in _ANNOTATION_BREAK.split(self.text) if line.strip()]
        if not lines:
            return ""
        first, *rest = lines
        return "\n".join([self._prefix() + first] + [f"> {line}" for line in rest])


@dataclass(frozen=True)
class PageAnalysis:
    """What geometry can tell us about a page before converting it."""

    page_number: int
    columns: int
    ruled_tables: int
    candidate_text_tables: int


def _normalise(text: str) -> str:
    return _DIGITS.sub("#", _WHITESPACE.sub(" ", text).strip().lower())


def _group_words_into_lines(words: list[dict], config: Config) -> list[Line]:
    """Cluster words sharing a baseline into lines, left to right."""
    if not words:
        return []

    lines: list[Line] = []
    current: list[dict] = []
    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if current and abs(word["top"] - current[0]["top"]) > config.pdf_line_tolerance:
            lines.append(_build_line(current, config))
            current = []
        current.append(word)
    if current:
        lines.append(_build_line(current, config))
    return lines


def _rejoin_fragments(
    ordered: list[dict], space_ratio: float
) -> list[tuple[float, float, str]]:
    """Merge adjacent words separated by a gap too narrow to be a space.

    `extract_words` ends a word at an absolute x-tolerance *or* wherever one of the extra
    attributes changes -- and heading detection needs `size` and `fontname`, so it asks for
    both. A word typeset in two subsets of the same face ("S" in one, "ubmit" in the other,
    which is ordinary in PDFs produced by Office) is therefore returned as two words with a
    gap of exactly zero, and joining words with a space renders it "S ubmit".

    Gap alone separates the two cases cleanly, as long as it is measured against the type
    size rather than in absolute points: a space is a glyph with a width, an intra-word
    split has no glyph between the fragments at all.
    """
    merged: list[list] = []
    for word in ordered:
        x0, x1 = float(word["x0"]), float(word["x1"])
        text = str(word["text"])
        threshold = max(float(word.get("size") or 0.0), 1.0) * space_ratio
        if merged and x0 - merged[-1][1] < threshold:
            merged[-1][1] = max(merged[-1][1], x1)
            merged[-1][2] += text
        else:
            merged.append([x0, x1, text])
    return [(x0, x1, text) for x0, x1, text in merged]


def _build_line(words: list[dict], config: Config) -> Line:
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
    rejoined = _rejoin_fragments(ordered, config.pdf_space_width_ratio)
    return Line(
        top=min(float(word["top"]) for word in ordered),
        bottom=max(float(word["bottom"]) for word in ordered),
        x0=min(float(word["x0"]) for word in ordered),
        size=round(max(sizes), 2) if sizes else 0.0,
        bold=bold_chars / total_chars >= 0.8,
        text=" ".join(text for _, _, text in rejoined).strip(),
        words=tuple(rejoined),
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


def _table_cells(page) -> list[Cell]:
    """Every cell of every ruled table on the page, with the text it contains.

    Built from the same `find_tables` call that `_table_regions` filters for rendering, but
    without the filtering: a row that is empty of text is dropped from the rendered table and
    is still somewhere a callout can point, and an empty *cell* is the normal case on a blank
    form. Row and cell geometry come from pdfplumber directly rather than being reconstructed
    from the rendered rows, so the two cannot drift apart.
    """
    cells: list[Cell] = []
    try:
        tables = page.find_tables()
    except Exception:  # noqa: BLE001 - a page whose tables cannot be read still converts
        return []

    for table in tables:
        try:
            extracted = table.extract()
        except Exception:  # noqa: BLE001 - skip this table, keep the rest of the page
            continue
        table_top = float(table.bbox[1])
        for row, texts in zip(table.rows, extracted):
            row_texts = [(text or "").strip() for text in texts]
            row_label = next((text for text in row_texts if text), "")
            for box, text in zip(row.cells, row_texts):
                if box is None:
                    continue
                x0, top, x1, bottom = (float(value) for value in box)
                cells.append(
                    Cell(
                        top=top,
                        bottom=bottom,
                        x0=x0,
                        x1=x1,
                        text=text,
                        row_label=row_label,
                        table_top=table_top,
                    )
                )
    return cells


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


def _heading_level(
    size: float, body: float, heading_sizes: list[float], ratio: float
) -> int | None:
    """Heading depth from type size alone, or `None` if this size is not a heading size.

    `ratio` is the same `PDF_HEADING_SIZE_RATIO` that `to_markdown` uses to decide which
    sizes count as heading sizes at all. It used to be 1.001 here -- anything even a
    hair larger than body text was a heading -- which is fine on a report whose headings
    are half again the body size and catastrophic on a form tutorial, where body text is
    the smallest type on the page and every field label, note and caption sits a point or
    two above it. Those documents came out as a wall of `###` with no body under them.
    """
    if body <= 0 or size <= body * ratio:
        return None
    if heading_sizes:
        for index, candidate in enumerate(heading_sizes[:3]):
            if abs(size - candidate) < 0.01:
                return index + 1
    return 3


def _list_item(text: str) -> tuple[str, str] | None:
    """`(marker, text)` if the line opens with a list marker, else `None`.

    Bullets all normalise to `-`; a number keeps its number, because the order is the
    content. `(1)` becomes `1.` so that the result is a list in markdown rather than a
    paragraph that happens to start with a bracket.
    """
    match = _LIST_MARKER.match(text)
    if match is None:
        return None
    raw = match.group("marker")
    marker = f"{raw.strip('().')}." if raw[-1] in ".)" else "-"
    return marker, text[match.end():].strip()


def _subtype_name(data: dict) -> str:
    subtype = (data or {}).get("Subtype")
    return getattr(subtype, "name", None) or str(subtype or "")


def _pdf_text(value: Any) -> str:
    """A PDF string as text. Field names arrive as bytes far more often than as str."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    return str(value or "").strip()


def _callout_tip(annotation: dict) -> tuple[float, float] | None:
    """The point an annotation's callout line indicates, or `None`.

    `/CL` is 4 or 6 numbers -- an optional knee, then the tip -- and the tip is the last
    pair. This is the annotator's own statement of what the note is about, which makes it
    the one link in the file that needs no inference at all.

    The array is in PDF user space, whose y grows upward from the page bottom, while
    everything else here uses pdfplumber's downward `top`. The conversion needs the page
    height, which is not on the annotation, so the y is returned unconverted and
    `resolve_targets` flips it once the page is in scope.
    """
    raw = (annotation.get("data") or {}).get("CL")
    if not isinstance(raw, (list, tuple)) or len(raw) not in (4, 6):
        return None
    try:
        return float(raw[-2]), float(raw[-1])
    except (TypeError, ValueError):
        return None


def extract_widgets(page) -> list[Widget]:
    """Form fields on one page, as targets an annotation can be bound to.

    A Widget is still not an annotation and is still never emitted as text -- its value is
    already drawn on the page. What it contributes is identity: `/T` is the name the form
    itself gives the field, which beats any label this module could read off the page.

    Fails soft for the same reason `extract_annotations` does: a form whose fields cannot be
    read should convert without field names, not fail to convert.
    """
    widgets: list[Widget] = []
    try:
        raw_annotations = page.annots or []
    except Exception:  # noqa: BLE001 - a broken annot table must not lose the page
        return []

    for annotation in raw_annotations:
        try:
            data = annotation.get("data") or {}
            if _subtype_name(data) != _WIDGET_SUBTYPE:
                continue
            name = _pdf_text(_field_name(data))
            if not name:
                continue
            widgets.append(
                Widget(
                    top=float(annotation.get("top") or 0.0),
                    bottom=float(annotation.get("bottom") or 0.0),
                    x0=float(annotation.get("x0") or 0.0),
                    x1=float(annotation.get("x1") or 0.0),
                    name=name,
                )
            )
        except Exception:  # noqa: BLE001 - skip the widget, keep the page
            continue
    return widgets


def _field_name(data: dict) -> Any:
    """`/T` from the widget, or from its parent field.

    A field split across several widgets (a radio group, a field continued on another page)
    carries its name once, on the shared parent, and leaves the kids unnamed.
    """
    if data.get("T") is not None:
        return data["T"]
    parent = data.get("Parent")
    resolve = getattr(parent, "resolve", None)
    if resolve is not None:
        try:
            parent = resolve()
        except Exception:  # noqa: BLE001 - an unresolvable parent is simply no name
            return None
    if isinstance(parent, dict):
        return parent.get("T")
    return None


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
            if _subtype_name(annotation.get("data") or {}) not in _TEXT_ANNOTATION_SUBTYPES:
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
                    bottom=float(annotation.get("bottom") or 0.0),
                    x1=float(annotation.get("x1") or 0.0),
                    text=text,
                    callout=_callout_tip(annotation),
                )
            )
        except Exception:  # noqa: BLE001 - skip the annotation, keep the page
            continue
    return annotations


# --------------------------------------------------------------------------------------
# Binding annotations to fields
# --------------------------------------------------------------------------------------


def _label(text: str) -> str:
    """A target's text reduced to something that reads as an anchor."""
    collapsed = _WHITESPACE.sub(" ", text).strip()
    if len(collapsed) <= _LABEL_MAX_CHARS:
        return collapsed
    return collapsed[: _LABEL_MAX_CHARS - 1].rstrip() + "\u2026"


def _overlaps(
    top: float, bottom: float, other_top: float, other_bottom: float, tolerance: float
) -> bool:
    """Whether two vertical spans share a row, within `tolerance` points of slack."""
    return not (bottom < other_top - tolerance or top > other_bottom + tolerance)


def _within(
    x: float, y: float, box: tuple[float, float, float, float], tolerance: float
) -> bool:
    """Whether a point falls inside `(x0, top, x1, bottom)`, with slack on every side."""
    x0, top, x1, bottom = box
    return x0 - tolerance <= x <= x1 + tolerance and top - tolerance <= y <= bottom + tolerance


def _line_x1(line: Line) -> float:
    return max((x1 for _, x1, _ in line.words), default=line.x0)


def _widget_anchor(
    widget: Widget, lines: list[Line], tolerance: float
) -> tuple[str, float]:
    """`(label, position)` for a form field.

    The two come from different places on purpose. `/T` is the field's own name and beats any
    label read off the page; the widget's *rectangle*, though, is the input box, which is
    typically set a little above the label beside it -- so anchoring to the rectangle would
    emit the note just before the row it belongs to. The row's own text is the right place.
    """
    row = [
        line.top
        for line in lines
        if line.text and _overlaps(widget.top, widget.bottom, line.top, line.bottom, tolerance)
    ]
    return widget.name, max([widget.top, *row])


def _cell_anchor(cell: Cell) -> tuple[str, float] | None:
    """`(label, position)` for a table cell: its own text, or failing that, its row's.

    A cell with neither is not a target; there is nothing there to name.

    Position is the table's top, not the cell's: a table converts to a single block, so a
    note about one of its cells belongs after the whole thing. Cells keep their own vertical
    order within that by the fractional offset, so several notes on one table come out in row
    order rather than in the order the PDF happened to store them.
    """
    label = cell.text or cell.row_label
    if not label:
        return None
    return _label(label), cell.table_top + cell.top / 1e6


def _target_at(
    point: tuple[float, float],
    widgets: list[Widget],
    cells: list[Cell],
    lines: list[Line],
    tolerance: float,
) -> Target | None:
    """What sits under a point: a form field, then a table cell, then a printed line.

    Cells are tried before lines because a ruled table's words are not in `lines` at all --
    they belong to the table block instead -- so on a form, which is mostly table, the line
    pass has nothing to match against.
    """
    x, y = point
    for widget in widgets:
        if _within(x, y, (widget.x0, widget.top, widget.x1, widget.bottom), tolerance):
            label, top = _widget_anchor(widget, lines, tolerance)
            return Target(label=label, kind=TARGET_POINTS_TO, top=top)
    for cell in cells:
        # No tolerance here, unlike widgets and lines. Cells tile the table with no gaps
        # between them, so slack cannot bridge a gap -- there is none -- and can only pull a
        # tip that missed the table entirely into whichever edge cell happens to be nearest,
        # then report that as exact. A point is inside a cell or it is not.
        if _within(x, y, (cell.x0, cell.top, cell.x1, cell.bottom), 0.0):
            anchor = _cell_anchor(cell)
            if anchor is not None:
                return Target(label=anchor[0], kind=TARGET_POINTS_TO, top=anchor[1])
    for line in lines:
        if line.text and _within(
            x, y, (line.x0, line.top, _line_x1(line), line.bottom), tolerance
        ):
            return Target(label=_label(line.text), kind=TARGET_POINTS_TO, top=line.top)
    return None


def _target_beside(
    annotation: Annotation, widgets: list[Widget], lines: list[Line], tolerance: float
) -> Target | None:
    """The field on the same row as a callout that carries no line of its own.

    A widget wins over a printed label even though both are found the same way, because the
    widget carries the form's own name for the thing and the label only describes it. Either
    way the result is marked `beside`: sharing a row is evidence, not a statement, and on a
    two-column form it is evidence that can be wrong.
    """
    for widget in widgets:
        if _overlaps(
            annotation.top, annotation.bottom, widget.top, widget.bottom, tolerance
        ):
            label, top = _widget_anchor(widget, lines, tolerance)
            return Target(label=label, kind=TARGET_BESIDE, top=top)

    candidates = [
        line
        for line in lines
        if line.text
        and _overlaps(annotation.top, annotation.bottom, line.top, line.bottom, tolerance)
        # Only what lies back towards the body of the page: a callout describes the form it
        # sits beside, never the callout stacked under it in the same margin column.
        and _line_x1(line) <= annotation.x0
    ]
    if not candidates:
        return None
    # A numbered line is the field label; anything else on that row is incidental.
    anchored = [line for line in candidates if _FIELD_ANCHOR.match(line.text)]
    chosen = min(
        anchored or candidates, key=lambda line: annotation.x0 - _line_x1(line)
    )
    return Target(label=_label(chosen.text), kind=TARGET_BESIDE, top=chosen.top)


def resolve_targets(
    annotations: list[Annotation],
    widgets: list[Widget],
    lines: list[Line],
    page_height: float,
    config: Config,
    cells: list[Cell] | None = None,
) -> list[Annotation]:
    """Bind each annotation to the field it describes, where the page allows it.

    Three rules, tried strongest first. Each reports a measurement, and none of them claims
    to know which *field* a note is about -- see `TARGET_POINTS_TO` for why that claim is not
    available from geometry:

    1. the annotation's own callout line, resolved against form fields, ruled table cells and
       printed lines in that order, and reported as `points to`;
    2. a form field sharing its row, reported as `beside`, labelled with the form's own name;
    3. a printed label sharing its row, reported as `beside`.

    An annotation that matches none of them keeps no target and renders exactly as it did
    before any of this existed. That is the intended outcome, not a failure: on a page where
    nothing can be established, a plausible anchor is worse than none, because nothing
    downstream can tell the plausible one from the real ones.
    """
    tolerance = config.pdf_annotation_link_tolerance
    resolved: list[Annotation] = []
    for annotation in annotations:
        target = None
        if annotation.callout is not None:
            x, y = annotation.callout
            target = _target_at(
                (x, page_height - y), widgets, cells or [], lines, tolerance
            )
        if target is None:
            target = _target_beside(annotation, widgets, lines, tolerance)
        resolved.append(replace(annotation, target=target) if target else annotation)
    return resolved


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
                "cells": (
                    _table_cells(page)
                    if config.pdf_annotations and config.pdf_annotation_linking
                    else []
                ),
                "width": float(page.width),
                "height": float(page.height),
                "annotations": (
                    extract_annotations(page, page.extract_text() or "")
                    if config.pdf_annotations
                    else []
                ),
                "widgets": (
                    extract_widgets(page)
                    if config.pdf_annotations and config.pdf_annotation_linking
                    else []
                ),
            }
            for page in pdf.pages
        ]

    for page in pages:
        page["body_words"] = _outside_regions(
            page["words"], [box for box, _ in page["tables"]]
        )
        page["lines"] = _group_words_into_lines(page["body_words"], config)
        if config.pdf_annotation_linking and page["annotations"]:
            page["annotations"] = resolve_targets(
                page["annotations"],
                page["widgets"],
                page["lines"],
                page["height"],
                config,
                cells=page["cells"],
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
            # A bound annotation is emitted at its *target's* position, not its own. That is
            # the whole point of binding: in a margin column the notes' vertical order is
            # not the fields' order, so leaving them at their own y is what scattered them.
            elements.append((annotation.anchor, rendered))

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


def _render_list(items: list[tuple[float, str, list[str]]], tolerance: float) -> str:
    """Render collected list items, nesting by how far their markers are indented.

    Depth comes from the marker's x, grouped within the same tolerance the table code uses
    for column alignment. Absolute indent is meaningless across documents; the *order* of
    the distinct indents on one list is not.
    """
    columns: list[float] = []
    for x0, _, _ in sorted(items, key=lambda item: item[0]):
        if not columns or x0 - columns[-1] > tolerance:
            columns.append(x0)

    lines = []
    for x0, marker, parts in items:
        depth = max(index for index, column in enumerate(columns) if x0 >= column - tolerance)
        lines.append(f"{'  ' * depth}{marker} {' '.join(parts).strip()}")
    return "\n".join(lines)


def _heading_run(
    lines: list[Line],
    start: int,
    in_table: dict[int, tuple[int, int]],
    body: float,
    heading_sizes: list[float],
    wrap_limit: float,
    config: Config,
) -> tuple[int, int]:
    """`(end_index, level)` for the run starting at `start`; level 0 means "not a heading".

    A heading that wraps is one heading, so the run extends over following lines set the
    same way and spaced as a wrap rather than as a new block. The run is then accepted only
    if the whole thing is short: a wrapped *title* is a heading, a wrapped *paragraph* that
    happens to be set larger than body text is not, and before this the two were
    indistinguishable -- which is how a bulleted notes box became a single `###` line.

    `end_index` is returned for a rejected run too, and the caller must consume all of it.
    Rejecting only the first line would let the tail re-form into a run short enough to
    pass, so the last line of a paragraph would come back as a heading.
    """
    first = lines[start]
    level = _heading_level(first.size, body, heading_sizes, config.pdf_heading_size_ratio)
    if level is None:
        if not (first.bold and body > 0 and first.size >= body):
            return start + 1, 0
        level = 3

    end = start + 1
    while (
        end < len(lines)
        and end not in in_table
        and abs(lines[end].size - first.size) < 0.01
        and lines[end].bold == first.bold
        and _list_item(lines[end].text) is None
        and lines[end].top - lines[end - 1].bottom <= wrap_limit
    ):
        end += 1

    combined = " ".join(line.text for line in lines[start:end])
    if len(combined) >= _MAX_HEADING_CHARS:
        return end, 0
    return end, level


def _paragraphs(
    lines: list[Line], body: float, heading_sizes: list[float], config: Config
) -> list[tuple[float, str]]:
    """Group lines into headings, paragraphs, lists and borderless tables."""
    if not lines:
        return []

    in_table: dict[int, tuple[int, int]] = {}
    for start, end in find_aligned_table_runs(lines, config):
        for index in range(start, end):
            in_table[index] = (start, end)

    heights = [line.bottom - line.top for line in lines if line.bottom > line.top]
    line_height = statistics.median(heights) if heights else 0.0
    gap_limit = line_height * config.pdf_paragraph_gap_ratio
    wrap_limit = max(line_height * 1.4, 1.0)

    output: list[tuple[float, str]] = []
    buffer: list[Line] = []
    # (marker x0, marker, text parts) per item of the list currently being collected.
    items: list[tuple[float, str, list[str]]] = []
    list_top = 0.0
    list_size = 0.0

    def flush() -> None:
        if not buffer:
            return
        text = " ".join(line.text for line in buffer).strip()
        if text:
            output.append((buffer[0].top, text))
        buffer.clear()

    def flush_list() -> None:
        nonlocal items
        if items:
            output.append((list_top, _render_list(items, config.pdf_column_align_tolerance)))
            items = []

    index = 0
    while index < len(lines):
        line = lines[index]

        if index in in_table:
            start, end = in_table[index]
            flush()
            flush_list()
            output.append((lines[start].top, _table_from_lines(lines[start:end], config)))
            index = end
            continue

        item = _list_item(line.text)
        broken = index > 0 and line.top - lines[index - 1].bottom > gap_limit

        if item is not None:
            flush()
            if items and (broken or abs(line.size - list_size) >= 0.01):
                flush_list()
            if not items:
                list_top, list_size = line.top, line.size
            marker, text = item
            items.append((line.x0, marker, [text]))
            index += 1
            continue

        # An unmarked line hard against the item above it, set the same way, is that
        # item wrapping. It cannot be told from indentation: in real documents a wrapped
        # bullet starts at the marker's own x, not at the text's.
        if items and not broken and abs(line.size - list_size) < 0.01:
            items[-1][2].append(line.text)
            index += 1
            continue

        flush_list()

        end, level = _heading_run(
            lines, index, in_table, body, heading_sizes, wrap_limit, config
        )
        if level:
            flush()
            text = " ".join(heading.text for heading in lines[index:end])
            output.append((line.top, f"{'#' * level} {text}"))
            index = end
            continue

        for candidate in lines[index:end]:
            if buffer and candidate.top - buffer[-1].bottom > gap_limit:
                flush()
            buffer.append(candidate)
        index = end

    flush()
    flush_list()
    return output
