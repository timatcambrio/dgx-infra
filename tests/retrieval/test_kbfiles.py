"""`retrieval.kbfiles` (brief §5.2/§5.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.frontmatter import build as fm_build
from pipeline.frontmatter import render as fm_render
from retrieval.kbfiles import KbFileError, load_document

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kb"


def _write_doc(tmp_path: Path, slug: str, body: str, *, sidecar_blocks=None, sha_override=None):
    import hashlib

    content_sha256 = sha_override or hashlib.sha256(body.encode("utf-8")).hexdigest()
    meta = fm_build(
        title="Test Doc",
        source_file=f"{slug}.pdf",
        source_format="pdf",
        converter="test",
        content_sha256=content_sha256,
        text_class="clean",
        doc_date="UNCONFIRMED",
    )
    text = fm_render(meta, body)
    md_path = tmp_path / f"{slug}.md"
    md_path.write_text(text, encoding="utf-8")
    if sidecar_blocks is not None:
        real_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        sidecar = {
            "version": 1,
            "source_file": f"{slug}.pdf",
            "source_format": "pdf",
            "content_sha256": real_sha,
            "converter": "test",
            "blocks": sidecar_blocks,
        }
        (tmp_path / f"{slug}.provenance.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return md_path


# --- fixtures round trip -----------------------------------------------------------


def test_fixtures_load_cleanly() -> None:
    for slug in ("handbook", "budget-form", "deck", "reference-table"):
        doc = load_document(FIXTURES / f"{slug}.md")
        assert doc.blocks, f"{slug} produced no blocks"
        assert doc.chars > 0


def test_handbook_heading_levels_and_sidecar_shape() -> None:
    doc = load_document(FIXTURES / "handbook.md")
    assert doc.has_sidecar is True
    headings = [b for b in doc.blocks if b.kind == "heading"]
    assert headings, "expected heading blocks"
    for h in headings:
        assert 1 <= h.level <= 6
    # The #### "Detail Note" heading is level 4.
    assert any(h.level == 4 for h in headings)


def test_budget_form_incomplete_pages_em_dash() -> None:
    doc = load_document(FIXTURES / "budget-form.md")
    assert doc.incomplete_pages == [2, 3]


def test_reference_table_has_no_sidecar_and_synthetic_ids() -> None:
    doc = load_document(FIXTURES / "reference-table.md")
    assert doc.has_sidecar is False
    assert doc.page_count is None
    assert len(doc.blocks) == 1
    assert doc.blocks[0].kind == "table"
    assert doc.blocks[0].page is None
    assert doc.blocks[0].block_id == "reference-table:p000:b001"


def test_deck_section0_paragraph_precedes_first_heading() -> None:
    doc = load_document(FIXTURES / "deck.md")
    assert doc.blocks[0].kind == "paragraph"
    assert doc.blocks[1].kind == "heading"


# --- hand-built error/edge cases ----------------------------------------------------


def test_sidecar_sha_mismatch_raises(tmp_path: Path) -> None:
    md_path = _write_doc(
        tmp_path,
        "mismatch",
        "<!-- dgx:block=mismatch:p001:b000 -->\nHello world.\n",
        sidecar_blocks=[{"block_id": "mismatch:p001:b000", "page": 1, "kind": "paragraph", "confidence": "x"}],
        sha_override="0" * 64,
    )
    with pytest.raises(KbFileError, match="sha mismatch"):
        load_document(md_path)


def test_missing_anchor_in_sidecar_raises(tmp_path: Path) -> None:
    """A markdown block whose id is absent from the sidecar raises (brief §5.2 step 4)."""
    md_path = _write_doc(
        tmp_path,
        "noanchor",
        "<!-- dgx:block=noanchor:p001:b000 -->\nHello world.\n",
        sidecar_blocks=[{"block_id": "noanchor:p001:b999", "page": 1, "kind": "paragraph", "confidence": "x"}],
    )
    with pytest.raises(KbFileError, match="missing from the sidecar"):
        load_document(md_path)


def test_sidecar_block_missing_from_markdown_raises(tmp_path: Path) -> None:
    """A sidecar entry with no matching anchor in the markdown also raises."""
    md_path = _write_doc(
        tmp_path,
        "extra",
        "<!-- dgx:block=extra:p001:b000 -->\nHello world.\n",
        sidecar_blocks=[
            {"block_id": "extra:p001:b000", "page": 1, "kind": "paragraph", "confidence": "x"},
            {"block_id": "extra:p001:b001", "page": 1, "kind": "paragraph", "confidence": "x"},
        ],
    )
    with pytest.raises(KbFileError, match="missing from the markdown"):
        load_document(md_path)


def test_empty_blocks_dropped_and_counted(tmp_path: Path) -> None:
    body = (
        "<!-- dgx:block=empt:p001:b000 -->\nReal text here.\n\n"
        "<!-- dgx:block=empt:p001:b001 -->\n\n\n"
        "<!-- dgx:block=empt:p001:b002 -->\nMore real text.\n"
    )
    md_path = _write_doc(
        tmp_path,
        "empt",
        body,
        sidecar_blocks=[
            {"block_id": "empt:p001:b000", "page": 1, "kind": "paragraph", "confidence": "x"},
            {"block_id": "empt:p001:b001", "page": 1, "kind": "paragraph", "confidence": "x"},
            {"block_id": "empt:p001:b002", "page": 1, "kind": "paragraph", "confidence": "x"},
        ],
    )
    doc = load_document(md_path)
    assert doc.dropped_empty_blocks == 1
    assert len(doc.blocks) == 2
    assert [b.text for b in doc.blocks] == ["Real text here.", "More real text."]


def test_incomplete_pages_both_dash_forms(tmp_path: Path) -> None:
    body_em = "<!-- dgx:block=d1:p001:b000 -->\n> **INCOMPLETE — pages 1, 2 are image.**\n"
    md1 = _write_doc(
        tmp_path,
        "d1",
        body_em,
        sidecar_blocks=[{"block_id": "d1:p001:b000", "page": 1, "kind": "paragraph", "confidence": "x"}],
    )
    assert load_document(md1).incomplete_pages == [1, 2]

    body_hyphen = "<!-- dgx:block=d2:p001:b000 -->\n> **INCOMPLETE - page 5 is image.**\n"
    md2 = _write_doc(
        tmp_path,
        "d2",
        body_hyphen,
        sidecar_blocks=[{"block_id": "d2:p001:b000", "page": 1, "kind": "paragraph", "confidence": "x"}],
    )
    assert load_document(md2).incomplete_pages == [5]


def test_heading_block_not_starting_with_hash_defaults_level_3(tmp_path: Path) -> None:
    body = "<!-- dgx:block=h1:p001:b000 -->\nA Heading With No Hashes\n"
    md_path = _write_doc(
        tmp_path,
        "h1",
        body,
        sidecar_blocks=[{"block_id": "h1:p001:b000", "page": 1, "kind": "heading", "confidence": "x"}],
    )
    doc = load_document(md_path)
    assert doc.blocks[0].level == 3


def test_no_sidecar_fallback_splits_at_headings(tmp_path: Path) -> None:
    import hashlib

    body = "# Title\n\nIntro paragraph text.\n\n## Sub\n\nSub paragraph text.\n"
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    meta = fm_build(
        title="No Sidecar Doc",
        source_file="ns.docx",
        source_format="docx",
        converter="test",
        content_sha256=sha,
        text_class="clean",
        doc_date="UNCONFIRMED",
    )
    md_path = tmp_path / "ns.md"
    md_path.write_text(fm_render(meta, body), encoding="utf-8")

    doc = load_document(md_path)
    assert doc.has_sidecar is False
    kinds = [b.kind for b in doc.blocks]
    assert kinds == ["heading", "paragraph", "heading", "paragraph"]
    assert doc.blocks[0].level == 1
    assert doc.blocks[2].level == 2
    assert all(b.page is None for b in doc.blocks)
    assert all(b.block_id.startswith("ns:p000:b") for b in doc.blocks)
