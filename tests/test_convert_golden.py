"""Golden-file comparison: converted output must match, byte for byte.

When a Docling version bump legitimately changes output, regenerate the goldens in a separate
commit that changes nothing else. A golden edited in the same commit as a code change hides
exactly the regression the golden exists to catch.

The CSV and DOCX goldens exist. The PDF goldens do not: producing them means downloading
Docling's layout model, whose base-weight provenance `models.yaml` has not cleared. Those
cases skip loudly rather than silently passing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.convert import convert_entry

GOLDEN_BY_FIXTURE = {
    "reference_table.csv": "reference_table.md",
    "born_digital.pdf": "born_digital.md",
    "callout_notes.pdf": "callout_notes.md",
    "mixed.pdf": "mixed.md",
    "annotated_form.pdf": "annotated_form.md",
    "linked_form.pdf": "linked_form.md",
    "ruled_form.pdf": "ruled_form.md",
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


def test_pdf_converts_with_the_model_free_engine_by_default(config, entry_for):
    """Geometry is the default, so a PDF converts with no model and no network."""
    result = convert_entry(entry_for("born_digital.pdf"), config)

    assert result.status == "written"
    assert "model-free" in result.converter
    assert "# Travel Reimbursement Handbook" in result.output.read_text()


def test_docling_escalation_fails_loudly_until_it_is_cleared(config, entry_for, monkeypatch):
    """Opting into Docling must explain what is missing, not silently fall back."""
    monkeypatch.setenv("PDF_ENGINE", "docling")
    from pipeline import config as config_module

    escalated = config_module.load(source_dir=config.source_dir())

    with pytest.raises(NotImplementedError, match="pending_review"):
        convert_entry(entry_for("born_digital.pdf"), escalated, force=True)


def test_docx_conversion_downloads_no_model(config, entry_for, tmp_path, monkeypatch):
    """DOCX is pure parsing. If this starts fetching, the model gate has been bypassed.

    The PDF path needs layout and table-structure models that `models.yaml` has not cleared;
    DOCX needs none, which is the whole reason it could ship first. Assert that rather than
    trusting it to stay true across Docling versions.
    """
    cache = tmp_path / "model-cache"
    cache.mkdir()
    monkeypatch.setenv("HF_HOME", str(cache))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    result = convert_entry(entry_for("simple.docx"), config, force=True)

    assert result.status == "written"
    assert list(cache.rglob("*")) == []


def test_docx_output_is_well_formed_markdown(config, entry_for):
    """A table jammed against a list renders as literal pipes, so it must be separated."""
    text = convert_entry(entry_for("simple.docx"), config, force=True).output.read_text()
    lines = text.splitlines()

    for index, line in enumerate(lines):
        if line.startswith("|") and index and not lines[index - 1].startswith("|"):
            assert lines[index - 1].strip() == "", "table must be preceded by a blank line"

    assert "## Travel Reimbursement Handbook" in text
    assert "- Lodging" in text
    assert "<table" not in text and "<p>" not in text  # no raw HTML
