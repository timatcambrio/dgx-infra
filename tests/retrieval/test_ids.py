"""`retrieval.ids` (brief §5.5)."""

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
