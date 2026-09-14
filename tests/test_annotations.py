"""Text annotations: content attached to a page rather than printed on it.

Why this exists at all. A "Markup" callout -- the box someone types into when annotating a
form in Preview or Acrobat -- is a PDF annotation object, not page content, so **no
text-layer extraction sees it**. On a real institutional document that turned out to matter
more than any other single difference between engines: a 7-page funding-request tutorial in
the sample corpus carries 18 such callouts (`select the appropriate FISCAL YEAR`, `don't
forget to attach a copy of the draft SOW`), and none of them appear in the text layer. They
are the instructions. Without them the converted file is a blank form.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import config as config_module
from pipeline.converters import pdf, pdf_geometry
from pipeline.converters.pdf_geometry import ANNOTATION_PREFIX, Annotation

FIXTURE = "annotated_form.pdf"


class FakePage:
    """Minimal stand-in for a pdfplumber page.

    The interesting failure modes -- a flattened duplicate, a form widget, a malformed annot
    dictionary -- are all awkward to express in a generated PDF and trivial to express here.
    """

    def __init__(self, annots, text=""):
        self._annots = annots
        self._text = text

    @property
    def annots(self):
        if self._annots is _BROKEN:
            raise RuntimeError("annotation table is corrupt")
        return self._annots

    def extract_text(self):
        return self._text


_BROKEN = object()


class FakeSubtype:
    """pdfminer hands back a PSLiteral, whose name is the subtype."""

    def __init__(self, name):
        self.name = name


def annot(subtype, contents, top=100.0, x0=10.0):
    return {"data": {"Subtype": FakeSubtype(subtype)}, "contents": contents,
            "top": top, "x0": x0}


# --------------------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------------------


def test_freetext_and_sticky_notes_are_extracted():
    page = FakePage([annot("FreeText", "select the appropriate FISCAL YEAR"),
                     annot("Text", "check with FM first")])
    found = pdf_geometry.extract_annotations(page, "")
    assert [a.text for a in found] == [
        "select the appropriate FISCAL YEAR",
        "check with FM first",
    ]


def test_form_widgets_are_not_treated_as_annotations():
    """A Widget's value is already drawn on the page; emitting it would duplicate it."""
    page = FakePage([annot("Widget", "Jane Doe")], text="Jane Doe")
    assert pdf_geometry.extract_annotations(page, "Jane Doe") == []


def test_a_flattened_annotation_is_not_emitted_twice():
    """Flattening (Print to PDF, Save As Flattened) leaves both the drawn text and the annot."""
    page = FakePage([annot("FreeText", "YOUR NAME")], text="Form\nYOUR NAME\nSignature")
    assert pdf_geometry.extract_annotations(page, "Form\nYOUR NAME\nSignature") == []


def test_empty_annotations_are_skipped():
    page = FakePage([annot("FreeText", "   "), annot("FreeText", None)])
    assert pdf_geometry.extract_annotations(page, "") == []


def test_a_corrupt_annotation_table_loses_the_annotations_not_the_page():
    """Failing hard here would take the page's printed text down with it."""
    assert pdf_geometry.extract_annotations(FakePage(_BROKEN), "") == []


def test_one_malformed_annotation_does_not_lose_the_others():
    page = FakePage([{"data": None, "contents": "junk"},
                     annot("FreeText", "keep me")])
    assert [a.text for a in pdf_geometry.extract_annotations(page, "")] == ["keep me"]


def test_bytes_contents_are_decoded():
    page = FakePage([annot("FreeText", b"attach the signed IGCE")])
    assert [a.text for a in pdf_geometry.extract_annotations(page, "")] == [
        "attach the signed IGCE"
    ]


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def test_rendering_marks_the_text_as_an_annotation():
    """A citation must be able to distinguish the form's words from someone's note on it."""
    rendered = Annotation(top=1.0, x0=1.0, text="select the appropriate FUND").render()
    assert rendered == f"{ANNOTATION_PREFIX}select the appropriate FUND"


def test_multi_line_annotations_stay_inside_the_blockquote():
    """PDF annotation contents break lines with \\r; one unmarked line ends the quote."""
    rendered = Annotation(top=1.0, x0=1.0, text="Upload all attachments\r- SOW\r- IGCE").render()
    assert rendered.splitlines() == [
        f"{ANNOTATION_PREFIX}Upload all attachments",
        "> - SOW",
        "> - IGCE",
    ]


# --------------------------------------------------------------------------------------
# End to end, against the committed fixture
# --------------------------------------------------------------------------------------


def test_annotations_appear_in_converted_output(config, fixtures_dir):
    body = pdf_geometry.to_markdown(fixtures_dir / FIXTURE, config)
    for callout in ("YOUR NAME", "select the appropriate FISCAL YEAR",
                    "enter the TOTAL REQUEST AMOUNT"):
        assert callout in body, f"{callout!r} was dropped"


def test_annotations_are_ordered_by_position_not_by_source_order(config, fixtures_dir):
    """The fixture stores them bottom-up; reading order is what the output must follow.

    This is the half Marker got wrong on the real document -- it emitted blocks in the PDF's
    internal storage order, which put the page title several blocks below the form fields.
    """
    body = pdf_geometry.to_markdown(fixtures_dir / FIXTURE, config)
    positions = [body.index(text) for text in (
        "YOUR NAME", "select the appropriate FISCAL YEAR", "enter the TOTAL REQUEST AMOUNT"
    )]
    assert positions == sorted(positions)


def test_each_annotation_sits_next_to_the_field_it_describes(config, fixtures_dir):
    body = pdf_geometry.to_markdown(fixtures_dir / FIXTURE, config)
    assert body.index("Requester:") < body.index("YOUR NAME") < body.index("Fiscal year:")
    assert body.index("Fiscal year:") < body.index("FISCAL YEAR") < body.index("Amount:")


def test_annotations_can_be_switched_off(fixtures_dir, monkeypatch):
    """The switch exists to isolate the feature when comparing engines, not as a default."""
    monkeypatch.setenv("PDF_ANNOTATIONS", "false")
    disabled = config_module.load(source_dir=fixtures_dir)
    body = pdf_geometry.to_markdown(fixtures_dir / FIXTURE, disabled)
    assert "YOUR NAME" not in body
    assert "Funding Request Form" in body, "the page's own text must be unaffected"


def test_an_unparseable_switch_value_is_an_error(fixtures_dir, monkeypatch):
    monkeypatch.setenv("PDF_ANNOTATIONS", "sure")
    with pytest.raises(config_module.ConfigError, match="PDF_ANNOTATIONS"):
        config_module.load(source_dir=fixtures_dir)


def test_the_needs_ocr_stub_still_carries_annotations(config, fixtures_dir):
    """A scanned form marked up electronically: the callouts are its only readable text."""
    body, _ = pdf.needs_ocr_stub(fixtures_dir / FIXTURE, config)
    assert "Annotations (not printed on the page)" in body
    assert "select the appropriate FISCAL YEAR" in body


def test_documents_without_annotations_are_unchanged(config, fixtures_dir):
    """The feature must be invisible on the corpus it does not apply to."""
    body = pdf_geometry.to_markdown(fixtures_dir / "born_digital.pdf", config)
    assert ANNOTATION_PREFIX not in body


# --------------------------------------------------------------------------------------
# Binding an annotation to the field it describes
# --------------------------------------------------------------------------------------
#
# Position alone answers "which field is this callout about?" only while a page is sparse.
# On a real form the callouts stack up in one margin column and the fields they point at do
# not, so ordering by vertical position emits a run of notes with nothing to say which is
# which -- and a reader downstream, who has no access to the PDF, cannot recover it.
#
# Three things in the file can answer it, and they are tried in order of how much they are
# really saying: a callout line, which is the annotator pointing at the field; a form
# widget, which carries the field's own name; and failing both, overlap with a printed
# label, which is an inference and is marked as one.


LINKED = "linked_form.pdf"


def widget(name, x0=230.0, top=130.0, x1=350.0, bottom=148.0):
    data = {"Subtype": FakeSubtype("Widget"), "T": name}
    return {"data": data, "contents": None, "top": top, "x0": x0,
            "x1": x1, "bottom": bottom}


def free_text(contents, top=100.0, x0=410.0, x1=560.0, bottom=118.0, callout=None):
    data = {"Subtype": FakeSubtype("FreeText")}
    if callout is not None:
        data["CL"] = list(callout)
    return {"data": data, "contents": contents, "top": top, "x0": x0,
            "x1": x1, "bottom": bottom}


def test_the_annotation_rectangle_is_kept_not_just_its_corner():
    """Binding is rectangle-to-rectangle; a single corner cannot express overlap."""
    page = FakePage([free_text("note", top=10.0, x0=20.0, x1=90.0, bottom=30.0)])
    found = pdf_geometry.extract_annotations(page, "")
    assert (found[0].x0, found[0].top, found[0].x1, found[0].bottom) == (20.0, 10.0, 90.0, 30.0)


def test_a_callout_line_is_read_as_the_point_it_indicates():
    """`/CL` is knee-then-tip; the tip is where the annotator aimed."""
    page = FakePage([free_text("note", callout=(410, 653, 350, 653, 290, 653))])
    found = pdf_geometry.extract_annotations(page, "")
    assert found[0].callout == (290.0, 653.0)


def test_an_absent_or_malformed_callout_line_is_simply_absent():
    page = FakePage([free_text("no line"), free_text("junk", callout=("x", "y"))])
    assert [a.callout for a in pdf_geometry.extract_annotations(page, "")] == [None, None]


def test_widgets_are_collected_as_targets_with_their_field_names():
    """A Widget is still not an annotation -- but its rect and `/T` name are what to aim at."""
    page = FakePage([widget(b"TypeOfSubmission"), free_text("note")])
    found = pdf_geometry.extract_widgets(page)
    assert [(w.name, w.x0, w.top) for w in found] == [("TypeOfSubmission", 230.0, 130.0)]


def test_a_widget_without_a_field_name_is_not_a_target():
    """An unnamed widget could only ever produce an empty label."""
    assert pdf_geometry.extract_widgets(FakePage([widget(None)])) == []


# -- the cascade, end to end against the fixture ----------------------------------------


def converted(config, fixtures_dir):
    return pdf_geometry.to_markdown(fixtures_dir / LINKED, config)


def test_a_callout_line_binds_to_the_widget_it_points_into(config, fixtures_dir):
    """The strongest link in the file: the annotator drew the arrow themselves."""
    assert "[field: TypeOfSubmission]: Use Application for the first submission attempt." \
        in converted(config, fixtures_dir)


def test_a_callout_line_binds_to_a_printed_label_when_there_is_no_widget(config, fixtures_dir):
    assert "[field: 4. PROJECT TITLE]: Limited to 200 characters." \
        in converted(config, fixtures_dir)


def test_without_a_callout_line_an_overlapping_widget_still_names_the_field(config, fixtures_dir):
    assert "[field: ApplicantName]: Must match the name registered with the agency." \
        in converted(config, fixtures_dir)


def test_overlap_with_a_label_is_marked_as_inferred_not_as_exact(config, fixtures_dir):
    """A guess must never render in the same shape as a link the file actually states."""
    body = converted(config, fixtures_dir)
    assert "[near: 2. DATE SUBMITTED]: Format: MM/DD/YYYY." in body
    assert "[field: 2. DATE SUBMITTED]" not in body


def test_an_annotation_pointing_at_nothing_stays_unbound(config, fixtures_dir):
    """Better no anchor than a plausible one: a wrong field is unrecoverable downstream."""
    body = converted(config, fixtures_dir)
    assert f"{ANNOTATION_PREFIX}General guidance is in the programme announcement." in body


def test_a_bound_annotation_is_emitted_beside_its_target(config, fixtures_dir):
    """The point of binding: the note travels to its field instead of to its own margin y.

    In the fixture the callouts are stacked in one column whose vertical order differs from
    the fields', so sorting by the annotation's own position cannot produce this.
    """
    body = converted(config, fixtures_dir)
    for label, note in (
        ("1. TYPE OF SUBMISSION", "Use Application"),
        ("2. DATE SUBMITTED", "Format: MM/DD/YYYY."),
        ("3. APPLICANT NAME", "Must match the name"),
        ("4. PROJECT TITLE", "Limited to 200 characters."),
    ):
        assert body.index(label) < body.index(note), f"{note!r} drifted from {label!r}"


def test_linking_can_be_switched_off_without_losing_the_annotations(config, fixtures_dir,
                                                                    monkeypatch):
    monkeypatch.setenv("PDF_ANNOTATION_LINKING", "false")
    disabled = config_module.load(source_dir=fixtures_dir)
    body = pdf_geometry.to_markdown(fixtures_dir / LINKED, disabled)
    assert "[field:" not in body and "[near:" not in body
    assert "Use Application for the first submission attempt." in body


def test_an_unparseable_linking_switch_is_an_error(fixtures_dir, monkeypatch):
    monkeypatch.setenv("PDF_ANNOTATION_LINKING", "maybe")
    with pytest.raises(config_module.ConfigError, match="PDF_ANNOTATION_LINKING"):
        config_module.load(source_dir=fixtures_dir)


def test_a_document_with_no_widgets_or_callouts_is_unaffected(config, fixtures_dir):
    """Linking must not put labels on a document that never had fields."""
    body = pdf_geometry.to_markdown(fixtures_dir / "born_digital.pdf", config)
    assert "[field:" not in body and "[near:" not in body


def test_the_needs_ocr_stub_can_still_name_fields(config, fixtures_dir):
    """A scanned form has no labels to read, so its widgets are the only source of names."""
    body, _ = pdf.needs_ocr_stub(fixtures_dir / LINKED, config)
    assert "[field: TypeOfSubmission]: Use Application for the first submission attempt." in body
