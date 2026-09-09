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


def convert(path: Path, config: Config) -> tuple[str, str]:
    """Convert a `clean` or `partial` PDF. Returns `(markdown_body, converter_name)`."""
    raise NotImplementedError(
        "M2: Docling PDF conversion, the pdfplumber table rescue and the MarkItDown "
        "per-file fallback are deliberately not built yet. The M1 coverage report is a "
        "decision gate, and the rescue and fallback heuristics should be shaped by the real "
        "corpus rather than tuned against synthetic samples."
    )
