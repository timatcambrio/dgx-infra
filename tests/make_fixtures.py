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


def _canvas(path: Path):
    from reportlab import rl_config

    # Must be set before the canvas is constructed: it fixes both the /CreationDate and the
    # /ID, which are otherwise clock- and random-seeded.
    rl_config.invariant = 1

    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen.canvas import Canvas

    return Canvas(str(path), pagesize=LETTER, invariant=1)


def _draw_heading(canvas, text: str, y: int) -> int:
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(72, y, text)
    return y - 28


def _draw_paragraph(canvas, text: str, y: int, width: int = 78) -> int:
    canvas.setFont("Helvetica", 11)
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


GENERATORS = {
    "born_digital.pdf": born_digital,
    "image_only.pdf": image_only,
    "mixed.pdf": mixed,
    "mojibake.pdf": mojibake,
    "simple.docx": simple_docx,
    "reference_table.csv": reference_table_csv,
    "too_big.csv": too_big_csv,
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
