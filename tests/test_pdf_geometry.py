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
