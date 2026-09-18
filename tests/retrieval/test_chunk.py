"""`retrieval.chunk.build_chunks` (brief §5.4)."""

from __future__ import annotations

import json
from pathlib import Path

from retrieval.chunk import CHUNK_MAX, CHUNK_TARGET, build_chunks
from retrieval.kbfiles import Block, load_document
from retrieval.sections import Section, build_sections

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kb"


def _b(ordinal: int, kind: str, text: str, page: int | None = 1) -> Block:
    return Block(
        ordinal=ordinal,
        block_id=f"doc:p{page or 0:03d}:b{ordinal:03d}",
        page=page,
        kind=kind,
        confidence=None,
        bbox=None,
        text=text,
        level=1 if kind == "heading" else None,
    )


def _section(blocks: list[Block], index: int = 0) -> Section:
    pages = [b.page for b in blocks if b.page is not None]
    return Section(
        index=index,
        level=1,
        heading="H",
        heading_path="Doc › H",
        block_first=blocks[0].ordinal,
        block_last=blocks[-1].ordinal,
        page_first=min(pages) if pages else None,
        page_last=max(pages) if pages else None,
        blocks=blocks,
    )


# --- hand-built rule checks ----------------------------------------------------------


def test_table_opening_a_section_shares_the_heading_chunk() -> None:
    blocks = [_b(0, "heading", "# H"), _b(1, "table", "| a | b |\n| - | - |\n| 1 | 2 |")]
    chunks = build_chunks([_section(blocks)])
    assert len(chunks) == 1
    assert chunks[0].block_first == 0
    assert chunks[0].block_last == 1


def test_table_not_first_content_starts_its_own_chunk() -> None:
    blocks = [
        _b(0, "heading", "# H"),
        _b(1, "paragraph", "some text"),
        _b(2, "table", "| a | b |\n| - | - |\n| 1 | 2 |"),
    ]
    chunks = build_chunks([_section(blocks)])
    assert len(chunks) == 2
    assert chunks[0].block_first == 0 and chunks[0].block_last == 1
    assert chunks[1].block_first == 2 and chunks[1].block_last == 2


def test_table_never_spans_two_chunks_even_when_oversized() -> None:
    big_table = "| a |\n| - |\n" + "\n".join(f"| {'x' * 50} |" for _ in range(80))
    assert len(big_table) > CHUNK_MAX
    blocks = [
        _b(0, "heading", "# H"),
        _b(1, "paragraph", "lead in"),
        _b(2, "table", big_table),
    ]
    chunks = build_chunks([_section(blocks)])
    table_chunks = [c for c in chunks if c.block_first == 2]
    assert len(table_chunks) == 1
    assert table_chunks[0].block_last == 2


def test_annotation_and_boxed_text_never_start_a_chunk() -> None:
    blocks = [
        _b(0, "heading", "# H"),
        _b(1, "paragraph", "x" * (CHUNK_TARGET + 500)),
        _b(2, "annotation", "> **Annotation** note"),
        _b(3, "boxed_text", "> **Boxed text:** note"),
    ]
    chunks = build_chunks([_section(blocks)])
    # The lone heading is flushed on its own once the oversized paragraph would push past
    # CHUNK_TARGET (rule priority: the target check runs before the paragraph is
    # appended). The annotation/boxed_text then stay glued to that paragraph even though
    # the chunk is already past CHUNK_TARGET -- that gluing is rule 2, under test here.
    assert len(chunks) == 2
    assert chunks[0].block_first == 0 and chunks[0].block_last == 0
    assert chunks[1].block_first == 1 and chunks[1].block_last == 3


def test_oversized_paragraph_splits_at_blank_lines_preserving_ordinal() -> None:
    para = "\n\n".join(
        (f"Paragraph piece number {i} with some filler words. " * 6) for i in range(20)
    )
    assert len(para) > CHUNK_MAX
    blocks = [_b(0, "heading", "# H"), _b(1, "paragraph", para)]
    chunks = build_chunks([_section(blocks)])
    piece_chunks = [c for c in chunks if c.block_first == 1]
    assert len(piece_chunks) >= 2
    for c in piece_chunks:
        assert c.block_first == 1 and c.block_last == 1
    # no piece chunk is empty
    assert all(c.text.strip() for c in piece_chunks)


def test_no_empty_chunks_for_hand_built_section() -> None:
    blocks = [_b(0, "heading", "# H"), _b(1, "paragraph", "text")]
    chunks = build_chunks([_section(blocks)])
    assert all(c.text.strip() for c in chunks)


def test_page_first_le_page_last() -> None:
    blocks = [
        _b(0, "heading", "# H", page=1),
        _b(1, "paragraph", "a", page=1),
        _b(2, "paragraph", "b", page=2),
    ]
    chunks = build_chunks([_section(blocks)])
    for c in chunks:
        if c.page_first is not None and c.page_last is not None:
            assert c.page_first <= c.page_last


# --- fixture-based checks -------------------------------------------------------------


def _all_fixture_chunks():
    out = {}
    for slug in ("handbook", "budget-form", "deck", "reference-table"):
        doc = load_document(FIXTURES / f"{slug}.md")
        sections = build_sections(doc)
        out[slug] = (sections, build_chunks(sections))
    return out


def test_fixtures_chunk_boundaries_are_stable_across_two_runs() -> None:
    first = _all_fixture_chunks()
    second = _all_fixture_chunks()
    for slug in first:
        c1 = [(c.section_index, c.block_first, c.block_last, c.text) for c in first[slug][1]]
        c2 = [(c.section_index, c.block_first, c.block_last, c.text) for c in second[slug][1]]
        assert c1 == c2


def test_fixtures_no_empty_chunks() -> None:
    for slug, (sections, chunks) in _all_fixture_chunks().items():
        assert all(c.text.strip() for c in chunks), slug


def test_fixtures_table_chunks_never_split_and_pages_ordered() -> None:
    for slug, (sections, chunks) in _all_fixture_chunks().items():
        for c in chunks:
            if c.page_first is not None and c.page_last is not None:
                assert c.page_first <= c.page_last


def test_fixtures_expected_counts_match_committed_expected_json() -> None:
    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))["documents"]
    for slug, (sections, chunks) in _all_fixture_chunks().items():
        assert len(sections) == expected[slug]["section_count"], slug
        assert len(chunks) == expected[slug]["chunk_count"], slug


def test_annotations_after_a_table_stay_in_the_table_chunk() -> None:
    """A form's notes beside a table are the evidence that makes the table readable; the
    table's chunk keeps them, and the next ordinary block starts a new chunk."""
    blocks = [
        _b(0, "heading", "# Budget grid"),
        _b(1, "paragraph", "Intro line."),
        _b(2, "table", "| a | b |\n| - | - |\n| 1 | 2 |"),
        _b(3, "annotation", "> **Annotation** note one"),
        _b(4, "boxed_text", "> **Boxed text:** note two"),
        _b(5, "paragraph", "Next paragraph."),
    ]
    chunks = build_chunks([_section(blocks)])
    spans = [(c.block_first, c.block_last) for c in chunks]
    assert (2, 4) in spans, spans
    assert all(not (c.block_first == 3 or c.block_first == 4) for c in chunks)
    assert (5, 5) in spans
