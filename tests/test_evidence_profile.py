"""The evidence profile: what a document offered, reported next to what was recovered.

The failure this exists to catch is the silent one. A three-page ruled budget form converted
with `text_class: clean`, no warnings, and no missing-page note — and its grid reduced to a
single table, which is to say gone. Every signal the pipeline emitted said the conversion was
fine, and the only way anyone found out was by asking the document a question by hand.

Counting what each document *offers* — annotations, callout lines, form fields, ruled tables
— turns that into a line of output. It also stops the converter being tuned to whichever
evidence class happened to appear in the document someone looked at first: a document type
nobody has handled shows up as an unfamiliar profile rather than as a quiet degradation.
"""

from __future__ import annotations

from pipeline import report as report_module
from pipeline.converters import pdf_geometry
from pipeline.triage import triage_pdf


# --------------------------------------------------------------------------------------
# Counting the evidence
# --------------------------------------------------------------------------------------


def test_analyse_counts_annotations_and_callout_lines(fixtures_dir, config):
    pages = pdf_geometry.analyse(fixtures_dir / "linked_form.pdf", config)
    assert sum(page.annotations for page in pages) == 5
    assert sum(page.callout_lines for page in pages) == 2


def test_analyse_counts_form_fields(fixtures_dir, config):
    pages = pdf_geometry.analyse(fixtures_dir / "linked_form.pdf", config)
    assert sum(page.form_fields for page in pages) == 2


def test_a_document_with_no_annotations_counts_none(fixtures_dir, config):
    pages = pdf_geometry.analyse(fixtures_dir / "born_digital.pdf", config)
    assert sum(page.annotations for page in pages) == 0
    assert sum(page.form_fields for page in pages) == 0


def test_triage_carries_the_profile(fixtures_dir, config):
    result = triage_pdf(fixtures_dir / "linked_form.pdf", config)
    assert result.annotations == 5
    assert result.annotations_with_callout == 2
    assert result.form_fields == 2


def test_the_profile_survives_the_yaml_round_trip(fixtures_dir, config):
    """It has to reach the report, which reads the manifest rather than the PDF."""
    import yaml

    result = triage_pdf(fixtures_dir / "linked_form.pdf", config)
    restored = yaml.safe_load(yaml.safe_dump(result.to_dict()))
    assert restored["annotations"] == 5
    assert restored["annotations_with_callout"] == 2
    assert restored["form_fields"] == 2


def test_a_profile_failure_never_invalidates_the_coverage_measurement(
    fixtures_dir, config, monkeypatch
):
    """Diagnostics are diagnostics. A page whose annots cannot be read still gets counted."""
    monkeypatch.setattr(
        pdf_geometry, "analyse", lambda *_: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    result = triage_pdf(fixtures_dir / "born_digital.pdf", config)
    assert result.page_count == 3
    assert result.annotations == 0


# --------------------------------------------------------------------------------------
# Surfacing it
# --------------------------------------------------------------------------------------


def _manifest(**triage):
    base = {
        "page_count": 3, "chars_per_page_median": 400.0, "chars_per_page_mean": 400.0,
        "alpha_ratio": 1.0, "low_page_fraction": 0.0, "low_pages": [], "text_class": "clean",
        "error": None, "max_columns": 1, "ruled_tables": 0, "borderless_table_pages": 0,
        "annotations": 0, "annotations_with_callout": 0, "form_fields": 0,
    }
    base.update(triage)
    return {"documents": [{"source_file": "a.pdf", "source_format": "pdf",
                           "status": "converted", "triage": base}]}


def test_the_report_carries_the_profile(config):
    built = report_module.build(_manifest(annotations=205, annotations_with_callout=71), config)
    row = built["documents"][0]
    assert row["annotations"] == 205
    assert row["annotations_with_callout"] == 71


def test_a_form_with_fields_but_no_recovered_grid_is_flagged(config):
    """The NIFA signature: form fields present, ruled tables absent — the grid is gone.

    This is the exact shape that converted 'successfully' while losing a budget table, and
    the reason the profile exists at all.
    """
    built = report_module.build(_manifest(form_fields=13, ruled_tables=1), config)
    text = report_module.render_table(built)
    assert "a.pdf" in text
    assert "13 form field" in text


def test_a_document_whose_instructions_are_annotations_is_flagged(config):
    """Annotations are not in the text layer, so their absence is invisible downstream."""
    built = report_module.build(_manifest(annotations=205, annotations_with_callout=71), config)
    text = report_module.render_table(built)
    assert "205 annotation" in text


def test_a_plain_document_raises_no_evidence_notes(config):
    text = report_module.render_table(report_module.build(_manifest(), config))
    assert "EVIDENCE" not in text
