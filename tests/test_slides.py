"""PowerPoint decks: one page per slide, with what a deck carries beyond its slide text.

The fixture (`deck.pptx`, built by `make_fixtures.py`) has one of each: a subtitle, nested
bullets, speaker notes, a reviewer comment, a table, a chart, a hidden slide, grouped text
boxes, a picture and SmartArt. Docling's own PowerPoint walk was measured against it first
and flattened the nested bullets to one level, so the converter walks the slides itself.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipeline import manifest as manifest_module
from pipeline.convert import STATUS_WRITTEN, convert_entry
from pipeline.frontmatter import parse_file, validate
from retrieval.cite import build_citation
from retrieval.kbfiles import load_document
from retrieval.sections import build_sections

FIXTURE = "deck.pptx"


@pytest.fixture
def converted(config, entry_for):
    entry = entry_for(FIXTURE)
    result = convert_entry(entry, config)
    assert result.status == STATUS_WRITTEN, result.message
    text = result.output.read_text(encoding="utf-8")
    sidecar = json.loads(result.output.with_suffix(".provenance.json").read_text("utf-8"))
    return entry, result.output, text, sidecar


def _blocks_on(sidecar: dict, page: int) -> list[dict]:
    return [b for b in sidecar["blocks"] if b["page"] == page]


def _block_text(text: str, block_id: str) -> str:
    after = text.split(f"<!-- dgx:block={block_id} -->\n", 1)[1]
    return after.split("\n\n<!-- dgx:block=", 1)[0]


def test_pptx_extension_is_inventoried():
    """Before this, inventory counted `.pptx` files as skipped."""
    assert manifest_module.format_for(Path("deck.pptx")) == "pptx"
    assert manifest_module.format_for(Path("DECK.PPTX")) == "pptx"


def test_front_matter_is_valid(converted):
    _, output, _, _ = converted
    meta = parse_file(output)
    assert validate(meta) == []
    assert meta["source_format"] == "pptx"


def test_each_slide_opens_with_a_numbered_heading(converted):
    """A slide number is how people refer to a deck; every slide gets one, titled or not."""
    _, _, text, _ = converted
    headings = re.findall(r"(?m)^## Slide .*$", text)
    assert headings == [
        "## Slide 1: Program Review",
        "## Slide 2: Travel Rates",
        "## Slide 3: Limits",
        "## Slide 4: Draft Options",
        "## Slide 5",
        "## Slide 6: Process",
    ]


def test_block_ids_and_pages_are_slide_numbers(converted):
    """`fetch` by page and the citation's range both rely on page == slide number."""
    _, _, _, sidecar = converted
    for block in sidecar["blocks"]:
        page = int(block["block_id"].split(":p", 1)[1][:3])
        assert block["page"] == page
    assert sorted({b["page"] for b in sidecar["blocks"]}) == [1, 2, 3, 4, 5, 6]


def test_headings_inside_a_slide_sit_below_the_slide_heading(converted):
    """A subtitle or second title must not open a section above its own slide."""
    _, output, _, _ = converted
    document = load_document(output)
    levels = [b.level for b in document.blocks if b.kind == "heading"]
    assert all(level >= 2 for level in levels)
    titles = [s.heading_path for s in build_sections(document)]
    assert any("Slide 2: Travel Rates" in path for path in titles)


def test_nested_bullets_keep_their_level(converted):
    """Docling's walk put "Published rate applies" at the same level as "Lodging"."""
    _, _, text, sidecar = converted
    lists = [b for b in _blocks_on(sidecar, 2) if b["kind"] == "list"]
    assert len(lists) == 1
    assert _block_text(text, lists[0]["block_id"]) == (
        "- Lodging\n  - Published rate applies\n- Meals\n  - Daily allowance"
    )


def test_speaker_notes_are_kept_on_their_slide(converted):
    _, _, text, sidecar = converted
    notes = [b for b in sidecar["blocks"] if b["confidence"] == "pptx:notes"]
    assert [b["page"] for b in notes] == [2]
    assert _block_text(text, notes[0]["block_id"]) == (
        "> **Speaker notes:** Mention the lodging change.\n"
        "> Rates take effect at the start of the quarter."
    )


def test_reviewer_comments_are_left_out(converted, fixtures_dir):
    """Comments are review traffic, not the deck's content; the Word path drops them too."""
    import sys

    sys.path.insert(0, str(fixtures_dir.parent))
    from make_fixtures import REVIEWER_COMMENT

    _, _, text, _ = converted
    assert REVIEWER_COMMENT not in text
    assert "Reviewer remark" not in text


def test_table_becomes_a_markdown_table(converted):
    _, _, text, sidecar = converted
    tables = [b for b in _blocks_on(sidecar, 3) if b["kind"] == "table"]
    assert len(tables) == 2  # the table and the chart's data
    first = _block_text(text, tables[0]["block_id"])
    assert first.startswith("| Expense category | Limit | Receipt required |\n| --- | --- | --- |")


def test_chart_data_is_kept_as_a_table(converted):
    """A chart's numbers are in the file; rendering it as a missing picture would drop them."""
    entry, _, text, sidecar = converted
    charts = [b for b in sidecar["blocks"] if b["confidence"] == "pptx:chart"]
    assert len(charts) == 1
    assert _block_text(text, charts[0]["block_id"]) == (
        "**Chart:** Trips per quarter\n\n"
        "|  | Trips |\n| --- | --- |\n| Q1 | 12 |\n| Q2 | 18 |\n| Q3 | 9 |"
    )
    assert entry["conversion"]["charts"] == 1


def test_hidden_slide_is_kept_and_marked(converted):
    entry, _, text, sidecar = converted
    marks = [b for b in sidecar["blocks"] if b["confidence"] == "pptx:hidden"]
    assert [b["page"] for b in marks] == [4]
    assert "Option under discussion" in text
    assert entry["conversion"]["hidden_slides"] == 1


def test_text_inside_grouped_shapes_is_kept(converted):
    _, _, text, _ = converted
    assert "Travel desk hours" in text
    assert "Weekdays only" in text


def test_pictures_are_marked_and_counted(converted):
    entry, _, text, sidecar = converted
    pictures = [b for b in sidecar["blocks"] if b["kind"] == "picture"]
    assert [b["page"] for b in pictures] == [5]
    assert "> **INCOMPLETE — this deck shows 1 image(s)" in text
    assert entry["conversion"]["images"] == 1


def test_smartart_text_is_recovered(converted, fixtures_dir):
    """SmartArt keeps its text in a separate part that a shape walk never reaches."""
    _, _, text, sidecar = converted
    smartart = [b for b in sidecar["blocks"] if b["confidence"] == "pptx:smartart"]
    assert [b["page"] for b in smartart] == [6]
    assert _block_text(text, smartart[0]["block_id"]) == (
        "- Request approval\n- Book through the travel desk\n- Submit receipts"
    )


def test_output_loads_for_indexing(converted):
    _, output, _, _ = converted
    document = load_document(output)
    assert document.has_sidecar
    assert document.page_count == 6


def test_conversion_is_deterministic(config, entry_for):
    first = convert_entry(entry_for(FIXTURE), config).output.read_bytes()
    second = convert_entry(entry_for(FIXTURE), config, force=True).output.read_bytes()
    assert first == second


def test_citation_names_slides_for_a_deck():
    citation = build_citation(
        title="Deck",
        heading_path="Slide 2: Travel Rates",
        source_file="deck.pptx",
        source_format="pptx",
        page_first=2,
        page_last=3,
        doc_date="UNCONFIRMED",
        first_block_id="deck:p002:b001",
        last_block_id="deck:p003:b002",
    )
    assert ", slides 2–3," in citation
    assert "pages" not in citation


def test_citation_still_names_pages_for_a_pdf():
    citation = build_citation(
        title="Handbook",
        heading_path="Rates",
        source_file="handbook.pdf",
        source_format="pdf",
        page_first=4,
        page_last=5,
        doc_date="UNCONFIRMED",
        first_block_id="h:p004:b001",
        last_block_id="h:p005:b001",
    )
    assert ", pages 4–5," in citation
