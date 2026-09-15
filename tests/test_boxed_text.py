"""Text set in a drawn box, which is how a flattened callout survives in a PDF.

Not every note on a form is a PDF annotation object. When commentary is typed into a box and
the file is flattened -- or authored that way to begin with -- the note becomes ordinary page
text inside an ordinary rectangle, and three things go wrong at once.

It is indistinguishable from the form's own words, so nothing downstream can tell commentary
from content. Boxes sharing a baseline merge into a single interleaved line, because line
grouping knows only about baselines: on a real specimen, six boxes across the top of a budget
form came out as `Federal Matching Amount Corresponding Amount Total lines funds carry funds
carry from`, which answers nothing and matches no search. And the fact that the text was set
apart at all -- which is the author's own signal that it is commentary -- is lost.

A drawn box containing text is a measurement, so that is what gets reported: `Boxed text`,
not `Annotation`. These are not annotation objects and must not claim to be.
"""

from __future__ import annotations

from pipeline import config as config_module
from pipeline.converters import pdf_geometry
from pipeline.converters.pdf_geometry import BOXED_PREFIX

FIXTURE = "boxed_notes.pdf"


def converted(fixtures_dir, config):
    return pdf_geometry.to_markdown(fixtures_dir / FIXTURE, config)


def test_boxes_on_one_baseline_stay_separate(fixtures_dir, config):
    """The failure that makes a marked-up form unreadable."""
    body = converted(fixtures_dir, config)
    assert f"{BOXED_PREFIX}Federal funds carry over" in body
    assert f"{BOXED_PREFIX}Matching funds carry over" in body
    assert f"{BOXED_PREFIX}Amount from Appendix A" in body
    assert "Federal funds carry over Matching funds carry over" not in body


def test_a_multi_line_box_is_one_block(fixtures_dir, config):
    body = converted(fixtures_dir, config)
    assert f"{BOXED_PREFIX}Required match - note, this amount accounts for a waiver." in body


def test_boxed_text_is_not_called_an_annotation(fixtures_dir, config):
    """It is page text in a rectangle, not a PDF annotation object. Saying so would overclaim."""
    body = converted(fixtures_dir, config)
    assert "**Annotation" not in body


def test_ordinary_prose_is_untouched(fixtures_dir, config):
    body = converted(fixtures_dir, config)
    assert "Continue with the instructions on the next page." in body
    assert f"{BOXED_PREFIX}Continue with" not in body


def test_a_shaded_table_cell_is_not_a_note(fixtures_dir, config):
    """A shaded header is a filled rect holding text, and it is part of the table."""
    body = converted(fixtures_dir, config)
    assert "| Category | Federal | Match |" in body
    assert f"{BOXED_PREFIX}Category" not in body


def test_the_table_is_unharmed(fixtures_dir, config):
    body = converted(fixtures_dir, config)
    assert "| Personnel | 250,000 | 125,000 |" in body


def test_boxes_are_emitted_in_reading_order(fixtures_dir, config):
    body = converted(fixtures_dir, config)
    positions = [
        body.index(text)
        for text in ("Federal funds carry over", "Required match", "Continue with")
    ]
    assert positions == sorted(positions)


def test_a_document_with_no_boxes_is_unchanged(fixtures_dir, config):
    body = pdf_geometry.to_markdown(fixtures_dir / "born_digital.pdf", config)
    assert BOXED_PREFIX not in body


def test_boxed_text_can_be_switched_off(fixtures_dir, monkeypatch):
    """Off, the text must still be present -- unmarked and merged, but never dropped."""
    monkeypatch.setenv("PDF_BOXED_TEXT", "false")
    disabled = config_module.load(source_dir=fixtures_dir)
    body = pdf_geometry.to_markdown(fixtures_dir / FIXTURE, disabled)
    assert BOXED_PREFIX not in body
    assert "Federal funds carry over" in body


def test_an_unparseable_switch_is_an_error(fixtures_dir, monkeypatch):
    import pytest

    monkeypatch.setenv("PDF_BOXED_TEXT", "perhaps")
    with pytest.raises(config_module.ConfigError, match="PDF_BOXED_TEXT"):
        config_module.load(source_dir=fixtures_dir)


def test_a_page_sized_box_is_not_a_note(fixtures_dir, config):
    """A border or background rect wraps the whole page; it is not commentary about it."""
    assert not pdf_geometry._is_note_box(
        {"x0": 0, "top": 0, "x1": 612, "bottom": 792, "fill": True, "stroke": True},
        page_area=612 * 792,
        max_area_fraction=config.pdf_boxed_max_area,
    )


def test_an_ordinary_note_box_qualifies(config):
    assert pdf_geometry._is_note_box(
        {"x0": 72, "top": 100, "x1": 220, "bottom": 124, "fill": True, "stroke": True},
        page_area=612 * 792,
        max_area_fraction=config.pdf_boxed_max_area,
    )
