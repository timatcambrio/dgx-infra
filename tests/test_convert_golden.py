"""Golden-file comparison: converted output must match, byte for byte.

When a Docling version bump legitimately changes output, regenerate the goldens in a separate
commit that changes nothing else. A golden edited in the same commit as a code change hides
exactly the regression the golden exists to catch.

Only the CSV golden exists today. The PDF and DOCX goldens are M2 artefacts: producing them
means running Docling, which is the M2 converter, and downloading its layout and table
models — which `models.yaml` does not yet permit, because their licence and base-weight
provenance are unresolved. Those tests skip loudly rather than silently passing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.convert import convert_entry

GOLDEN_BY_FIXTURE = {
    "reference_table.csv": "reference_table.md",
    "born_digital.pdf": "born_digital.md",
    "mixed.pdf": "mixed.md",
    "simple.docx": "simple.md",
}


def convert_fixture(fixture_name: str, config, entry_for) -> str:
    result = convert_entry(entry_for(fixture_name), config)
    assert result.output is not None, f"{fixture_name} produced no output: {result.message}"
    return result.output.read_text(encoding="utf-8")


@pytest.mark.parametrize("fixture_name", sorted(GOLDEN_BY_FIXTURE))
def test_matches_golden(fixture_name, config, golden_dir, entry_for):
    golden = golden_dir / GOLDEN_BY_FIXTURE[fixture_name]
    if not golden.is_file():
        pytest.skip(
            f"{golden.name} not generated yet — this is an M2 artefact, since producing it "
            "requires running Docling and downloading models that models.yaml has not "
            "cleared."
        )

    assert convert_fixture(fixture_name, config, entry_for) == golden.read_text(encoding="utf-8")


def test_at_least_one_golden_exists(golden_dir):
    """Guards against the whole suite above silently skipping into vacuity."""
    assert list(golden_dir.glob("*.md")), "no golden files at all — nothing is being compared"


def test_csv_golden_content_is_what_we_think_it_is(config, golden_dir):
    """A golden nobody has read is just a hash of whatever the code did on the day."""
    text = (golden_dir / "reference_table.md").read_text(encoding="utf-8")

    assert text.startswith("---\ntitle: Reference Table\n")
    assert "source_format: csv" in text
    assert "text_coverage: null" in text
    assert "needs_ocr: false" in text
    assert "\n# Reference Table\n" in text
    assert "| Code | Expense category | Limit | Receipt required |" in text
    assert r"Conference registration \| fees" in text
    assert text.endswith("|\n")


def test_needs_ocr_pdf_emits_a_stub_not_an_empty_file(config, entry_for):
    """The stub must be visibly incomplete rather than plausibly empty."""
    entry = entry_for("image_only.pdf")
    entry["triage"] = {"text_class": "needs_ocr", "chars_per_page_mean": 0.0}

    result = convert_entry(entry, config)
    text = result.output.read_text(encoding="utf-8")

    assert result.status == "stub"
    assert "INCOMPLETE" in text
    assert "Phase 2 OCR" in text
    assert "needs_ocr: true" in text
    assert "text_class: needs_ocr" in text
    assert len(text.strip()) > 200


def test_needs_ocr_stub_keeps_salvageable_text(config, entry_for):
    """A mojibake file has no *usable* text, but discarding what was there hides evidence."""
    entry = entry_for("mojibake.pdf")
    entry["triage"] = {"text_class": "needs_ocr", "chars_per_page_mean": 2574.0}

    text = convert_entry(entry, config).output.read_text(encoding="utf-8")

    assert "INCOMPLETE" in text
    assert "Partial text recovered" in text


def test_docling_backed_formats_are_not_silently_skipped(config, entry_for):
    """Until M2 lands, PDF and DOCX conversion must fail loudly, not emit an empty file."""
    entry = entry_for("simple.docx")

    with pytest.raises(NotImplementedError, match="M2"):
        convert_entry(entry, config)
