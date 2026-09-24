#!/usr/bin/env python3
"""Regenerate the test fixtures deterministically.

The real documents cannot be committed, so the test suite runs against small synthetic files
that are generated here and then **committed**. Committing them means the suite needs neither
`reportlab`, nor `python-docx`, nor a network at run time — only the runtime dependencies.

Determinism is fought for in two places:

* ReportLab embeds a creation date and a document ID by default. `rl_config.invariant`
  replaces both with fixed values.
* A `.docx` is a zip, and zip entries carry mtimes. python-docx writes them with the current
  time, so the archive is repacked afterwards with a fixed timestamp and sorted entry order.

Run: `uv run python tests/make_fixtures.py`
"""

from __future__ import annotations

import csv
import shutil
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Fixed timestamp for every generated artefact. Any constant would do; what matters is that
#: it never comes from the clock.
EPOCH = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
ZIP_DATE_TIME = (2020, 1, 1, 0, 0, 0)

BODY_TEXT = (
    "Travel costs are reimbursed at the rates in effect on the date the travel began. "
    "Receipts are required for any single expense of twenty-five dollars or more. "
    "Requests submitted more than sixty days after the end of travel require written "
    "justification from the department head."
)

TABLE_ROWS = [
    ["Expense category", "Limit", "Receipt required"],
    ["Lodging", "$180 / night", "Yes"],
    ["Meals", "$74 / day", "No"],
    ["Mileage", "$0.67 / mile", "No"],
]


def _canvas(path: Path, pagesize: tuple[float, float] | None = None):
    from reportlab import rl_config

    # Must be set before the canvas is constructed: it fixes both the /CreationDate and the
    # /ID, which are otherwise clock- and random-seeded.
    rl_config.invariant = 1

    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen.canvas import Canvas

    return Canvas(str(path), pagesize=pagesize or LETTER, invariant=1)


def _draw_heading(canvas, text: str, y: int) -> int:
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(72, y, text)
    return y - 28


def _draw_paragraph(canvas, text: str, y: int, width: int = 78, size: float = 11) -> int:
    canvas.setFont("Helvetica", size)
    words = text.split()
    line: list[str] = []
    for word in words:
        line.append(word)
        if len(" ".join(line)) > width:
            canvas.drawString(72, y, " ".join(line[:-1]))
            y -= 15
            line = [word]
    if line:
        canvas.drawString(72, y, " ".join(line))
        y -= 15
    return y


def _draw_table(canvas, rows: list[list[str]], y: int) -> int:
    column_x = (72, 260, 400)
    for index, row in enumerate(rows):
        canvas.setFont("Helvetica-Bold" if index == 0 else "Helvetica", 11)
        for x, cell in zip(column_x, row):
            canvas.drawString(x, y, cell)
        y -= 16
    return y


def _text_page_image(text: str, size: tuple[int, int] = (1000, 1294)):
    """Render text to a bitmap, so the resulting PDF page has no text layer at all."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    y = 80
    for line in text.split("\n"):
        draw.text((80, y), line, fill="black")
        y += 22
    return image


def born_digital(path: Path) -> None:
    """3 pages: headings, a paragraph, and one simple table. Must classify `clean`."""
    canvas = _canvas(path)

    y = _draw_heading(canvas, "Travel Reimbursement Handbook", 720)
    y = _draw_paragraph(canvas, BODY_TEXT, y - 6)
    canvas.showPage()

    y = _draw_heading(canvas, "Allowable Expenses", 720)
    y = _draw_paragraph(canvas, BODY_TEXT, y - 6)
    y = _draw_table(canvas, TABLE_ROWS, y - 20)
    canvas.showPage()

    y = _draw_heading(canvas, "Submitting a Claim", 720)
    _draw_paragraph(canvas, BODY_TEXT, y - 6)
    canvas.showPage()

    canvas.save()


def image_only(path: Path) -> None:
    """The same content rasterised. No text layer whatsoever. Must classify `needs_ocr`."""
    from reportlab.lib.utils import ImageReader

    canvas = _canvas(path)
    image = _text_page_image(
        "Travel Reimbursement Handbook\n\n"
        + "\n".join(BODY_TEXT[index : index + 70] for index in range(0, len(BODY_TEXT), 70))
    )
    canvas.drawImage(ImageReader(image), 36, 36, width=540, height=700)
    canvas.showPage()
    canvas.save()


def mixed(path: Path) -> None:
    """2 text pages + 1 image page, so the low-page fraction exceeds the max: `partial`."""
    from reportlab.lib.utils import ImageReader

    canvas = _canvas(path)

    y = _draw_heading(canvas, "Travel Reimbursement Handbook", 720)
    _draw_paragraph(canvas, BODY_TEXT, y - 6)
    canvas.showPage()

    y = _draw_heading(canvas, "Allowable Expenses", 720)
    y = _draw_paragraph(canvas, BODY_TEXT, y - 6)
    _draw_table(canvas, TABLE_ROWS, y - 20)
    canvas.showPage()

    image = _text_page_image("Appendix C - Scanned Rate Schedule\n\n" + BODY_TEXT[:200])
    canvas.drawImage(ImageReader(image), 36, 36, width=540, height=700)
    canvas.showPage()

    canvas.save()


def mojibake(path: Path) -> None:
    """Plenty of extractable characters, none of them usable: `alpha_ratio` must fail.

    This is the fixture that proves the alpha-ratio check is real — a character count alone
    scores this document as text-rich.

    It simulates the *result* of a broken font-to-Unicode map rather than building a broken
    cmap by hand: the page is filled with symbol soup that is WinAnsi-encodable (so ReportLab
    writes it happily) and that counts as neither alphanumeric, punctuation, nor whitespace.
    """
    canvas = _canvas(path)
    soup = "¤¦¨©®°±¶÷×µ§¬­¯²³·¸¹¼½¾" * 6
    canvas.setFont("Helvetica", 11)
    y = 720
    for _ in range(28):
        canvas.drawString(72, y, soup[:90])
        soup = soup[7:] + soup[:7]
        y -= 16
    canvas.showPage()
    canvas.save()


#: `linked_form.pdf` geometry, in ReportLab's bottom-up user space.
#:
#: The four rows exist to produce one of each resolution outcome, so that a change in the
#: cascade shows up as a change in *which* rule fired rather than as a vague diff. Rows 1
#: and 3 carry a form widget; rows 2 and 4 are label-and-rule only.
LINKED_ROWS: list[tuple[str, int, str | None]] = [
    ("1. TYPE OF SUBMISSION", 650, "TypeOfSubmission"),
    ("2. DATE SUBMITTED", 610, None),
    ("3. APPLICANT NAME", 570, "ApplicantName"),
    ("4. PROJECT TITLE", 530, None),
]

#: Left edge of the callout column, clear of the widest field label.
LINKED_CALLOUT_X = 410


def _callout_annotation_class():
    """`FreeTextAnnotation` that permits `/CL`.

    ReportLab validates annotation keys against an allow-list that predates callout lines,
    so the key has to be permitted explicitly. Everything else about the annotation is
    ReportLab's own.
    """
    from reportlab.pdfbase import pdfdoc

    class CalloutFreeTextAnnotation(pdfdoc.FreeTextAnnotation):
        permitted = pdfdoc.FreeTextAnnotation.permitted + ("CL",)

    return CalloutFreeTextAnnotation


def linked_form(path: Path) -> None:
    """A form whose callouts can be bound to the fields they describe.

    Position alone cannot say which field a margin callout belongs to once a page is dense:
    on a real form the callouts stack up in one column and the fields they point at do not.
    This fixture carries the three things that can say it, plus the case where nothing can:

    * a callout line (`/CL`) whose tip lands inside a form widget -- the annotator's own
      statement of the link, and exact,
    * a callout line whose tip lands on a printed label instead,
    * a callout with no line at all, level with a widget,
    * a callout with no line, level with a label that has no widget,
    * a callout sitting in empty space, which must stay unbound rather than acquire a
      plausible-looking neighbour.
    """
    from reportlab.pdfbase import pdfdoc

    canvas = _canvas(path)
    callout_annotation = _callout_annotation_class()

    def callout(contents: str, y: int, tip: tuple[int, int] | None = None) -> None:
        extra = {}
        if tip is not None:
            # Knee then tip: the elbow sits at the annotation's own left edge.
            extra["CL"] = pdfdoc.PDFArray(
                [LINKED_CALLOUT_X, y + 9, (LINKED_CALLOUT_X + tip[0]) // 2, tip[1], *tip]
            )
        canvas._addAnnotation(
            callout_annotation(
                (LINKED_CALLOUT_X, y, LINKED_CALLOUT_X + 150, y + 18),
                contents,
                "/Helv 9 Tf 0 g",
                **extra,
            ),
            None,
            1,
        )

    _draw_heading(canvas, "Grant Application Cover Page", 720)
    canvas.setFont("Helvetica", 11)
    for label, y, field in LINKED_ROWS:
        canvas.drawString(72, y, label)
        if field is None:
            canvas.line(230, y - 4, 350, y - 4)
        else:
            canvas.acroForm.textfield(
                name=field, x=230, y=y - 6, width=120, height=18,
                borderStyle="solid", forceBorder=True, fontName="Helvetica", fontSize=9,
            )

    # Added bottom-up, as in `annotated_form`: source order must not be reading order.
    callout("General guidance is in the programme announcement.", 200)
    callout("Limited to 200 characters.", 524, tip=(120, 533))
    callout("Must match the name registered with the agency.", 566)
    callout("Format: MM/DD/YYYY.", 606)
    callout("Use Application for the first submission attempt.", 646, tip=(290, 653))

    canvas.showPage()
    canvas.save()


#: `ruled_form.pdf` geometry, in ReportLab's bottom-up user space. A three-column grid with
#: a header row and two field rows, ruled on every edge so pdfplumber finds it as a table.
RULED_COLUMNS = (72, 200, 300, 420)
RULED_ROW_LINES = (640, 610, 580, 550)
RULED_CELLS: list[list[str]] = [
    ["Field", "Value", "Notes"],
    ["1. TYPE OF SUBMISSION", "", ""],
    ["2. DATE SUBMITTED", "", ""],
]


def ruled_form(path: Path) -> None:
    """A ruled form whose callouts point into table cells.

    The case the linking cascade could not see. Words inside a ruled table are handed to the
    table renderer and removed from the page's body lines, so a callout aimed at a form field
    -- which on a real form is a table cell -- found nothing under its tip and fell through
    to an inference or to nothing at all.

    Both cells that get pointed at here are the two shapes that matter: one carrying the
    field label, and one *empty*, which is what a blank form field actually is.
    """
    from reportlab.pdfbase import pdfdoc

    canvas = _canvas(path)
    callout_annotation = _callout_annotation_class()

    y = _draw_heading(canvas, "Ruled Application Form", 760)
    # Enough prose to clear MIN_CHARS_PER_PAGE. Without it the page triages as `needs_ocr`
    # and the golden quietly records the stub path instead of the geometry path this fixture
    # exists to exercise -- green, and testing nothing.
    _draw_paragraph(canvas, BODY_TEXT, y - 6)

    top, bottom = RULED_ROW_LINES[0], RULED_ROW_LINES[-1]
    for x in RULED_COLUMNS:
        canvas.line(x, bottom, x, top)
    for y in RULED_ROW_LINES:
        canvas.line(RULED_COLUMNS[0], y, RULED_COLUMNS[-1], y)

    canvas.setFont("Helvetica", 9)
    for row_index, row in enumerate(RULED_CELLS):
        baseline = RULED_ROW_LINES[row_index + 1] + 10
        for column_index, text in enumerate(row):
            if text:
                canvas.drawString(RULED_COLUMNS[column_index] + 4, baseline, text)

    def callout(contents: str, y: int, tip: tuple[int, int]) -> None:
        canvas._addAnnotation(
            callout_annotation(
                (450, y, 600, y + 18),
                contents,
                "/Helv 9 Tf 0 g",
                CL=pdfdoc.PDFArray([450, y + 9, (450 + tip[0]) // 2, tip[1], *tip]),
            ),
            None,
            1,
        )

    # Into the cell that carries the label, and into an empty cell two rows down.
    callout("Use Application for the first submission attempt.", 586, tip=(130, 595))
    callout("Format: MM/DD/YYYY.", 556, tip=(250, 565))

    canvas.showPage()
    canvas.save()


def screenshot_form(path: Path) -> None:
    """A form that is a *picture* of a form, with typed callouts around it.

    The shape that defeats every coverage metric at once. The form itself is a raster
    screenshot, so none of its rows, cells or values are in the text layer and no table
    extraction can reach them. What *is* in the text layer -- a heading and a handful of
    callouts someone typed alongside -- is real text, enough of it to clear
    MIN_CHARS_PER_PAGE and score a clean alpha ratio. The document therefore triages `clean`,
    converts without a warning, and silently omits the entire form.
    """
    from reportlab.lib.utils import ImageReader

    canvas = _canvas(path)
    y = _draw_heading(canvas, "Sample Budget - Annotated", 740)

    image = _text_page_image(
        "SECTION B - BUDGET CATEGORIES\n\n"
        "6. Object Class Categories        Federal        Non-Federal\n"
        "   a. Personnel                   250,000        125,000\n"
        "   b. Fringe Benefits              45,000         22,500\n"
        "   c. Travel                       12,000          6,000",
        size=(1188, 480),
    )
    # Sized to match a real specimen: roughly a third of the page, which is what a
    # full-width screenshot of a form comes to once it is placed under a heading.
    canvas.drawImage(ImageReader(image), 56, y - 360, width=500, height=330)

    canvas.setFont("Helvetica", 9)
    for offset, text in enumerate(
        (
            "Enter only estimated Federal funds in this column.",
            "Used 18% rate against Personnel.",
            "Estimated program income - use previous reports to estimate.",
        )
    ):
        canvas.drawString(72, y - 390 - offset * 14, text)

    canvas.showPage()
    canvas.save()


#: `boxed_notes.pdf`. Three boxes side by side on one baseline, which is the arrangement that
#: destroys a page: line grouping merges them into a single run-on line, interleaved word by
#: word, and the result is unreadable and unsearchable.
BOXED_ROW = (
    (72, "Federal funds carry over"),
    (232, "Matching funds carry over"),
    (392, "Amount from Appendix A"),
)


def boxed_notes(path: Path) -> None:
    """Text set in drawn boxes, as a marked-up form does it.

    The specimen: a form screenshot with commentary typed into filled, stroked boxes placed
    around and over it. Three things go wrong at once without special handling. The boxes'
    text is indistinguishable from the form's own words, so a reader cannot tell commentary
    from content. Boxes sharing a baseline merge into one interleaved line. And a box's text
    is set apart visually in a way nothing in the markdown records.

    The ruled table at the bottom is the false positive to avoid: a shaded header cell is a
    filled rect containing text, and it is not commentary.
    """
    canvas = _canvas(path)

    y = _draw_heading(canvas, "Capacity Budget - Annotated", 740)
    y = _draw_paragraph(canvas, BODY_TEXT, y - 6)

    def box(x: int, top: int, width: int, height: int, lines: list[str]) -> None:
        canvas.setStrokeColorRGB(0.2, 0.3, 0.7)
        canvas.setFillColorRGB(0.93, 0.95, 1.0)
        canvas.rect(x, top - height, width, height, stroke=1, fill=1)
        canvas.setFillColorRGB(0, 0, 0)
        canvas.setFont("Helvetica", 8)
        for index, text in enumerate(lines):
            canvas.drawString(x + 4, top - 12 - index * 10, text)

    row_top = y - 40
    for x, text in BOXED_ROW:
        box(x, row_top, 148, 24, [text])

    box(72, row_top - 50, 300, 34, ["Required match - note, this amount", "accounts for a waiver."])

    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(72, row_top - 110, "Continue with the instructions on the next page.")

    # A ruled table whose header cell is shaded: a filled rect with text in it that is not a
    # note. Boxes inside a table must stay part of the table.
    table_top, table_bottom = row_top - 140, row_top - 200
    columns = (72, 190, 310)
    canvas.setFillColorRGB(0.88, 0.88, 0.88)
    canvas.rect(72, table_top - 20, 358, 20, stroke=0, fill=1)
    canvas.setFillColorRGB(0, 0, 0)
    for x in (*columns, 430):
        canvas.line(x, table_bottom, x, table_top)
    for row_y in (table_top, table_top - 20, table_top - 40, table_bottom):
        canvas.line(72, row_y, 430, row_y)
    canvas.setFont("Helvetica", 9)
    for row_index, row in enumerate((("Category", "Federal", "Match"),
                                     ("Personnel", "250,000", "125,000"),
                                     ("Travel", "12,000", "6,000"))):
        for x, cell in zip(columns, row):
            canvas.drawString(x + 4, table_top - 14 - row_index * 20, cell)

    canvas.showPage()
    canvas.save()


def annotated_form(path: Path) -> None:
    """A form carrying FreeText annotations, as if someone marked it up in Preview.

    This is the real shape of an institutional "how to fill this in" document: a blank form
    plus callouts drawn on top telling you what goes where. The callouts are annotation
    objects, NOT page content, so no text-layer extraction sees them -- which is exactly the
    regression this fixture exists to catch. Note the annotations are deliberately placed out
    of source order (the last one added sits highest on the page) so the golden proves they
    are emitted by position rather than by the order the PDF happens to store them in.
    """
    canvas = _canvas(path)

    y = _draw_heading(canvas, "Funding Request Form", 720)
    y = _draw_paragraph(canvas, BODY_TEXT, y - 6)
    canvas.setFont("Helvetica", 11)
    for label, offset in (("Requester:", 40), ("Fiscal year:", 70), ("Amount:", 100)):
        canvas.drawString(72, y - offset, label)
    canvas.line(150, y - 44, 400, y - 44)
    canvas.line(150, y - 74, 400, y - 74)
    canvas.line(150, y - 104, 400, y - 104)

    # Added bottom-up on purpose: source order is the reverse of reading order.
    canvas.freeTextAnnotation(
        Rect=(410, y - 112, 560, y - 92),
        contents="enter the TOTAL REQUEST AMOUNT",
        DA="/Helv 9 Tf 0 g",
    )
    canvas.freeTextAnnotation(
        Rect=(410, y - 82, 560, y - 62),
        contents="select the appropriate FISCAL YEAR",
        DA="/Helv 9 Tf 0 g",
    )
    canvas.freeTextAnnotation(
        Rect=(410, y - 52, 560, y - 32),
        contents="YOUR NAME\rnot your supervisor's",
        DA="/Helv 9 Tf 0 g",
    )
    canvas.showPage()
    canvas.save()


#: `callout_notes.pdf` geometry. The callout sits at one x for markers *and* for the
#: unmarked lines that continue them, exactly as the real document does -- so indentation
#: cannot be what tells a continuation from a new block.
CALLOUT_X = 108
CALLOUT_INDENT = 28
CALLOUT_SIZE = 12
CALLOUT_LEADING = 16
CALLOUT_BODY_SIZE = 9.5

#: The subhead is set 1.10x body: larger than body text, and still not a heading.
SUBHEAD_SIZE = 10.5

#: Lines of the "NOTES:" callout as `(indent, [(font, text), ...])`.
#:
#: Two things are deliberate. The sub-bullet is indented while its own continuation line is
#: not, so a list has to be reconstructed from markers and spacing rather than from x alone.
#: And "Submit" is drawn as "S" in one font followed by "ubmit" in another, with no gap
#: between them -- which is what a subset-font split looks like in a real PDF, and what
#: makes `extract_words` report one word as two.
CALLOUT_LINES: list[tuple[int, list[tuple[str, str]]]] = [
    (0, [("Helvetica", "NOTES:")]),
    (
        0,
        [
            ("Helvetica", "- "),
            ("Helvetica-Oblique", "S"),
            ("Helvetica", "ubmit SEPARATE FUNDING REQUESTS for EACH VENDOR"),
        ],
    ),
    (0, [("Helvetica", "- INCLUDE SOW, CONTRACT, SIGNED IGCE, 7600A (if")]),
    (0, [("Helvetica", "applicable), MIPR INSTRUCTIONS (if applicable)")]),
    (CALLOUT_INDENT, [("Helvetica", "* 7600A required for Reimbursable MIPRs, and")]),
    (CALLOUT_INDENT, [("Helvetica", "if the receiving office requires one")]),
]

#: Three wrapped sentences at callout size. Each line on its own is short enough to pass for
#: a heading; together they are plainly a paragraph, which is the distinction the converter
#: has to make.
CALLOUT_PROSE = [
    "This callout is a wrapped run of ordinary sentences, set two",
    "points larger than the body text around it, which makes it",
    "emphasis rather than a section heading.",
]

#: A second list, far enough below the prose that the gap alone ends the first one.
CALLOUT_POINTERS = [
    "- see page 4 for sample IGCEs",
    "- see page 5 for info going into 7600A",
]


def _draw_segments(canvas, x: float, y: float, segments, size: float) -> None:
    """Draw `(font, text)` runs end to end, advancing x by each run's measured width."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    for font, text in segments:
        canvas.setFont(font, size)
        canvas.drawString(x, y, text)
        x += stringWidth(text, font, size)


def callout_notes(path: Path) -> None:
    """A page whose emphasis is all set larger than body text, with a bulleted callout.

    This is the shape of a form tutorial, and it is the one geometry gets wrong by default:
    body text is the smallest type on the page, so a label, a callout and a bullet are all
    "bigger than body" and all read as headings. The fixture exists to pin down that being
    bigger than body text is not on its own enough to make a line a heading, that a bulleted
    block stays a list, and that a word split across two font subsets comes back as one word.
    """
    canvas = _canvas(path)

    y = _draw_heading(canvas, "OTA Funding Request Tutorial", 720)
    canvas.setFont("Helvetica", SUBHEAD_SIZE)
    canvas.drawString(72, y, "Request for contracting action")

    y = _draw_paragraph(canvas, BODY_TEXT, y - 34, size=CALLOUT_BODY_SIZE)

    y -= 20
    for indent, segments in CALLOUT_LINES:
        _draw_segments(canvas, CALLOUT_X + indent, y, segments, CALLOUT_SIZE)
        y -= CALLOUT_LEADING

    y -= 24
    for text in CALLOUT_PROSE:
        _draw_segments(canvas, CALLOUT_X, y, [("Helvetica", text)], CALLOUT_SIZE)
        y -= CALLOUT_LEADING

    y -= 24
    for text in CALLOUT_POINTERS:
        _draw_segments(canvas, CALLOUT_X, y, [("Helvetica", text)], CALLOUT_SIZE)
        y -= CALLOUT_LEADING

    # Body text has to be the most-set size on the page or it is not the body: without this
    # second paragraph the callout outweighs it and the callout becomes the baseline, which
    # would make the fixture prove the opposite of what it is for. It also puts ordinary
    # prose directly after the last bullet, where a list that does not know how to end
    # would swallow it.
    _draw_paragraph(canvas, BODY_TEXT, y - 24, size=CALLOUT_BODY_SIZE)

    canvas.showPage()
    canvas.save()


#: A 16:9 slide, in points, as a deck exported from PowerPoint measures one.
SLIDE_PAGE = (960, 540)

#: Sizes for `slide_table`, taken from the page they reproduce: body prose is the *smallest*
#: type on the slide and every cell of the table is set larger than it, so size alone cannot
#: separate the two.
SLIDE_BODY_SIZE = 18
SLIDE_TITLE_SIZE = 44
SLIDE_HEADER_SIZE = 20
SLIDE_VALUE_SIZE = 24
SLIDE_YEAR_SIZE = 28

#: `(year, single, family)` per row of the slide's table.
SLIDE_TABLE_ROWS = [("2025", "$4,300", "$8,550"), ("2026", "$4,400", "$8,750")]

#: The callout beside the table, in the slide's right-hand column. Its last line shares a
#: baseline with the table's second row, so grouping words by baseline across the whole page
#: welds the two together: the real deck produced the cell "2026 2026!", which is text
#: neither column contains.
SLIDE_CALLOUT = [
    "There is still time to max",
    "out your account for the",
    "earlier year. Contributions",
    "count up to the deadline.",
    "Ask before April 15,",
    "2026!",
]

#: Prose to outweigh the table, so that 18pt is the document's body size. Without it the
#: largest of the cell sizes becomes the baseline and the fixture proves nothing.
SLIDE_BODY_TEXT = (
    "Account holders may contribute up to the annual maximum set by the Internal Revenue "
    "Service. The limit depends on whether the account holder is enrolled in single "
    "coverage or in family coverage, and it is published again each calendar year. "
    "Contributions made after the end of a year but before the filing deadline may be "
    "applied to the earlier year."
)


def slide_table(path: Path) -> None:
    """A presentation slide whose table is drawn with type and whitespace alone.

    The shape that broke an HSA deck: a small table of contribution limits by year and plan
    type, with a rule above and below it and no vertical rules at all, so pdfplumber finds
    no table. Every cell is set larger than the slide's body prose and most of them in a
    bold face, so each one clears both of the heading tests on its own, and the table came
    out as a run of `###` lines that cut the slide into eight sections.

    Two further details are deliberate, because both are what the real slide does and both
    defeat the borderless-table recovery that would otherwise catch this:

    * the year cell is set larger than the value cells beside it, so its top sits more than
      `PDF_LINE_TOLERANCE` from theirs and the row does not group into one line;
    * the header is stacked as two lines ("SINGLE" / "PLAN"), so no three consecutive lines
      share a cell count.

    Nothing here is recoverable as a grid by measurement. What the converter must not do is
    assert section boundaries in its place.
    """
    canvas = _canvas(path, pagesize=SLIDE_PAGE)
    height = SLIDE_PAGE[1]

    canvas.setFont("Helvetica-Bold", SLIDE_TITLE_SIZE)
    canvas.drawString(46, height - 104, "IRS Contribution Limits")

    single_x, family_x, year_x, label_x = 341, 477, 231, 46

    canvas.setFont("Helvetica-Bold", SLIDE_HEADER_SIZE)
    for index, text in enumerate(("SINGLE", "PLAN")):
        canvas.drawString(single_x, height - 209 - index * 24, text)
    for index, text in enumerate(("FAMILY", "PLAN")):
        canvas.drawString(family_x, height - 209 - index * 24, text)

    # The rules a reader sees as the table. Horizontal only: with no verticals pdfplumber
    # will not call this a table, which is the whole point of the fixture.
    canvas.setLineWidth(1)
    canvas.line(45, height - 246, 549, height - 246)
    canvas.line(45, height - 451, 555, height - 451)

    canvas.setFont("Helvetica", SLIDE_BODY_SIZE)
    canvas.drawString(label_x, height - 349, "Maximum")
    canvas.drawString(label_x, height - 371, "contribution limit")

    for index, (year, single, family) in enumerate(SLIDE_TABLE_ROWS):
        baseline = height - 316 - index * 98
        canvas.setFont("Helvetica-Bold", SLIDE_VALUE_SIZE)
        canvas.drawString(single_x + 4, baseline, single)
        canvas.drawString(family_x - 12, baseline, family)
        # Larger type, and a baseline low enough that the year does not group with the
        # values it belongs to. This is the offset the real slide has.
        canvas.setFont("Helvetica-Bold", SLIDE_YEAR_SIZE)
        canvas.drawString(year_x, baseline - 7, year)

    # The right-hand column, across a gutter wide enough to be one. Its baselines are set so
    # that the last line lands level with the table's second row.
    canvas.setFont("Helvetica-Bold", SLIDE_VALUE_SIZE)
    canvas.drawString(622, height - 246, "Don't Miss Out!")
    for index, text in enumerate(SLIDE_CALLOUT):
        canvas.setFont(
            "Helvetica-Bold" if text.endswith("!") else "Helvetica", SLIDE_BODY_SIZE
        )
        canvas.drawString(622, height - 300 - index * 23, text)

    canvas.showPage()

    canvas.setFont("Helvetica-Bold", SLIDE_TITLE_SIZE)
    canvas.drawString(46, height - 104, "How The Limit Is Set")
    canvas.setFont("Helvetica", SLIDE_BODY_SIZE)
    y = height - 180
    words, line = SLIDE_BODY_TEXT.split(), []
    for word in words:
        line.append(word)
        if len(" ".join(line)) > 70:
            canvas.drawString(46, y, " ".join(line[:-1]))
            y -= 28
            line = [word]
    if line:
        canvas.drawString(46, y, " ".join(line))
    canvas.showPage()
    canvas.save()


#: `banded_table` rows: label, count, share. Alternate rows are shaded.
BANDED_TABLE_ROWS = [
    ("age", "number", "percent"),
    ("<22", "21", "0.1%"),
    ("22", "398", "1.9%"),
    ("23", "859", "4.2%"),
    ("24", "1,175", "5.7%"),
    ("25", "1,136", "5.5%"),
    ("Total", "3,589", "100%"),
]


def banded_table(path: Path) -> None:
    """A three-column table whose shading, not its grid, carries the vertical rules.

    The shape an almanac page produced. The two inner column rules run the full height of
    the table, but the outer left and right edges exist only as the sides of the box drawn
    around each *shaded* row. A cell exists for pdfplumber only where a rule bounds it on
    every side, so on the unshaded rows the first and last cells had nothing outboard of
    them, came back as `None`, and their text was dropped outright -- every second row
    keeping its middle number and losing both its label and its percentage.

    That is the dangerous failure mode: the numbers survive, unlabelled, and the table still
    reads as a table. Every row must keep all three of its cells.
    """
    canvas = _canvas(path)
    left, right = 100.0, 420.0
    inner = (220.0, 330.0)
    row_height = 22.0
    top = 640.0

    canvas.setLineWidth(0.75)
    for index in range(len(BANDED_TABLE_ROWS)):
        row_top = top - index * row_height
        if index % 2:
            # The shaded band, stroked as well as filled: this box is the only thing that
            # puts a vertical rule at `left` and at `right`, and only across its own row.
            canvas.setFillGray(0.9)
            canvas.rect(left, row_top - row_height, right - left, row_height, stroke=1, fill=1)
            canvas.setFillGray(0.0)

    # The full-height column rules. Nothing else spans the table vertically.
    for x in inner:
        canvas.line(x, top - len(BANDED_TABLE_ROWS) * row_height, x, top)

    for index, row in enumerate(BANDED_TABLE_ROWS):
        baseline = top - index * row_height - 15
        canvas.setFont("Helvetica-Bold" if index == 0 else "Helvetica", 9)
        for x, cell in zip((left + 8, inner[0] + 8, inner[1] + 8), row):
            canvas.drawString(x, baseline, cell)

    canvas.showPage()
    canvas.save()


def simple_docx(path: Path) -> None:
    """Headings, a list, and a table."""
    from docx import Document

    document = Document()
    document.add_heading("Travel Reimbursement Handbook", level=1)
    document.add_paragraph(BODY_TEXT)

    document.add_heading("Allowable Expenses", level=2)
    for item in ("Lodging", "Meals", "Mileage"):
        document.add_paragraph(item, style="List Bullet")

    table = document.add_table(rows=len(TABLE_ROWS), cols=len(TABLE_ROWS[0]))
    for row_index, row in enumerate(TABLE_ROWS):
        for column_index, cell in enumerate(row):
            table.cell(row_index, column_index).text = cell

    properties = document.core_properties
    properties.created = EPOCH.replace(tzinfo=None)
    properties.modified = EPOCH.replace(tzinfo=None)
    properties.title = "Travel Reimbursement Handbook"
    properties.author = "fixture"
    properties.last_modified_by = "fixture"
    properties.revision = 1

    document.save(str(path))
    _normalise_zip(path)


#: `tables_and_image.docx` table content. Two separate tables so the provenance test can
#: assert on "exactly two `table` blocks" rather than on one table split awkwardly in two.
ROOM_TABLE_ROWS = [
    ["Room", "Capacity", "Building", "Booking lead time"],
    ["Alder", "8", "North", "24 hours"],
    ["Birch", "20", "South", "48 hours"],
]

EQUIPMENT_TABLE_ROWS = [
    ["Item", "Location", "Contact"],
    ["Projector", "Supply closet", "facilities@example.org"],
    ["Whiteboard cart", "Supply closet", "facilities@example.org"],
]


def _floor_plan_thumbnail_bytes() -> BytesIO:
    """A tiny, deterministic PNG for the embedded-picture fixture.

    6x6 rather than the 4x4 the addendum names: docling's `MsWordDocumentBackend` treats any
    picture at or under `SPACER_IMAGE_AREA_THRESHOLD` (25px^2) as an invisible layout spacer
    and drops it from the converted document entirely (verified against the installed
    docling version -- a 4x4, 16px^2 image produces zero `PictureItem`s). 6x6 = 36px^2 clears
    the threshold while staying a trivial, committable size. PIL's PNG writer embeds no
    timestamp, so this is byte-stable across runs.
    """
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (6, 6), (176, 44, 44)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def tables_and_image_docx(path: Path) -> None:
    """A title, two heading levels, a list, two tables, and one embedded picture.

    Exercises the DOCX provenance walk end to end: headings at two distinct levels, a
    bulleted list, two separate tables (so list/table grouping and block counting both have
    something to prove), and one embedded picture, which must produce exactly one `picture`
    block and the document-level INCOMPLETE note.
    """
    from docx import Document

    document = Document()
    document.add_heading("Facilities Reference Guide", level=0)
    document.add_heading("Conference Rooms", level=1)
    document.add_heading("Booking Policy", level=2)
    document.add_paragraph(BODY_TEXT)

    for item in (
        "Reserve at least 24 hours ahead",
        "Cancel unused holds by 9am so others can book",
        "Report AV issues to facilities, not the room's occupant",
    ):
        document.add_paragraph(item, style="List Bullet")

    room_table = document.add_table(rows=len(ROOM_TABLE_ROWS), cols=len(ROOM_TABLE_ROWS[0]))
    for row_index, row in enumerate(ROOM_TABLE_ROWS):
        for column_index, cell in enumerate(row):
            room_table.cell(row_index, column_index).text = cell

    document.add_paragraph("Shared equipment is booked separately from the rooms themselves.")

    equipment_table = document.add_table(
        rows=len(EQUIPMENT_TABLE_ROWS), cols=len(EQUIPMENT_TABLE_ROWS[0])
    )
    for row_index, row in enumerate(EQUIPMENT_TABLE_ROWS):
        for column_index, cell in enumerate(row):
            equipment_table.cell(row_index, column_index).text = cell

    picture_paragraph = document.add_paragraph()
    picture_paragraph.add_run().add_picture(_floor_plan_thumbnail_bytes())

    properties = document.core_properties
    properties.created = EPOCH.replace(tzinfo=None)
    properties.modified = EPOCH.replace(tzinfo=None)
    properties.title = "Facilities Reference Guide"
    properties.author = "fixture"
    properties.last_modified_by = "fixture"
    properties.revision = 1

    document.save(str(path))
    _normalise_zip(path)


def _normalise_zip(path: Path) -> None:
    """Repack a zip with fixed timestamps and sorted entries, so bytes are reproducible."""
    with zipfile.ZipFile(path) as archive:
        entries = sorted(archive.namelist())
        payload = {name: archive.read(name) for name in entries}

    temporary = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in entries:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload[name])
    shutil.move(str(temporary), str(path))


def reference_table_csv(path: Path) -> None:
    """8 data rows, 4 columns — comfortably inside the guardrails."""
    rows = [["Code", "Expense category", "Limit", "Receipt required"]]
    data = [
        ("LDG", "Lodging", "$180 / night", "Yes"),
        ("MEA", "Meals and incidentals", "$74 / day", "No"),
        ("MIL", "Mileage, personal vehicle", "$0.67 / mile", "No"),
        ("AIR", "Airfare, coach", "Actual cost", "Yes"),
        ("RAI", "Rail, coach", "Actual cost", "Yes"),
        ("PRK", "Parking and tolls", "Actual cost", "Yes"),
        ("TAX", "Taxi or rideshare", "Actual cost", "Yes"),
        ("REG", "Conference registration | fees", "Actual cost", "Yes"),
    ]
    rows.extend(list(row) for row in data)
    _write_csv(path, rows)


def too_big_csv(path: Path) -> None:
    """400 data rows, to assert the guardrail raises rather than emitting a useless table."""
    rows = [["Row", "Code", "Description"]]
    rows.extend([str(index), f"C{index:04d}", f"Line item {index}"] for index in range(400))
    _write_csv(path, rows)


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)


def xml_comment_docx(path: Path) -> None:
    """A Word body carrying XML comment and processing-instruction nodes between paragraphs.

    Regulation publishing systems emit these (a `<!--Topic ...-->` marker before each
    section). Docling's Word backend walks every body child and asks lxml for its tag name;
    a comment node has none, so the walk raised `ValueError: Invalid input tag`. The fixture
    exists so that the converter is proven to strip such nodes before the walk.
    """
    from docx import Document
    from lxml import etree

    document = Document()
    document.add_heading("Supplement Part 201", level=1)
    document.add_paragraph(BODY_TEXT)
    document.add_heading("201.1 Purpose", level=2)
    document.add_paragraph(BODY_TEXT)

    body = document.element.body
    paragraphs = list(body)
    body.insert(0, etree.Comment("Topic unique_1"))
    body.insert(body.index(paragraphs[2]), etree.Comment("Topic unique_2"))
    body.insert(body.index(paragraphs[3]), etree.ProcessingInstruction("publisher", "marker"))
    # Inside a paragraph too, and in a header part: Docling walks headers and footers with
    # the same tag-name lookup, and a nested comment must not survive either.
    paragraphs[1].insert(0, etree.Comment("inline marker"))
    header = document.sections[0].header
    header.paragraphs[0].text = "Supplement running header"
    header._element.insert(0, etree.Comment("Topic header_1"))

    properties = document.core_properties
    properties.created = EPOCH.replace(tzinfo=None)
    properties.modified = EPOCH.replace(tzinfo=None)
    properties.title = "Supplement Part 201"
    properties.author = "fixture"
    properties.last_modified_by = "fixture"
    properties.revision = 1

    document.save(str(path))
    _normalise_zip(path)


def dangling_rels_docx(path: Path) -> None:
    """A Word package whose relationships file points at parts that do not exist.

    Seen in the wild on a regulation supplement: an image relationship whose target is the
    `media/` directory, plus references to files never packaged. Word opens such files
    without complaint; python-docx tries to load every relationship target as a part and
    fails. The fixture carries one dangling directory target, one dangling missing-file
    target, and one real inline image, so the test proves the real image still converts.
    """
    from docx import Document
    from docx.shared import Inches

    document = Document()
    document.add_heading("Procedures Part 252", level=1)
    document.add_paragraph(BODY_TEXT)
    document.add_picture(_floor_plan_thumbnail_bytes(), width=Inches(0.5))
    document.add_paragraph(BODY_TEXT)

    properties = document.core_properties
    properties.created = EPOCH.replace(tzinfo=None)
    properties.modified = EPOCH.replace(tzinfo=None)
    properties.title = "Procedures Part 252"
    properties.author = "fixture"
    properties.last_modified_by = "fixture"
    properties.revision = 1
    document.save(str(path))

    # Post-process the package: add the dangling relationships and a directory entry, the
    # way the publishing tool that produced the original did.
    with zipfile.ZipFile(path) as archive:
        entries = archive.namelist()
        payload = {name: archive.read(name) for name in entries}
    rels = payload["word/_rels/document.xml.rels"].decode("utf-8")
    dangling = (
        '<Relationship Id="rId901" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/image" Target="media/"/>'
        '<Relationship Id="rId902" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/image" Target="media/Graphics/never-packaged.gif"/>'
    )
    payload["word/_rels/document.xml.rels"] = rels.replace(
        "</Relationships>", dangling + "</Relationships>"
    ).encode("utf-8")
    payload["word/media/"] = b""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(payload):
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload[name])


def toc_page(path: Path) -> None:
    """A table-of-contents page with dotted leaders, then one body page.

    Real handbooks set each entry as "title <dots> page" on its own line. The geometry
    engine joins those lines into one paragraph, which is what a real 2023 report produced:
    a single block of 3,900 characters, 3,000 of them dots. The normaliser must turn that
    into one entry per line with the leader collapsed.
    """
    canvas = _canvas(path)
    y = _draw_heading(canvas, "Table of Contents", 720)
    canvas.setFont("Helvetica", 11)
    y -= 24
    entries = [("Section 1 Governance", "5"), ("1.1 Purpose", "5"), ("1.2 Scope", "6"),
               ("Section 2 Travel", "9"), ("2.1 Lodging", "9"), ("2.2 Meals", "11"),
               ("2.3 Mileage", "12"), ("Section 3 Claims", "14"), ("3.1 Receipts", "14"),
               ("3.2 Approval", "15"), ("Appendix A Forms", "iv"), ("Appendix B Contacts", "vi")]
    for title, page in entries:
        dots = "." * max(4, 70 - len(title) - len(page))
        canvas.drawString(72, y, f"{title} {dots} {page}")
        y -= 16
    canvas.showPage()

    y = _draw_heading(canvas, "Section 1 Governance", 720)
    _draw_paragraph(canvas, BODY_TEXT, y - 6)
    canvas.showPage()
    canvas.save()


GENERATORS = {
    "born_digital.pdf": born_digital,
    "toc_page.pdf": toc_page,
    "image_only.pdf": image_only,
    "mixed.pdf": mixed,
    "mojibake.pdf": mojibake,
    "simple.docx": simple_docx,
    "tables_and_image.docx": tables_and_image_docx,
    "xml_comment.docx": xml_comment_docx,
    "dangling_rels.docx": dangling_rels_docx,
    "reference_table.csv": reference_table_csv,
    "too_big.csv": too_big_csv,
    "annotated_form.pdf": annotated_form,
    "linked_form.pdf": linked_form,
    "ruled_form.pdf": ruled_form,
    "screenshot_form.pdf": screenshot_form,
    "boxed_notes.pdf": boxed_notes,
    "callout_notes.pdf": callout_notes,
    "slide_table.pdf": slide_table,
    "banded_table.pdf": banded_table,
}


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, generator in GENERATORS.items():
        target = FIXTURES / name
        generator(target)
        print(f"  wrote {target.relative_to(FIXTURES.parent.parent)} "
              f"({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
