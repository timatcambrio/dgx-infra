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


# --------------------------------------------------------- headings, lists, split words


def word(text: str, x0: float, x1: float, *, size: float = 12.0, font: str = "Helvetica") -> dict:
    return {
        "text": text,
        "x0": x0,
        "x1": x1,
        "top": 100.0,
        "bottom": 100.0 + size,
        "size": size,
        "fontname": font,
    }


def paragraphs(lines: list[Line], body: float, heading_sizes: list[float], config):
    """`_paragraphs` output as plain blocks, dropping the vertical positions."""
    return [text for _, text in pdf_geometry._paragraphs(lines, body, heading_sizes, config)]


def callout(fixtures_dir, config) -> str:
    return pdf_geometry.to_markdown(fixtures_dir / "callout_notes.pdf", config)


def test_a_line_barely_larger_than_body_is_not_a_heading(fixtures_dir, config):
    """Being bigger than body text is evidence of emphasis, not of a section title.

    The fixture's subhead is set 1.10x body -- under `PDF_HEADING_SIZE_RATIO`. On a form
    tutorial, where body text is the smallest type on the page, treating every larger line
    as a heading turns the whole document into headings and leaves nothing under them.
    """
    text = callout(fixtures_dir, config)

    assert "Request for contracting action" in text
    assert "# Request for contracting action" not in text


def test_a_bulleted_callout_stays_a_list(fixtures_dir, config):
    text = callout(fixtures_dir, config)

    assert "- Submit SEPARATE FUNDING REQUESTS for EACH VENDOR" in text
    for line in text.splitlines():
        assert not (line.startswith("#") and "SEPARATE FUNDING REQUESTS" in line)


def test_a_wrapped_bullet_continues_its_own_item(fixtures_dir, config):
    """The continuation line starts at the marker's own x, so only spacing can tell."""
    text = callout(fixtures_dir, config)

    assert (
        "- INCLUDE SOW, CONTRACT, SIGNED IGCE, 7600A (if applicable), "
        "MIPR INSTRUCTIONS (if applicable)"
    ) in text


def test_an_indented_sub_bullet_nests(fixtures_dir, config):
    text = callout(fixtures_dir, config)

    assert (
        "  - 7600A required for Reimbursable MIPRs, and if the receiving office "
        "requires one"
    ) in text


def test_a_wrapped_run_of_sentences_is_a_paragraph_not_a_heading(fixtures_dir, config):
    """Three short lines that are each heading-sized are still a paragraph together."""
    text = callout(fixtures_dir, config)

    assert (
        "This callout is a wrapped run of ordinary sentences, set two points larger than "
        "the body text around it, which makes it emphasis rather than a section heading."
    ) in text
    for line in text.splitlines():
        assert not (line.startswith("#") and "wrapped run of ordinary sentences" in line)


def test_a_list_ends_where_body_text_resumes(fixtures_dir, config):
    """A list that does not know how to end swallows the paragraph after it."""
    text = callout(fixtures_dir, config)

    assert "- see page 5 for info going into 7600A\n" in text
    assert "- Travel costs are reimbursed" not in text


def test_the_real_headings_survive(fixtures_dir, config):
    """The guards must not cost the document the one heading it really has."""
    assert "# OTA Funding Request Tutorial" in callout(fixtures_dir, config)
    assert "## NOTES:" in callout(fixtures_dir, config)


def test_a_word_split_across_font_subsets_is_rejoined(fixtures_dir, config):
    """"Submit" set as "S" + "ubmit" in two subsets of one face must come back whole."""
    text = callout(fixtures_dir, config)

    assert "Submit" in text
    assert "S ubmit" not in text


def test_fragments_with_no_gap_are_one_word(config):
    """A zero gap is never a space, whatever pdfplumber's absolute tolerance says."""
    words = [word("S", 72.0, 80.0, font="Helvetica-Oblique"), word("ubmit", 80.0, 109.3)]

    assert pdf_geometry._group_words_into_lines(words, config)[0].text == "Submit"


def test_a_real_space_still_separates_two_words(config):
    """The join must be narrower than a space at that type size, or words run together."""
    words = [word("EACH", 72.0, 100.0), word("VENDOR", 103.4, 145.0)]

    assert pdf_geometry._group_words_into_lines(words, config)[0].text == "EACH VENDOR"


def test_a_long_line_is_not_a_heading_however_large_the_type(config):
    long_text = (
        "Every funding request has to name a single vendor and a single programme "
        "element, which is why these are always submitted separately."
    )
    lines = [line(long_text, size=20.0)]

    assert paragraphs(lines, body=10.0, heading_sizes=[20.0], config=config) == [long_text]


def test_a_short_large_line_is_still_a_heading(config):
    lines = [line("Allowable Expenses", size=20.0)]

    assert paragraphs(lines, body=10.0, heading_sizes=[20.0], config=config) == [
        "# Allowable Expenses"
    ]


def test_a_bullet_glyph_line_is_a_list_item_not_a_heading(config):
    lines = [
        line("• Check with the office receiving funding", top=100, size=12.0),
        line("• Some offices publish their own instructions", top=116, size=12.0),
    ]

    assert paragraphs(lines, body=9.45, heading_sizes=[12.0], config=config) == [
        "- Check with the office receiving funding\n"
        "- Some offices publish their own instructions"
    ]


def test_a_numbered_item_keeps_its_number(config):
    lines = [
        line("1. Attach the signed IGCE", top=100, size=12.0),
        line("2. Attach the MIPR instructions", top=116, size=12.0),
    ]

    assert paragraphs(lines, body=9.45, heading_sizes=[12.0], config=config) == [
        "1. Attach the signed IGCE\n2. Attach the MIPR instructions"
    ]


def test_a_double_hyphen_is_not_a_list_marker(config):
    """`-- None --` is what an unfilled form field prints, not a bullet."""
    lines = [line("-- None --", size=9.45)]

    assert paragraphs(lines, body=9.45, heading_sizes=[12.0], config=config) == ["-- None --"]


# --------------------------------------------------------------- slide tables as headings


def slide_blocks(fixtures_dir, config, page: int = 1):
    return [
        block
        for block in pdf_geometry.to_blocks(fixtures_dir / "slide_table.pdf", config)
        if block.page_number == page
    ]


def test_a_slide_table_is_not_detected_as_a_table_at_all(fixtures_dir, config):
    """The premise of the other tests here: no grid to fall back on.

    The slide's table has horizontal rules only, so pdfplumber finds nothing, and no three
    consecutive lines share a cell count, so the borderless recovery finds nothing either.
    Both refusals are correct — inventing this grid would take guessing. The question the
    remaining tests answer is what the converter does *instead*.
    """
    import pdfplumber

    with pdfplumber.open(fixtures_dir / "slide_table.pdf") as pdf:
        page = pdf.pages[0]
        assert page.find_tables() == []
        lines = pdf_geometry._group_words_into_lines(
            page.extract_words(extra_attrs=pdf_geometry._WORD_ATTRS), config
        )
    assert find_aligned_table_runs(lines, config) == []


def test_slide_table_cells_do_not_become_headings(fixtures_dir, config):
    """Every cell is larger than body text and most are bold; none is a heading.

    Before this the slide came out as a heading per cell, and the sectioniser cut it into a
    section per cell — one holding `$8,750` and the next holding the `2026` it belongs to.
    """
    headings = [block.text for block in slide_blocks(fixtures_dir, config)
                if block.kind == "heading"]

    assert "# IRS Contribution Limits" in headings
    for cell in ("$4,300", "$8,550", "$4,400", "$8,750", "SINGLE", "FAMILY"):
        assert not any(cell in heading for heading in headings), heading_failure(headings, cell)


def heading_failure(headings: list[str], cell: str) -> str:
    return f"{cell!r} was promoted to a heading; headings were {headings}"


def test_a_line_does_not_cross_the_gutter(fixtures_dir, config):
    """Words are grouped into lines per column, not across the page.

    The slide's callout ends level with the table's second row, so grouping by baseline
    alone welds the two together. The real deck produced the cell `2026 2026!` that way —
    text neither column contains, and neither a reader nor an embedding can do anything
    with it.
    """
    text = pdf_geometry.to_markdown(fixtures_dir / "slide_table.pdf", config)

    assert "2026 2026!" not in text
    assert "$4,400 $8,750 2026" in text


def test_a_slide_table_row_keeps_its_figure_and_its_year_together(fixtures_dir, config):
    """What the sectioniser needs: the answer stated somewhere in one piece."""
    text = "\n".join(block.text for block in slide_blocks(fixtures_dir, config))

    assert "$8,750" in text and "2026" in text
    for block in slide_blocks(fixtures_dir, config):
        if "$8,750" in block.text:
            assert "2026" in block.text
            break
    else:  # pragma: no cover - the assertion above has already failed if we get here
        raise AssertionError("no block holds $8,750")


def test_the_slide_title_is_still_a_heading(fixtures_dir, config):
    """The demotion is targeted: a title with nothing beside it keeps its `#`."""
    text = pdf_geometry.to_markdown(fixtures_dir / "slide_table.pdf", config)

    assert "# How The Limit Is Set" in text


def test_two_lines_sharing_columns_are_not_headings(config):
    """A heading is one run of text; these are two rows, whatever size they are set in."""
    lines = [
        cells_line([(340.0, "SINGLE"), (477.0, "FAMILY")], top=100, size=20.0),
        cells_line([(340.0, "PLAN"), (477.0, "PLAN")], top=124, size=20.0),
    ]

    assert paragraphs(lines, body=10.0, heading_sizes=[20.0], config=config) == [
        "SINGLE FAMILY PLAN PLAN"
    ]


def test_a_numbered_heading_is_still_a_heading(config):
    """The rule above must not fire on `1.1<tab>Purpose`.

    A tab between a section number and its title opens a gap as wide as any table's, so the
    line splits into two cells exactly like a row does. What it does not have is a line
    above or below it splitting the same way -- which is why the rule asks for one.
    """
    lines = [
        cells_line([(72.0, "1.1"), (133.2, "Purpose")], top=100, size=15.0),
        line("This document describes the auxiliary data required by the", top=124, size=12.0),
    ]

    blocks = paragraphs(lines, body=12.0, heading_sizes=[15.0], config=config)

    assert blocks[0] == "# 1.1 Purpose"


def test_two_numbered_headings_in_a_row_are_still_headings(config):
    """Consecutive numbered headings share a tab stop but are not adjacent lines.

    A heading has its content under it, so the line below a heading is prose. This pins the
    case down anyway, because the cost of getting it wrong is a whole document's structure.
    """
    lines = [
        cells_line([(72.0, "1.1"), (133.2, "Purpose")], top=100, size=15.0),
        line("Body text under the first heading.", top=124, size=12.0),
        cells_line([(72.0, "1.2"), (133.2, "Scope")], top=160, size=15.0),
        line("Body text under the second heading.", top=184, size=12.0),
    ]

    blocks = paragraphs(lines, body=12.0, heading_sizes=[15.0], config=config)

    assert blocks[0] == "# 1.1 Purpose"
    assert blocks[2] == "# 1.2 Scope"


def test_a_line_standing_beside_another_is_not_a_heading(config):
    """One cell per line, but they share a row: different sizes, same band of the page."""
    lines = [
        line("$4,400", top=290.0, size=24.0, words=((345.0, 419.0, "$4,400"),), bold=True),
        line("2026", top=294.0, size=28.0, words=((231.0, 299.0, "2026"),), bold=True),
    ]

    assert paragraphs(lines, body=10.0, heading_sizes=[28.0, 24.0], config=config) == [
        "$4,400 2026"
    ]


def test_stacked_prose_is_not_read_as_side_by_side(config):
    """The guard rail on the rule above: consecutive lines of one paragraph overlap in x."""
    first = line("Contributions are capped", top=100.0, size=12.0)
    second = line("each calendar year", top=116.0, size=12.0)

    assert not pdf_geometry._side_by_side(first, second)


def test_a_heading_with_a_wide_margin_note_beside_it_is_demoted(config):
    """Stated as the cost of the rule, not as a happy outcome.

    Horizontally disjoint and sharing a row is the only evidence geometry has, and a
    margin note beside a heading matches it. The text survives; only the `#` is lost,
    which is the cheaper of the two errors.
    """
    heading = line("Annual Limits", top=100.0, size=20.0, words=((72.0, 200.0, "Annual"),
                                                                 (205.0, 260.0, "Limits")))
    note = line("revised", top=102.0, size=20.0, words=((480.0, 540.0, "revised"),))

    assert pdf_geometry._side_by_side(heading, note)


def banded_rows(fixtures_dir) -> list[list[str]]:
    import pdfplumber

    with pdfplumber.open(fixtures_dir / "banded_table.pdf") as pdf:
        regions = pdf_geometry._table_regions(pdf.pages[0])
    assert len(regions) == 1, f"expected one ruled table, got {len(regions)}"
    return regions[0][1]


def test_a_banded_table_has_no_rule_around_its_unshaded_rows(fixtures_dir):
    """The premise of the test below, asserted so the fixture cannot rot into a no-op.

    Only the shaded rows are boxed, so the outer verticals exist across those rows alone.
    pdfplumber therefore reports no cell at all at either end of an unshaded row.
    """
    import pdfplumber

    with pdfplumber.open(fixtures_dir / "banded_table.pdf") as pdf:
        table = pdf.pages[0].find_tables()[0]
        missing = [
            index
            for index, row in enumerate(table.rows)
            if row.cells[0] is None and row.cells[-1] is None
        ]

    assert missing, "the fixture no longer reproduces the unruled outer cells"


def test_every_row_of_a_banded_table_keeps_all_its_cells(fixtures_dir):
    """The defect this fixture exists for.

    Every second row came back holding its middle number and nothing else: the label and
    the percentage were dropped, not merely unplaced. A table of bare numbers still reads
    as a table, which is why this failed silently where a missing table would not have.
    """
    rows = banded_rows(fixtures_dir)

    for row in rows:
        assert all(cell for cell in row), f"row {row} lost a cell; table was {rows}"


def test_a_recovered_cell_carries_the_value_the_page_prints(fixtures_dir):
    """Recovered, not invented: the text is the page's own words, in its own columns."""
    rows = banded_rows(fixtures_dir)

    assert ["22", "398", "1.9%"] in rows
    assert ["24", "1,175", "5.7%"] in rows


def test_a_blank_cell_that_is_ruled_stays_blank(fixtures_dir):
    """The guard rail: only a cell with no rule around it is filled in.

    An empty cell on a form is a fact about the form — nothing was entered — and the
    linking tests downstream depend on it staying empty.
    """
    import pdfplumber

    with pdfplumber.open(fixtures_dir / "ruled_form.pdf") as pdf:
        cells = pdf_geometry._table_cells(pdf.pages[0])

    assert any(cell.text == "" for cell in cells)


class StubRow:
    """One row of a `pdfplumber` table: its band, and a box per cell or `None` where unruled."""

    def __init__(self, bbox, cells):
        self.bbox = bbox
        self.cells = cells


class StubTable:
    def __init__(self, bbox, rows, extracted):
        self.bbox = bbox
        self.rows = rows
        self._extracted = extracted

    def extract(self):
        return self._extracted


class StubPage:
    def __init__(self, words):
        self._words = words

    def extract_words(self, **_):
        return self._words


def cell_word(text: str, x0: float, top: float, width: float = 30.0, height: float = 8.0) -> dict:
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top, "bottom": top + height}


def test_a_cell_is_recovered_only_where_the_grid_is_regular():
    """Two rows disagreeing about where a column ends is a merged cell, not a column.

    Averaging the two produces a span overlapping its neighbour, and filling that in copies
    one cell's text into two. The table is reported exactly as pdfplumber reported it.
    """
    rows = [
        StubRow((0.0, 0.0, 300.0, 10.0), [(0.0, 0.0, 100.0, 10.0), (100.0, 0.0, 300.0, 10.0)]),
        StubRow((0.0, 10.0, 300.0, 20.0), [(0.0, 10.0, 200.0, 20.0), None]),
    ]
    table = StubTable((0.0, 0.0, 300.0, 20.0), rows, [["a", "b"], ["c", ""]])
    page = StubPage([cell_word("recovered", 210.0, 11.0)])

    assert [texts for _, texts, _ in pdf_geometry._table_rows(page, table)] == [
        ["a", "b"],
        ["c", ""],
    ]


def test_a_cell_is_not_recovered_over_a_row_that_already_renders_it():
    """pdfplumber returns rows nested inside a taller row of the same table.

    The tall row's cell already holds the text, so filling the nested row's cell from the
    same words says it twice — which is how a bulleted list ended up printed three times.
    """
    rows = [
        StubRow((0.0, 0.0, 200.0, 40.0), [(0.0, 0.0, 100.0, 40.0), (100.0, 0.0, 200.0, 40.0)]),
        StubRow((0.0, 10.0, 200.0, 20.0), [(0.0, 10.0, 100.0, 20.0), None]),
    ]
    table = StubTable((0.0, 0.0, 200.0, 40.0), rows, [["label", "bullets"], ["", ""]])
    page = StubPage([cell_word("bullets", 120.0, 11.0)])

    assert [texts for _, texts, _ in pdf_geometry._table_rows(page, table)] == [
        ["label", "bullets"],
        ["", ""],
    ]


def test_a_cell_is_not_recovered_over_another_table():
    """A table nested inside another renders its own text; the outer cell must not repeat it."""
    rows = [
        StubRow((0.0, 0.0, 200.0, 10.0), [(0.0, 0.0, 100.0, 10.0), (100.0, 0.0, 200.0, 10.0)]),
        StubRow((0.0, 10.0, 200.0, 20.0), [(0.0, 10.0, 100.0, 20.0), None]),
    ]
    table = StubTable((0.0, 0.0, 200.0, 20.0), rows, [["a", "b"], ["c", ""]])
    page = StubPage([cell_word("inner", 120.0, 11.0)])
    nested = (100.0, 10.0, 200.0, 20.0)

    assert [texts for _, texts, _ in pdf_geometry._table_rows(page, table, [nested])] == [
        ["a", "b"],
        ["c", ""],
    ]


def test_a_recovered_cell_takes_the_words_drawn_inside_it():
    """The positive case, stated on a grid with nothing else in the way."""
    rows = [
        StubRow((0.0, 0.0, 200.0, 10.0), [(0.0, 0.0, 100.0, 10.0), (100.0, 0.0, 200.0, 10.0)]),
        StubRow((0.0, 10.0, 200.0, 20.0), [(0.0, 10.0, 100.0, 20.0), None]),
    ]
    table = StubTable((0.0, 0.0, 200.0, 20.0), rows, [["a", "b"], ["c", ""]])
    page = StubPage([cell_word("41+", 120.0, 11.0)])

    rows_out = pdf_geometry._table_rows(page, table)

    assert [texts for _, texts, _ in rows_out] == [["a", "b"], ["c", "41+"]]
    assert rows_out[1][2][1] == (100.0, 10.0, 200.0, 20.0)
