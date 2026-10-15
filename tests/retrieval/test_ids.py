"""`retrieval.ids`."""

from __future__ import annotations

import pytest

from retrieval import ids


def test_doc_id() -> None:
    assert ids.doc_id("handbook") == "doc:handbook"
    assert ids.parse_id("doc:handbook") == ("doc", "handbook", None)


def test_sec_id() -> None:
    assert ids.sec_id("handbook", 3) == "sec:handbook:3"
    assert ids.parse_id("sec:handbook:3") == ("sec", "handbook", 3)


def test_page_id_zero_padded() -> None:
    assert ids.page_id("handbook", 7) == "page:handbook:p007"
    assert ids.parse_id("page:handbook:p007") == ("page", "handbook", 7)


def test_page_id_large_page() -> None:
    assert ids.page_id("handbook", 123) == "page:handbook:p123"
    assert ids.parse_id("page:handbook:p123") == ("page", "handbook", 123)


def test_chunk_id() -> None:
    assert ids.chunk_id("budget-form", 12) == "chunk:budget-form:12"
    assert ids.parse_id("chunk:budget-form:12") == ("chunk", "budget-form", 12)


def test_slug_with_hyphens_and_digits() -> None:
    assert ids.parse_id("doc:reference-table-2") == ("doc", "reference-table-2", None)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "handbook",
        "doc:",
        "doc:Handbook",  # uppercase not allowed
        "doc:-handbook",  # cannot start with a hyphen
        "doc:__handbook-pdf",  # cannot start with the folder separator
        "doc:_handbook-pdf",
        "doc:gsa__",  # a separator must be followed by a name
        "doc:gsa___travel-pdf",  # three underscores is not a separator
        "doc:gsa__-travel-pdf",  # a folder part cannot start with a hyphen either
        "doc:gsa_travel-pdf",  # one underscore is not a separator
        "sec:gsa__travel-pdf",  # missing index
        "page:gsa__travel-pdf:p7",
        "sec:handbook",  # missing index
        "sec:handbook:abc",
        "page:handbook:7",  # missing p prefix
        "page:handbook:p7",  # not zero-padded to 3
        "chunk:handbook:",
        "unknown:handbook:1",
    ],
)
def test_parse_id_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        ids.parse_id(bad)


def test_roundtrip_all_kinds() -> None:
    assert ids.parse_id(ids.doc_id("x")) == ("doc", "x", None)
    assert ids.parse_id(ids.sec_id("x", 0)) == ("sec", "x", 0)
    assert ids.parse_id(ids.page_id("x", 1)) == ("page", "x", 1)
    assert ids.parse_id(ids.chunk_id("x", 0)) == ("chunk", "x", 0)


@pytest.mark.parametrize(
    "slug",
    ["gsa__travel-pdf", "rfsuny__2026__travel-pdf", "a__b"],
)
def test_roundtrip_slug_from_a_subfolder(slug: str) -> None:
    """`pipeline.manifest.slug_for` joins folders with `__`, so `gsa/travel.pdf` is
    `gsa__travel-pdf`. The id pattern allowed no underscore, so every document in a
    SOURCE_DIR subfolder was indexed but could not be fetched, outlined or paged by id:
    `parse_id("doc:gsa__travel-pdf")` raised "not a recognised kb id". The proxy corpus is
    flat, so nothing showed it."""
    assert ids.parse_id(ids.doc_id(slug)) == ("doc", slug, None)
    assert ids.parse_id(ids.sec_id(slug, 4)) == ("sec", slug, 4)
    assert ids.parse_id(ids.page_id(slug, 12)) == ("page", slug, 12)
    assert ids.parse_id(ids.chunk_id(slug, 31)) == ("chunk", slug, 31)


def test_every_slug_manifest_makes_from_a_nested_path_parses() -> None:
    """The id pattern is the reader's half of `slug_for`'s guarantee; check it against the
    writer rather than a hand-copied example."""
    from pipeline.manifest import slug_for

    for path in ("gsa/travel.pdf", "Policies/HR Team/Leave 2026.docx", "a/b/c/deck.pptx"):
        slug = slug_for(path)
        assert "__" in slug
        assert ids.parse_id(ids.doc_id(slug)) == ("doc", slug, None)
