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

from ..config import BANNED_OCR_ENGINES, PDF_ENGINE_DOCLING, PDF_ENGINE_MARKER, Config
from ..triage import extract_page_texts
from . import pdf_geometry, pdf_marker

CONVERTER_GEOMETRIC = "pdfplumber-geometry (model-free)"
CONVERTER_DOCLING = "docling (do_ocr=False)"
CONVERTER_DOCLING_PDFPLUMBER = "docling+pdfplumber"
CONVERTER_MARKITDOWN = "markitdown (fallback)"
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


def needs_ocr_stub(path: Path) -> tuple[str, str]:
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
    return "\n\n".join(parts), CONVERTER_STUB


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


def convert(
    path: Path, config: Config, low_pages: list[int] | tuple[int, ...] = ()
) -> tuple[str, str]:
    """Convert a `clean` or `partial` PDF. Returns `(markdown_body, converter_name)`.

    Geometry is the default, and Docling is an escalation rather than a fallback. With a
    usable text layer on every page — which is what triage established — a layout model buys
    borderless-table structure and unusual reading orders, and costs a torch runtime, a model
    download, per-platform output variance, and a base-weight provenance question. That is a
    bad default trade for a corpus of born-digital handbooks, and a reasonable one for the
    specific documents that turn out to need it.

    Escalation is per document: set `converter: docling` on a manifest entry.

    `PDF_ENGINE=marker` is a third path and is not an escalation at all -- it is a
    licence evaluation that happens to produce markdown. Its output carries a banner
    saying so.
    """
    if _wants_marker(config):
        # Deliberately before the Docling branch and deliberately not a fallback: Marker is
        # an evaluation engine whose weights are not licensed for this deployment, so it only
        # ever runs because someone set PDF_ENGINE=marker on purpose. See pdf_marker.py.
        return pdf_marker.convert(path, config)

    if _wants_docling(config):
        return _convert_with_docling(path, config)

    body = pdf_geometry.to_markdown(path, config)
    note = missing_pages_note(low_pages)
    return (f"{note}\n\n{body}" if note else body), CONVERTER_GEOMETRIC


def _wants_docling(config: Config) -> bool:
    return config.pdf_engine == PDF_ENGINE_DOCLING


def _wants_marker(config: Config) -> bool:
    return config.pdf_engine == PDF_ENGINE_MARKER


def _convert_with_docling(path: Path, config: Config) -> tuple[str, str]:
    """The escalation path. Requires the `pdf` extra and a cleared layout model."""
    raise NotImplementedError(
        "The Docling PDF escalation is not wired up. It needs two things first: "
        "`uv sync --extra pdf` to install the ML runtime, and a decision on "
        "docling-project/docling-layout-heron's base-weight provenance, which models.yaml "
        "still lists under pending_review. Use the default geometric engine, or resolve "
        "those. See README 'Open questions'."
    )
