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
