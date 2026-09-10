"""Triage: does the measurement actually separate usable text layers from unusable ones."""

from __future__ import annotations

import pytest

from pipeline.triage import alpha_ratio, classify, triage_pdf, triage_text_native

THRESHOLDS = {
    "min_chars_per_page": 100,
    "min_alpha_ratio": 0.60,
    "max_low_page_fraction": 0.20,
}


@pytest.mark.parametrize(
    ("filename", "expected_class"),
    [
        ("born_digital.pdf", "clean"),
        ("image_only.pdf", "needs_ocr"),
        ("mixed.pdf", "partial"),
        ("mojibake.pdf", "needs_ocr"),
    ],
)
def test_fixture_classification(fixtures_dir, config, filename, expected_class):
    result = triage_pdf(fixtures_dir / filename, config)
    assert result.text_class == expected_class


def test_mojibake_is_text_rich_but_fails_on_alpha_ratio(fixtures_dir, config):
    """The reason the alpha check exists: character count alone scores this file well."""
    result = triage_pdf(fixtures_dir / "mojibake.pdf", config)

    assert result.chars_per_page_median > config.min_chars_per_page * 10
    assert result.pages_below_threshold == 0
    assert result.alpha_ratio < config.min_alpha_ratio
    assert result.text_class == "needs_ocr"


def test_image_only_extracts_nothing(fixtures_dir, config):
    result = triage_pdf(fixtures_dir / "image_only.pdf", config)
    assert result.chars_per_page_mean == 0.0
    assert result.alpha_ratio == 0.0


def test_mixed_is_partial_not_clean(fixtures_dir, config):
    """A handbook with a scanned appendix is convertible, but not completely."""
    result = triage_pdf(fixtures_dir / "mixed.pdf", config)
    assert result.pages_below_threshold == 1
    assert result.low_page_fraction > config.max_low_page_fraction
    assert result.text_class == "partial"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0.0),
        ("plain ascii text.", 1.0),
        ("Ärger über Straßen", 1.0),
        ("����", 0.0),
        ("ab��", 0.5),
    ],
)
def test_alpha_ratio(text, expected):
    assert alpha_ratio(text) == pytest.approx(expected)


def test_median_not_mean_decides_the_class():
    """A few dense pages must not mask a scanned majority."""
    pages = ["x" * 5000] + [""] * 9

    result = classify(pages, **THRESHOLDS)

    assert result.chars_per_page_mean == 500.0  # a mean-based rule would say "clean"
    assert result.chars_per_page_median == 0.0
    assert result.text_class == "needs_ocr"


def test_zero_page_pdf_is_an_error_not_a_class():
    result = classify([], **THRESHOLDS)
    assert result.text_class == "error"
    assert result.error


def test_low_page_fraction_boundary_is_inclusive():
    """Exactly at the maximum is still `clean`; the class changes only above it."""
    pages = ["x" * 500] * 8 + [""] * 2

    at_limit = classify(pages, **{**THRESHOLDS, "max_low_page_fraction": 0.20})
    below_limit = classify(pages, **{**THRESHOLDS, "max_low_page_fraction": 0.19})

    assert at_limit.text_class == "clean"
    assert below_limit.text_class == "partial"


def test_thresholds_come_from_config(fixtures_dir, monkeypatch):
    """Raising the bar reclassifies a document, proving nothing is hard-coded."""
    from pipeline import config as config_module

    monkeypatch.setenv("MIN_CHARS_PER_PAGE", "5000")
    strict = config_module.load(source_dir=fixtures_dir)

    assert triage_pdf(fixtures_dir / "born_digital.pdf", strict).text_class == "needs_ocr"


def test_text_native_formats_skip_measurement(fixtures_dir, config):
    result = triage_text_native(fixtures_dir / "simple.docx", config)
    assert result.text_class == "clean"
    assert result.chars_per_page_mean is None
    assert result.alpha_ratio is None


def test_unreadable_pdf_is_recorded_not_raised(tmp_path, config):
    """A corrupt source must not fail the whole run."""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nnot actually a pdf\n")

    result = triage_pdf(broken, config)

    assert result.text_class == "error"
    assert result.error


def test_low_pages_are_identified_by_number():
    """A `clean` document can still hold pages with no usable text; name which ones."""
    pages = ["x" * 500, "", "x" * 500, "", "x" * 500] + ["x" * 500] * 15

    result = classify(pages, **THRESHOLDS)

    assert result.text_class == "clean"
    assert result.low_pages == (2, 4)
    assert result.pages_below_threshold == 2


def test_low_pages_survive_the_yaml_round_trip():
    """Recorded as a list, so a second write cannot differ from the first."""
    import yaml

    result = classify(["", "x" * 500], **THRESHOLDS)
    data = result.to_dict()

    assert data["low_pages"] == [1]
    assert yaml.safe_load(yaml.safe_dump(data))["low_pages"] == [1]


def test_mixed_fixture_names_its_scanned_page(fixtures_dir, config):
    result = triage_pdf(fixtures_dir / "mixed.pdf", config)
    assert result.low_pages == (3,)
