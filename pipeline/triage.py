"""Text-layer coverage measurement — the core of M1.

Measures how much usable text a PDF already carries, so the size of the OCR problem is a
number rather than a guess. Model-free by design: `pypdfium2` reads the existing text layer
and nothing is inferred.

Three metrics, because character count alone is not enough:

* **median chars/page** — the median matters more than the mean, since a few dense pages
  should not mask a scanned majority.
* **low-page fraction** — catches the mixed document, e.g. a handbook with scanned
  appendices, which is convertible but incompletely so.
* **alpha ratio** — catches the failure mode a character count misses entirely. A PDF with
  a broken font-to-Unicode map extracts plenty of characters and every one of them is
  mojibake. Such a file looks text-rich and is unusable.
"""

from __future__ import annotations

import statistics
import string
from dataclasses import asdict, dataclass
from pathlib import Path

import pypdfium2 as pdfium

from .config import Config
from .manifest import (
    TEXT_CLASS_CLEAN,
    TEXT_CLASS_ERROR,
    TEXT_CLASS_NEEDS_OCR,
    TEXT_CLASS_PARTIAL,
)

_PUNCTUATION = frozenset(string.punctuation)


class EncryptedDocumentError(RuntimeError):
    """The document is encrypted or password-protected — a STOP-AND-ASK, not a failure."""


@dataclass(frozen=True)
class TriageResult:
    page_count: int
    chars_per_page_mean: float | None
    chars_per_page_median: float | None
    pages_below_threshold: int
    low_page_fraction: float | None
    alpha_ratio: float | None
    text_class: str
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def alpha_ratio(text: str) -> float:
    """Fraction of characters that are alphanumeric, punctuation, or whitespace.

    Everything else — replacement characters, private-use-area glyphs, stray symbol soup —
    counts against the document. Note the honest limit of this measure: mojibake that
    happens to decode into plausible-looking letters still scores well. It catches broken
    encodings, not wrong-but-lettered text.
    """
    if not text:
        return 0.0
    good = sum(
        1 for char in text if char.isalnum() or char.isspace() or char in _PUNCTUATION
    )
    return good / len(text)


def _page_text(page) -> str:
    textpage = page.get_textpage()
    try:
        return textpage.get_text_range()
    finally:
        textpage.close()


def extract_page_texts(path: Path) -> list[str]:
    """Per-page text from the existing text layer. Never runs OCR."""
    try:
        document = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise EncryptedDocumentError(str(exc)) from exc
        raise

    try:
        texts = []
        for index in range(len(document)):
            page = document[index]
            try:
                texts.append(_page_text(page))
            finally:
                page.close()
        return texts
    finally:
        document.close()


def classify(
    page_texts: list[str],
    *,
    min_chars_per_page: int,
    min_alpha_ratio: float,
    max_low_page_fraction: float,
) -> TriageResult:
    page_count = len(page_texts)
    if page_count == 0:
        return TriageResult(
            page_count=0,
            chars_per_page_mean=None,
            chars_per_page_median=None,
            pages_below_threshold=0,
            low_page_fraction=None,
            alpha_ratio=None,
            text_class=TEXT_CLASS_ERROR,
            error="PDF reports zero pages",
        )

    counts = [len(text) for text in page_texts]
    low_pages = sum(1 for count in counts if count < min_chars_per_page)
    low_fraction = low_pages / page_count
    ratio = alpha_ratio("".join(page_texts))
    median = statistics.median(counts)

    fails_median = median < min_chars_per_page
    fails_alpha = ratio < min_alpha_ratio

    if fails_median or fails_alpha:
        text_class = TEXT_CLASS_NEEDS_OCR
    elif low_fraction > max_low_page_fraction:
        text_class = TEXT_CLASS_PARTIAL
    else:
        text_class = TEXT_CLASS_CLEAN

    return TriageResult(
        page_count=page_count,
        chars_per_page_mean=round(statistics.fmean(counts), 1),
        chars_per_page_median=round(float(median), 1),
        pages_below_threshold=low_pages,
        low_page_fraction=round(low_fraction, 4),
        alpha_ratio=round(ratio, 4),
        text_class=text_class,
    )


def triage_pdf(path: Path, config: Config) -> TriageResult:
    """Measure one PDF. An unreadable document is recorded, never allowed to fail the run."""
    try:
        page_texts = extract_page_texts(path)
    except EncryptedDocumentError as exc:
        return _error_result(f"encrypted or password-protected: {exc}")
    except pdfium.PdfiumError as exc:
        return _error_result(f"unreadable PDF: {exc}")

    return classify(
        page_texts,
        min_chars_per_page=config.min_chars_per_page,
        min_alpha_ratio=config.min_alpha_ratio,
        max_low_page_fraction=config.max_low_page_fraction,
    )


def triage_text_native(_path: Path, _config: Config) -> TriageResult:
    """DOCX and CSV always carry text, so they skip measurement and record `clean`."""
    return TriageResult(
        page_count=0,
        chars_per_page_mean=None,
        chars_per_page_median=None,
        pages_below_threshold=0,
        low_page_fraction=None,
        alpha_ratio=None,
        text_class=TEXT_CLASS_CLEAN,
    )


def _error_result(message: str) -> TriageResult:
    return TriageResult(
        page_count=0,
        chars_per_page_mean=None,
        chars_per_page_median=None,
        pages_below_threshold=0,
        low_page_fraction=None,
        alpha_ratio=None,
        text_class=TEXT_CLASS_ERROR,
        error=message,
    )
