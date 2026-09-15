"""PDF -> markdown.

Docling runs with **`do_ocr=False`** throughout this phase: its layout and TableFormer
models are used over the *existing* text layer, and no OCR model is ever fetched or run.

Docling's OCR engine is nevertheless pinned here. Its backends differ in weight provenance
(RapidOCR wraps PaddleOCR/Baidu models), so leaving the selection on Docling's defaults
would let a version bump pull Chinese-trained weights into ingestion silently. Building the
options explicitly means `test_gates.py` can assert the pin, and it holds even if a future
Docling flips a default.
"""

from __future__ import annotations

from pathlib import Path

from ..config import BANNED_OCR_ENGINES, Config
from ..triage import extract_page_texts
from . import pdf_geometry

CONVERTER_GEOMETRIC = "pdfplumber-geometry (model-free)"
CONVERTER_DOCLING = "docling (do_ocr=False)"
CONVERTER_STUB = "none (no usable text layer)"

INCOMPLETE_CALLOUT = (
    "> **INCOMPLETE — no usable text layer. Deferred to Phase 2 OCR.**"
)


class OcrEngineError(RuntimeError):
    """The configured OCR engine fails the base-weight provenance rule."""


def build_pipeline_options(config: Config):
    """Docling pipeline options for this phase: OCR off, engine pinned, tables on.

    Imports Docling lazily so that inventory, triage and the report — none of which need a
    model — stay fast and keep working on a machine where Docling cannot be installed.
    """
    engine = config.docling_ocr_engine.lower()
    if engine in BANNED_OCR_ENGINES:
        raise OcrEngineError(
            f"OCR engine {engine!r} is banned on base-weight provenance grounds "
            "(RapidOCR/PaddleOCR ship Baidu-trained models). "
            "Set DOCLING_OCR_ENGINE to a permitted engine."
        )

    from docling.datamodel.pipeline_options import (  # noqa: PLC0415 - lazy by design
        EasyOcrOptions,
        PdfPipelineOptions,
        TesseractCliOcrOptions,
    )

    engines = {"easyocr": EasyOcrOptions, "tesseract": TesseractCliOcrOptions}
    if engine not in engines:
        raise OcrEngineError(
            f"Unknown OCR engine {engine!r}. Permitted: {sorted(engines)}. "
            "An unrecognised value must not silently fall through to Docling's default."
        )

    options = PdfPipelineOptions()
    options.do_ocr = False
    options.do_table_structure = True
    options.table_structure_options.do_cell_matching = True
    # Set even though OCR is off, so the provenance pin survives a Docling default change.
    options.ocr_options = engines[engine]()
    return options


def needs_ocr_stub(path: Path, config: Config) -> tuple[str, str]:
    """Body for a PDF with no usable text layer.

    Emits the visible INCOMPLETE callout plus whatever text *was* extractable, so the file
    is honest about being partial rather than silently empty. Does not attempt OCR.
    """
    try:
        page_texts = extract_page_texts(path)
    except Exception:  # noqa: BLE001 - an unreadable source still gets an honest stub
        page_texts = []

    salvaged = "\n\n".join(text.strip() for text in page_texts if text.strip())
    parts = [INCOMPLETE_CALLOUT]
    if salvaged:
        parts.append("## Partial text recovered from the existing text layer")
        parts.append(salvaged)
    else:
        parts.append("No text whatsoever could be extracted from this document.")

    # Annotations survive the stub path too, and the case is not hypothetical: a scanned
    # form that someone then marked up electronically has no usable text layer and a full
    # set of typed callouts. Those callouts are the only machine-readable text in the file,
    # so dropping them here would throw away the one thing that was never lost.
    annotations = _annotation_block(path, config)
    if annotations:
        parts.append("## Annotations (not printed on the page)")
        parts.append(annotations)
    return "\n\n".join(parts), CONVERTER_STUB


def _annotation_block(path: Path, config: Config) -> str:
    """Rendered text annotations for a document taking the stub path, or ""."""
    if not config.pdf_annotations:
        return ""
    try:
        import pdfplumber  # noqa: PLC0415 - lazy, and only on this path

        rendered: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                annotations = pdf_geometry.extract_annotations(
                    page, page.extract_text() or ""
                )
                # Field binding still applies on this path, and is worth more here than
                # anywhere else: a scanned form has no text layer to read labels off, so a
                # callout's field name is recoverable only from the form's own widgets. No
                # lines are passed because there are none — the label rule cannot fire, and
                # an annotation that matches no widget simply stays unbound.
                if config.pdf_annotation_linking:
                    annotations = pdf_geometry.resolve_targets(
                        annotations,
                        pdf_geometry.extract_widgets(page),
                        [],
                        float(page.height),
                        config,
                    )
                for annotation in annotations:
                    text = annotation.render()
                    if text:
                        rendered.append(text)
    except Exception:  # noqa: BLE001 - the stub is a best-effort salvage, never a failure
        return ""
    return "\n\n".join(rendered)


def missing_pages_note(low_pages: list[int] | tuple[int, ...]) -> str:
    """A visible marker naming pages that contributed no text.

    A `partial` document converts fine and quietly omits its scanned pages. Downstream that
    is indistinguishable from a document which simply never covered the topic, so anything
    citing this file would be citing a document with holes in it and no way to know. Naming
    the pages costs one line and makes the gap answerable.
    """
    if not low_pages:
        return ""
    numbers = ", ".join(str(number) for number in low_pages)
    plural = "s" if len(low_pages) > 1 else ""
    return (
        f"> **INCOMPLETE — page{plural} {numbers} yielded no usable text and "
        f"{'are' if plural else 'is'} not represented below.** No OCR was attempted."
    )


def image_pages_note(
    image_pages: list[int] | tuple[int, ...],
    max_coverage: float,
    low_pages: list[int] | tuple[int, ...] = (),
) -> str:
    """A visible marker naming pages whose substance is a picture.

    The case this exists for is a form supplied as a screenshot with typed callouts beside
    it. Nothing else catches it: the callouts are real text, so character coverage is healthy
    and the alpha ratio is perfect, no page falls below the low-text threshold, and the page
    carries no vector content for table extraction to find. The document converts `clean`,
    without a warning, and omits the form it is about.

    Deliberately a note and not a reclassification. A page that is a third diagram is fine,
    and telling a diagram from a screenshot of a form is exactly the inference this pipeline
    declines to make -- so it reports the measurement and leaves the judgement to a reader.
    """
    # A page with no usable text at all is already named by `missing_pages_note`, and a
    # full-page scan is both things at once. Naming it twice adds no information; what this
    # note is for is the page that looks *fine* by every text metric and is still a picture.
    remaining = [number for number in image_pages if number not in set(low_pages)]
    if not remaining:
        return ""
    numbers = ", ".join(str(number) for number in remaining)
    plural = "s" if len(remaining) > 1 else ""
    verb = "are" if plural else "is"
    return (
        f"> **INCOMPLETE — page{plural} {numbers} {verb} mostly image "
        f"(up to {max_coverage:.0%} of the page), and that content is not in the text "
        "layer.** No OCR was attempted."
    )


def convert(
    path: Path,
    config: Config,
    low_pages: list[int] | tuple[int, ...] = (),
    image_pages: list[int] | tuple[int, ...] = (),
    max_image_coverage: float = 0.0,
) -> tuple[str, str]:
    """Convert a `clean` or `partial` PDF. Returns `(markdown_body, converter_name)`.

    Geometry is the default, and Docling is an escalation rather than a fallback. With a
    usable text layer on every page — which is what triage established — a layout model buys
    borderless-table structure and unusual reading orders, and costs a torch runtime, a model
    download, per-platform output variance, and a base-weight provenance question. That is a
    bad default trade for a corpus of born-digital handbooks, and a reasonable one for the
    specific documents that turn out to need it.

    Escalation is per document: set `converter: docling` on a manifest entry.
    """
    if _wants_docling(config):
        return _convert_with_docling(path, config)

    body = pdf_geometry.to_markdown(path, config)
    notes = [
        note
        for note in (
            missing_pages_note(low_pages),
            image_pages_note(image_pages, max_image_coverage, low_pages),
        )
        if note
    ]
    return "\n\n".join([*notes, body]), CONVERTER_GEOMETRIC


def _wants_docling(config: Config) -> bool:
    return config.pdf_engine == "docling"


def _convert_with_docling(path: Path, config: Config) -> tuple[str, str]:
    """The escalation path. Requires the `pdf` extra and a cleared layout model."""
    raise NotImplementedError(
        "The Docling PDF escalation is not wired up. It needs two things first: "
        "`uv sync --extra pdf` to install the ML runtime, and a decision on "
        "docling-project/docling-layout-heron's base-weight provenance, which models.yaml "
        "still lists under pending_review. Use the default geometric engine, or resolve "
        "those. See README 'Open questions'."
    )
