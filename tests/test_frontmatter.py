"""The frontmatter contract. Validation is only worth having if it rejects things."""

from __future__ import annotations

import pytest

from pipeline.frontmatter import (
    FIELD_ORDER,
    FrontmatterError,
    build,
    parse_file,
    render,
    validate,
)

VALID = {
    "title": "Travel Reimbursement Handbook",
    "source_file": "rfsuny/travel_handbook.pdf",
    "source_format": "pdf",
    "source_url": None,
    "doc_date": "UNCONFIRMED",
    "retrieved": None,
    "converter": "docling (do_ocr=False)",
    "text_coverage": 412,
    "text_class": "clean",
    "needs_ocr": False,
    "content_sha256": "a" * 64,
}


def test_valid_metadata_passes():
    assert validate(VALID) == []


def test_build_sets_needs_ocr_from_text_class():
    meta = build(
        title="Scanned Appendix",
        source_file="a/b.pdf",
        source_format="pdf",
        converter="none",
        content_sha256="b" * 64,
        text_class="needs_ocr",
    )

    assert meta["needs_ocr"] is True
    assert meta["doc_date"] == "UNCONFIRMED"
    assert meta["source_url"] is None


def test_absolute_source_path_is_rejected():
    """The source root's own path must never end up in a kb/ file."""
    problems = validate({**VALID, "source_file": "/Users/ttl/documents/travel.pdf"})
    assert any("relative" in problem for problem in problems)


def test_needs_ocr_contradicting_text_class_is_rejected():
    problems = validate({**VALID, "text_class": "needs_ocr", "needs_ocr": False})
    assert any("contradicts" in problem for problem in problems)


@pytest.mark.parametrize(
    "doc_date",
    ["2026-09-09", "UNCONFIRMED"],
)
def test_accepted_doc_dates(doc_date):
    assert validate({**VALID, "doc_date": doc_date}) == []


@pytest.mark.parametrize(
    "doc_date",
    ["Sept 2026", "2026", "unconfirmed", "", None, "today"],
)
def test_rejected_doc_dates(doc_date):
    """A guessed or loosely formatted date is worse than an absent one."""
    problems = validate({**VALID, "doc_date": doc_date})
    assert any("doc_date" in problem for problem in problems)


@pytest.mark.parametrize(
    "digest",
    ["", "abc", "A" * 64, "g" * 64, None, 12345],
)
def test_bad_content_sha256_is_rejected(digest):
    problems = validate({**VALID, "content_sha256": digest})
    assert any("content_sha256" in problem for problem in problems)


def test_unknown_source_format_is_rejected():
    problems = validate({**VALID, "source_format": "rtf"})
    assert any("source_format" in problem for problem in problems)


def test_missing_field_is_reported():
    incomplete = {key: value for key, value in VALID.items() if key != "converter"}
    problems = validate(incomplete)
    assert any("converter" in problem for problem in problems)


def test_unexpected_field_is_reported():
    problems = validate({**VALID, "chunk_size": 512})
    assert any("chunk_size" in problem for problem in problems)


def test_render_refuses_invalid_metadata():
    with pytest.raises(FrontmatterError):
        render({**VALID, "text_class": "excellent"}, "# Body")


def test_render_is_stable_and_ordered(tmp_path):
    output = render(VALID, "# Heading\n\nBody text.")

    assert output.startswith("---\ntitle:")
    assert output.endswith("Body text.\n")
    assert output == render(VALID, "# Heading\n\nBody text.\n\n")

    keys = [
        line.split(":")[0]
        for line in output.split("---")[1].strip().splitlines()
        if not line.startswith(" ")
    ]
    assert keys == list(FIELD_ORDER)


def test_render_roundtrips_through_the_parser(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(render(VALID, "# Heading\n\nBody."), encoding="utf-8")

    assert validate(parse_file(path)) == []


def test_null_source_url_and_retrieved_are_valid():
    """Locally provided samples have neither a URL nor a retrieval date."""
    assert validate({**VALID, "source_url": None, "retrieved": None}) == []


def test_text_coverage_null_for_text_native_formats():
    assert validate({**VALID, "source_format": "csv", "text_coverage": None}) == []
