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


def _pipe_table(rows: int, cell: str = "x" * 50, cols: int = 1) -> str:
    header = "| " + " | ".join(f"h{c}" for c in range(cols)) + " |"
    delim = "| " + " | ".join("---" for _ in range(cols)) + " |"
    body = [f"| {' | '.join(f'{cell}{r}' for _ in range(cols))} |" for r in range(rows)]
    return "\n".join([header, delim, *body])


def _rows(text: str) -> list[str]:
    return text.split("\n")


def test_table_at_or_under_chunk_max_is_one_chunk() -> None:
    table = _pipe_table(rows=20)
    assert len(table) <= CHUNK_MAX
    blocks = [_b(0, "heading", "# H"), _b(1, "paragraph", "lead in"), _b(2, "table", table)]
    chunks = build_chunks([_section(blocks)])
    table_chunks = [c for c in chunks if c.block_first == 2]
    assert len(table_chunks) == 1
    assert table_chunks[0].text == table


def test_oversized_table_splits_at_row_boundaries_with_header_and_overlap() -> None:
    """Above CHUNK_MAX a pipe table is cut only between rows. Every piece re-opens with the
    header row and its delimiter, carries the last body row of the previous piece as
    overlap, keeps the table's block ordinal, and stays within CHUNK_MAX. Read back in
    order, the pieces' body rows are exactly the original rows."""
    table = _pipe_table(rows=80)
    assert len(table) > CHUNK_MAX
    header, delim, *body = _rows(table)
    blocks = [_b(0, "heading", "# H"), _b(1, "paragraph", "lead in"), _b(2, "table", table)]
    chunks = build_chunks([_section(blocks)])
    pieces = [c for c in chunks if c.block_first == 2]
    assert len(pieces) >= 2
    seen: list[str] = []
    prev_last: str | None = None
    for c in pieces:
        assert c.block_last == 2
        assert len(c.text) <= CHUNK_MAX
        lines = _rows(c.text)
        assert lines[0] == header and lines[1] == delim
        piece_body = lines[2:]
        assert all(line in body for line in piece_body)  # never a partial row
        if prev_last is not None:
            assert piece_body[0] == prev_last  # exactly one row of overlap
            piece_body = piece_body[1:]
        assert piece_body, "a piece must add at least one new row"
        seen.extend(piece_body)
        prev_last = lines[-1]
    assert seen == body


def test_table_opening_a_section_shares_its_first_piece_with_the_heading() -> None:
    table = _pipe_table(rows=80)
    blocks = [_b(0, "heading", "# H"), _b(1, "table", table)]
    chunks = build_chunks([_section(blocks)])
    assert chunks[0].block_first == 0 and chunks[0].block_last == 1
    assert chunks[0].text.startswith("# H\n\n| h0 |")
    assert all(c.block_first == 1 and c.block_last == 1 for c in chunks[1:])


def test_table_row_longer_than_chunk_max_is_never_cut() -> None:
    """A row is the smallest unit the markdown table format guarantees; a row that is
    itself over CHUNK_MAX stays whole in its own piece, like a paragraph with no blank
    line to split at."""
    huge = "y" * (CHUNK_MAX + 100)
    table = "\n".join(["| h |", "| --- |", "| a |", f"| {huge} |", "| b |"])
    blocks = [_b(0, "heading", "# H"), _b(1, "paragraph", "lead in"), _b(2, "table", table)]
    chunks = build_chunks([_section(blocks)])
    pieces = [c for c in chunks if c.block_first == 2]
    assert any(f"| {huge} |" in _rows(c.text) for c in pieces)
    for c in pieces:
        assert all(line.startswith("|") for line in _rows(c.text))


def test_annotations_after_a_split_table_stay_with_its_last_piece() -> None:
    table = _pipe_table(rows=80)
    blocks = [
        _b(0, "heading", "# H"),
        _b(1, "paragraph", "lead in"),
        _b(2, "table", table),
        _b(3, "annotation", "> **Annotation** note"),
        _b(4, "paragraph", "after"),
    ]
    chunks = build_chunks([_section(blocks)])
    spans = [(c.block_first, c.block_last) for c in chunks]
    assert spans.count((2, 4)) == 0
    assert (2, 3) in spans and spans.index((2, 3)) == len(spans) - 2
    assert spans[-1] == (4, 4)
    assert all((a, b) in ((0, 1), (2, 2), (2, 3), (4, 4)) for a, b in spans), spans


def test_oversized_table_block_without_pipe_rows_splits_at_blank_lines() -> None:
    """A block Stage 1 labelled `table` but rendered without pipe rows has no row
    boundaries to honour; it is treated like an oversized paragraph."""
    text = "\n\n".join(f"Flattened cell run number {i} " * 8 for i in range(40))
    assert len(text) > CHUNK_MAX and "|" not in text
    blocks = [_b(0, "heading", "# H"), _b(1, "paragraph", "lead in"), _b(2, "table", text)]
    chunks = build_chunks([_section(blocks)])
    pieces = [c for c in chunks if c.block_first == 2]
    assert len(pieces) >= 2
    assert all(len(c.text) <= CHUNK_MAX for c in pieces)
    assert "\n\n".join(c.text for c in pieces) == text


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


def test_fixtures_table_pieces_are_whole_rows_within_chunk_max() -> None:
    """Every chunk over CHUNK_MAX in the fixtures is a single indivisible unit; a table
    piece holds only complete rows and starts with the table's header."""
    for slug, (sections, chunks) in _all_fixture_chunks().items():
        blocks = {b.ordinal: b for s in sections for b in s.blocks}
        for c in chunks:
            if c.page_first is not None and c.page_last is not None:
                assert c.page_first <= c.page_last
            if c.block_first == c.block_last and blocks[c.block_first].kind == "table":
                lines = c.text.split("\n")
                if lines[0].startswith("|"):
                    original = blocks[c.block_first].text.split("\n")
                    assert lines[:2] == original[:2], slug
                    assert all(line in original for line in lines), slug
                    assert len(c.text) <= CHUNK_MAX or len(lines) == 3, slug


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
