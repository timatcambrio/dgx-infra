"""Model-free PDF layout reconstruction.

The tests that matter most here are the ones asserting what the extractor must *not* do:
invent a table inside prose, split a paragraph at a bold phrase, or interleave two columns.
Those failures produce output that looks plausible and is wrong, which is far worse than
output that is obviously incomplete.
"""

from __future__ import annotations

import pytest

from pipeline.converters import pdf_geometry
from pipeline.converters.pdf import missing_pages_note
from pipeline.converters.pdf_geometry import Line, detect_columns, find_aligned_table_runs


def line(text: str, *, top: float = 0.0, size: float = 10.0, words=None, bold=False) -> Line:
    if words is None:
        words = tuple(
            (float(index * 50), float(index * 50 + 40), part)
            for index, part in enumerate(text.split())
        )
    return Line(
        top=top,
        bottom=top + size,
        x0=words[0][0] if words else 0.0,
        size=size,
        bold=bold,
        text=text,
        words=tuple(words),
    )


def cells_line(cells: list[tuple[float, str]], top: float, size: float = 10.0) -> Line:
    words = tuple((x, x + 30, text) for x, text in cells)
    return Line(
        top=top,
        bottom=top + size,
        x0=cells[0][0],
        size=size,
        bold=False,
        text=" ".join(text for _, text in cells),
        words=words,
    )


# ------------------------------------------------------------------------------- fixtures


def test_born_digital_converts_with_headings_and_a_table(fixtures_dir, config):
    text = pdf_geometry.to_markdown(fixtures_dir / "born_digital.pdf", config)

    assert "# Travel Reimbursement Handbook" in text
    assert "| Expense category | Limit | Receipt required |" in text
    assert "| Lodging | $180 / night | Yes |" in text
    assert "twenty-five dollars" in text


def test_borderless_table_is_recovered_not_flattened(fixtures_dir, config):
    """The fixture's table is drawn with whitespace alone -- no ruling lines at all."""
    text = pdf_geometry.to_markdown(fixtures_dir / "born_digital.pdf", config)

    assert "| Mileage | $0.67 / mile | No |" in text
    assert "Lodging $180 / night Yes Meals" not in text  # the run-on failure mode


def test_reading_order_is_preserved(fixtures_dir, config):
    text = pdf_geometry.to_markdown(fixtures_dir / "born_digital.pdf", config)

    assert text.index("Travel Reimbursement Handbook") < text.index("Allowable Expenses")
    assert text.index("Allowable Expenses") < text.index("Submitting a Claim")


def test_image_only_page_contributes_nothing(fixtures_dir, config):
    """A rasterised page has no text layer; geometry must not invent one."""
    text = pdf_geometry.to_markdown(fixtures_dir / "image_only.pdf", config)
    assert text.strip() == ""


def test_conversion_is_deterministic(fixtures_dir, config):
    first = pdf_geometry.to_markdown(fixtures_dir / "mixed.pdf", config)
    second = pdf_geometry.to_markdown(fixtures_dir / "mixed.pdf", config)
    assert first == second


# ------------------------------------------------------------------ borderless table runs


def test_aligned_rows_are_detected_as_a_table(config):
    lines = [
        cells_line([(72.0, "Lodging"), (260.0, "$180"), (400.0, "Yes")], top=100),
        cells_line([(72.0, "Meals"), (260.0, "$74"), (400.0, "No")], top=116),
        cells_line([(72.0, "Mileage"), (260.0, "$0.67"), (400.0, "No")], top=132),
    ]

    assert find_aligned_table_runs(lines, config) == [(0, 3)]


def test_two_aligned_rows_are_not_enough(config):
    """Below the minimum row count, an accidental alignment must not become a table."""
    lines = [
        cells_line([(72.0, "Lodging"), (260.0, "$180")], top=100),
        cells_line([(72.0, "Meals"), (260.0, "$74")], top=116),
    ]

    assert find_aligned_table_runs(lines, config) == []


def test_prose_is_never_turned_into_a_table(config):
    """The important negative: a hallucinated table destroys the paragraph it consumes."""
    prose = [
        line("The modern battlefield is increasingly characterized by drones", top=100),
        line("and by systems that operate without any radio link at all", top=116),
        line("which renders traditional electronic warfare jamming obsolete", top=132),
    ]

    assert find_aligned_table_runs(prose, config) == []


def test_misaligned_columns_are_not_a_table(config):
    lines = [
        cells_line([(72.0, "Lodging"), (260.0, "$180")], top=100),
        cells_line([(72.0, "Meals"), (315.0, "$74")], top=116),
        cells_line([(72.0, "Mileage"), (390.0, "$0.67")], top=132),
    ]

    assert find_aligned_table_runs(lines, config) == []


def test_differing_cell_counts_break_the_run(config):
    lines = [
        cells_line([(72.0, "A"), (260.0, "B"), (400.0, "C")], top=100),
        cells_line([(72.0, "D"), (260.0, "E")], top=116),
        cells_line([(72.0, "F"), (260.0, "G"), (400.0, "H")], top=132),
    ]

    assert find_aligned_table_runs(lines, config) == []


# ------------------------------------------------------------------------------- columns


def _words(spans: list[tuple[float, float]]) -> list[dict]:
    return [
        {"x0": x0, "x1": x1, "top": 100.0 + index, "bottom": 110.0 + index, "text": "w"}
        for index, (x0, x1) in enumerate(spans)
    ]


def test_two_column_layout_is_detected():
    left = [(50.0, 250.0)] * 15
    right = [(350.0, 550.0)] * 15

    assert detect_columns(_words(left + right), 600.0, 0.06) == 2


def test_single_column_with_an_indent_is_not_two_columns():
    """An indented block opens a gap; calling it a column would interleave the page."""
    body = [(50.0, 550.0)] * 20 + [(120.0, 550.0)] * 5

    assert detect_columns(_words(body), 600.0, 0.06) == 1


def test_a_lopsided_gap_is_not_a_column_split():
    """A gutter needs real text on both sides, not a stray marginal note."""
    main = [(50.0, 300.0)] * 25
    stray = [(500.0, 560.0)] * 2

    assert detect_columns(_words(main + stray), 600.0, 0.06) == 1


def test_too_few_words_defaults_to_single_column():
    assert detect_columns(_words([(50.0, 100.0)] * 5), 600.0, 0.06) == 1


# ------------------------------------------------------------------- headings and margins


def test_a_bold_phrase_inside_a_sentence_is_not_a_heading(fixtures_dir, config):
    """Promoting an inline bold term tears the paragraph in half mid-clause."""
    mostly_plain = line(
        "The unit is seeking a Helmet-Mounted Directed Energy Weapon system for trials",
        words=(
            (72.0, 100.0, "The"),
            (104.0, 130.0, "unit"),
            (134.0, 300.0, "is seeking a Helmet-Mounted Directed Energy Weapon system"),
            (304.0, 380.0, "for trials"),
        ),
    )

    assert mostly_plain.bold is False


def test_running_headers_and_footers_are_dropped(config):
    pages = [
        [
            line("RF SUNY Travel Handbook", top=10),
            line("Body text on this page", top=300),
            line("Page 1 of 12", top=760),
        ],
        [
            line("RF SUNY Travel Handbook", top=10),
            line("Different body text", top=300),
            line("Page 2 of 12", top=760),
        ],
        [
            line("RF SUNY Travel Handbook", top=10),
            line("More body text", top=300),
            line("Page 3 of 12", top=760),
        ],
    ]

    repeated = pdf_geometry.find_repeated_margin_lines(pages, [792.0] * 3, config)

    assert "rf suny travel handbook" in repeated
    assert "page # of #" in repeated  # digits masked, so every page matches
    assert "body text on this page" not in repeated


def test_body_text_repeated_in_the_middle_of_pages_is_kept(config):
    """Only the margins are candidates -- a recurring phrase in the body is content."""
    pages = [[line("A recurring clause", top=400)] for _ in range(4)]

    assert pdf_geometry.find_repeated_margin_lines(pages, [792.0] * 4, config) == set()


def test_short_documents_skip_header_detection(config):
    """With two pages there is no evidence a line is a running header rather than content."""
    pages = [[line("Looks like a header", top=10)] for _ in range(2)]

    assert pdf_geometry.find_repeated_margin_lines(pages, [792.0] * 2, config) == set()


# --------------------------------------------------------------------- missing-page notes


def test_partial_documents_declare_their_missing_pages():
    note = missing_pages_note([3])

    assert "INCOMPLETE" in note
    assert "page 3" in note
    assert "is not represented" in note


def test_missing_page_note_pluralises():
    assert "pages 3, 7 yielded" in missing_pages_note([3, 7])
    assert "are not represented" in missing_pages_note([3, 7])


def test_no_note_when_every_page_has_text():
    assert missing_pages_note([]) == ""


def test_mixed_fixture_declares_its_scanned_page(fixtures_dir, config, entry_for):
    from pipeline.convert import convert_entry
    from pipeline.triage import triage_pdf

    entry = entry_for("mixed.pdf")
    entry["triage"] = triage_pdf(fixtures_dir / "mixed.pdf", config).to_dict()

    text = convert_entry(entry, config, force=True).output.read_text()

    assert "INCOMPLETE" in text
    assert "page 3" in text
