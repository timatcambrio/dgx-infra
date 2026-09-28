"""`retrieval.sections.build_sections` (brief §5.4)."""

from __future__ import annotations

from pathlib import Path

from retrieval.kbfiles import Block, Document, load_document
from retrieval.sections import build_sections

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kb"


def _doc(blocks: list[Block], title: str = "Doc Title") -> Document:
    return Document(
        slug="doc",
        md_path=Path("doc.md"),
        meta={"title": title},
        blocks=blocks,
        has_sidecar=True,
        incomplete_pages=[],
        page_count=None,
        dropped_empty_blocks=0,
        chars=0,
    )


def _h(ordinal: int, level: int, text: str, page: int | None = 1) -> Block:
    return Block(
        ordinal=ordinal,
        block_id=f"doc:p{page or 0:03d}:b{ordinal:03d}",
        page=page,
        kind="heading",
        confidence=None,
        bbox=None,
        text="#" * level + " " + text,
        level=level,
    )


def _p(ordinal: int, text: str = "some text", page: int | None = 1) -> Block:
    return Block(
        ordinal=ordinal,
        block_id=f"doc:p{page or 0:03d}:b{ordinal:03d}",
        page=page,
        kind="paragraph",
        confidence=None,
        bbox=None,
        text=text,
    )


def test_heading_path_built_from_a_per_level_stack() -> None:
    blocks = [
        _h(0, 1, "L1"),
        _p(1),
        _h(2, 2, "L2a"),
        _p(3),
        _h(4, 3, "L3a"),
        _p(5),
        _h(6, 2, "L2b"),  # returning to level 2 should drop the old level-3 heading
        _p(7),
        _h(8, 3, "L3b"),
        _p(9),
    ]
    sections = build_sections(_doc(blocks, title="Title"))
    paths = {s.heading: s.heading_path for s in sections}
    assert paths["L1"] == "Title › L1"
    assert paths["L2a"] == "Title › L1 › L2a"
    assert paths["L3a"] == "Title › L1 › L2a › L3a"
    assert paths["L2b"] == "Title › L1 › L2b"
    assert paths["L3b"] == "Title › L1 › L2b › L3b"


def test_level_4_heading_does_not_open_a_section() -> None:
    blocks = [
        _h(0, 2, "Section"),
        _p(1),
        _h(2, 4, "Detail Note"),
        _p(3),
        _p(4),
    ]
    sections = build_sections(_doc(blocks))
    assert len(sections) == 1
    assert sections[0].block_first == 0
    assert sections[0].block_last == 4
    # the #### heading block itself stays inside, as an ordinary block
    kinds = [b.kind for b in sections[0].blocks]
    assert kinds.count("heading") == 2


def test_section_zero_with_no_heading() -> None:
    blocks = [_p(0, "preamble"), _h(1, 1, "First Heading"), _p(2)]
    sections = build_sections(_doc(blocks, title="My Doc"))
    assert sections[0].heading is None
    assert sections[0].heading_path == "My Doc"
    assert sections[0].block_first == 0
    assert sections[0].block_last == 0
    assert sections[1].heading == "First Heading"


def test_document_starting_with_a_heading_has_no_bare_section_zero() -> None:
    blocks = [_h(0, 1, "Only Heading"), _p(1)]
    sections = build_sections(_doc(blocks))
    assert len(sections) == 1
    assert sections[0].heading == "Only Heading"


def test_page_ranges() -> None:
    blocks = [
        _h(0, 1, "S", page=1),
        _p(1, page=1),
        _p(2, page=2),
        _p(3, page=None),
        _p(4, page=3),
    ]
    sections = build_sections(_doc(blocks))
    assert sections[0].page_first == 1
    assert sections[0].page_last == 3


def test_fixtures_sections_are_well_formed() -> None:
    for slug in ("handbook", "budget-form", "deck", "reference-table"):
        doc = load_document(FIXTURES / f"{slug}.md")
        sections = build_sections(doc)
        assert sections
        for s in sections:
            assert s.block_first <= s.block_last
            if s.page_first is not None and s.page_last is not None:
                assert s.page_first <= s.page_last
        # section indices are contiguous starting at 0
        assert [s.index for s in sections] == list(range(len(sections)))


# ------------------------------------------- a heading that introduces nothing is no boundary


def test_a_caption_with_no_body_keeps_the_section_it_introduces() -> None:
    """The defect these tests exist for.

    A table's caption and the table itself arrived as two sections: the caption held a
    heading and nothing else, and the data was titled by its own header row. The words
    someone searches for were in one section and the answer in the next, and neither could
    answer on its own. Measured over the proxy corpus, 443 of 1,577 sections — 28% — had
    no body at all, so better than one search result in four was a dead end that cost a
    reader, or an assistant, a turn and told it nothing.
    """
    sections = build_sections(
        _doc([
            _h(1, 3, "Active Duty Officer Grade Distribution"),
            _h(2, 3, "rank number percent"),
            _p(3, "| Captain | 5,913 | 28.6% |"),
        ])
    )

    assert len(sections) == 1
    assert sections[0].heading == "Active Duty Officer Grade Distribution"
    assert "5,913" in "".join(block.text for block in sections[0].blocks)


def test_a_subsumed_heading_keeps_its_words_in_the_body() -> None:
    """Nothing is dropped: the swallowed heading is still indexed and still rendered.

    On the NIH form the swallowed line IS the answer — `List items and dollar amount for
    each item exceeding $5,000` was a heading with an empty body, and the question it
    answers asks for that threshold.
    """
    sections = build_sections(
        _doc([
            _h(1, 3, "C.Equipment Description"),
            _h(2, 3, "List items and dollar amount for each item exceeding $5,000"),
            _h(3, 3, "Equipment item Funds Requested ($)"),
            _p(4, "Additional Equipment: Total funds requested"),
        ])
    )

    assert len(sections) == 1
    body = "\n".join(block.text for block in sections[0].blocks)
    assert "exceeding $5,000" in body
    assert "Equipment item Funds Requested" in body


def test_a_heading_with_a_body_still_opens_its_own_section() -> None:
    """The guard rail: only a heading that introduces *nothing* gives up its boundary."""
    sections = build_sections(
        _doc([
            _h(1, 3, "First"),
            _p(2, "first body"),
            _h(3, 3, "Second"),
            _p(4, "second body"),
        ])
    )

    assert [section.heading for section in sections] == ["First", "Second"]


def test_a_swallowed_child_heading_still_deepens_the_heading_path() -> None:
    """A genuine parent-and-child keeps its path; a sibling does not take over the name.

    `4` then `4.1` is a nesting, and the citation should still say which subsection was
    meant. Two headings at the *same* level are not a nesting — a form's field labels are
    all set the same size — and letting the second name the section would restore exactly
    the useless heading this change removes.
    """
    nested = build_sections(
        _doc([_h(1, 2, "4 Processor Parameters"), _h(2, 3, "4.1 Overview"), _p(3)])
    )
    assert len(nested) == 1
    assert nested[0].heading_path == "Doc Title › 4 Processor Parameters › 4.1 Overview"

    siblings = build_sections(
        _doc([_h(1, 3, "C.Equipment Description"), _h(2, 3, "Equipment item"), _p(3)])
    )
    assert len(siblings) == 1
    assert siblings[0].heading_path == "Doc Title › C.Equipment Description"


def test_a_run_of_empty_headings_collapses_to_one_section() -> None:
    """Chaining stops at the first heading that has content, not before."""
    sections = build_sections(
        _doc([_h(1, 1, "A"), _h(2, 2, "B"), _h(3, 3, "C"), _p(4, "body"), _h(5, 3, "D"), _p(6)])
    )

    assert [section.heading for section in sections] == ["A", "D"]
    assert sections[0].heading_path == "Doc Title › A › B › C"


def test_no_section_in_the_fixtures_is_a_dead_end() -> None:
    """The property, asserted over the committed fixture corpus rather than one document."""
    for md in sorted(FIXTURES.glob("*.md")):
        for section in build_sections(load_document(md)):
            body = [b for b in section.blocks if b.kind != "heading"]
            assert body, f"{md.name} section {section.index} ({section.heading!r}) has no body"
