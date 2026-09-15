"""Pages whose substance is a picture, in documents that otherwise look fine.

The specimen: a three-page annotated budget form where each page carries a raster screenshot
of the form covering about a third of the page, and the only real text is the callouts
someone typed alongside it. None of the form's rows, cells or values are in the text layer,
and no table extraction can reach them — the page has zero ruled lines because there is
nothing vector on it to rule.

Every coverage metric is defeated at once, and none of them is wrong. Character count is
healthy, because callouts are text. The alpha ratio is perfect, because that text is clean.
No page is below the low-text threshold, so nothing is named as missing. The document
triages `clean`, converts without a warning, and silently omits the form it is about.

The fix is not reclassification — a document with a large diagram or a logo is not broken,
and guessing which is which is exactly the inference this pipeline refuses to make. It is to
say what was measured: these pages are mostly image, and that content is not in the text.
"""

from __future__ import annotations

from pipeline import report as report_module
from pipeline.converters import pdf
from pipeline.triage import triage_pdf

FIXTURE = "screenshot_form.pdf"


def test_an_image_dominant_page_is_identified(fixtures_dir, config):
    result = triage_pdf(fixtures_dir / FIXTURE, config)
    assert result.image_pages == (1,)


def test_the_coverage_fraction_is_recorded(fixtures_dir, config):
    """A number, so the threshold can be argued with rather than trusted."""
    result = triage_pdf(fixtures_dir / FIXTURE, config)
    assert 0.25 < result.max_image_coverage < 0.45


def test_a_text_document_has_no_image_pages(fixtures_dir, config):
    result = triage_pdf(fixtures_dir / "born_digital.pdf", config)
    assert result.image_pages == ()
    assert result.max_image_coverage == 0.0


def test_the_threshold_comes_from_config(fixtures_dir, monkeypatch):
    from pipeline import config as config_module

    monkeypatch.setenv("IMAGE_PAGE_COVERAGE", "0.9")
    strict = config_module.load(source_dir=fixtures_dir)
    assert triage_pdf(fixtures_dir / FIXTURE, strict).image_pages == ()


def test_the_class_is_not_silently_changed(fixtures_dir, config):
    """A page can be a third diagram and perfectly fine. Reporting is not reclassifying."""
    assert triage_pdf(fixtures_dir / FIXTURE, config).text_class == "clean"


def test_image_pages_survive_the_yaml_round_trip(fixtures_dir, config):
    import yaml

    result = triage_pdf(fixtures_dir / FIXTURE, config)
    restored = yaml.safe_load(yaml.safe_dump(result.to_dict()))
    assert restored["image_pages"] == [1]


# --------------------------------------------------------------------------------------
# Saying so, where it will be seen
# --------------------------------------------------------------------------------------


def test_the_converted_file_says_so(fixtures_dir, config, entry_for):
    """In the file itself, because that is all anything downstream ever sees."""
    from pipeline.convert import convert_entry

    result = convert_entry(entry_for(FIXTURE), config)
    body = result.output.read_text(encoding="utf-8")
    assert "page 1" in body
    assert "image" in body.lower()
    assert "No OCR was attempted" in body


def test_a_clean_text_document_gets_no_such_note(fixtures_dir, config, entry_for):
    from pipeline.convert import convert_entry

    body = convert_entry(entry_for("born_digital.pdf"), config).output.read_text("utf-8")
    assert "mostly image" not in body


def test_the_report_names_the_document(config):
    manifest = {
        "documents": [
            {
                "source_file": "a.pdf", "source_format": "pdf", "status": "converted",
                "triage": {
                    "page_count": 3, "chars_per_page_median": 405.0,
                    "chars_per_page_mean": 405.0, "alpha_ratio": 1.0,
                    "low_page_fraction": 0.0, "low_pages": [], "text_class": "clean",
                    "error": None, "max_columns": 1, "ruled_tables": 1,
                    "borderless_table_pages": 0, "annotations": 0,
                    "annotations_with_callout": 0, "form_fields": 13,
                    "image_pages": [1, 2, 3], "max_image_coverage": 0.37,
                },
            }
        ]
    }
    text = report_module.render_table(report_module.build(manifest, config))
    assert "3 page(s) that are mostly image" in text
    assert "37%" in text


def test_the_note_lists_the_pages(fixtures_dir):
    assert pdf.image_pages_note((2, 5), 0.41) == (
        "> **INCOMPLETE — pages 2, 5 are mostly image (up to 41% of the page), and that "
        "content is not in the text layer.** No OCR was attempted."
    )


def test_no_pages_means_no_note():
    assert pdf.image_pages_note((), 0.0) == ""
