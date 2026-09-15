"""Configuration: environment loading and every tunable in one place.

No magic numbers live anywhere else in the pipeline. Thresholds are defaults here and are
overridable by environment variable so that a run can be explained by its environment
rather than by a code change.

`SOURCE_DIR` deliberately has **no default**. The source documents live outside both repos
and pointing at them is an explicit act; defaulting to a path inside the repo would quietly
produce an empty or wrong corpus.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Extensions the pipeline knows how to convert. Anything else is reported as skipped.
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "doc",
    ".dot": "doc",
    ".csv": "csv",
}

#: Docling's OCR engine, pinned even though OCR is off for this whole phase.
#:
#: Docling's OCR backends differ in weight provenance -- RapidOCR wraps PaddleOCR (Baidu)
#: models -- so leaving engine selection on Docling's defaults would let a version bump pull
#: Chinese-trained weights into ingestion silently. `test_gates.py` asserts this value.
DOCLING_OCR_ENGINE = "easyocr"

#: PDF conversion engines. Geometry is the default: it needs no model, no ML runtime and no
#: network, and produces identical output on every machine. `docling` is a per-document
#: escalation for pages geometry handles badly (borderless tables, unusual columns) and
#: requires the `pdf` extra plus a cleared layout model.
PDF_ENGINE_GEOMETRY = "geometry"
PDF_ENGINE_DOCLING = "docling"

#: OCR engines that are banned outright on base-weight provenance grounds.
BANNED_OCR_ENGINES = frozenset({"rapidocr", "paddleocr", "paddle"})


class ConfigError(RuntimeError):
    """Configuration is missing or unusable. Always actionable in its message."""


def load_dotenv(path: Path | str | None = None) -> None:
    """Load `KEY=VALUE` lines from a .env file without overriding the real environment.

    Deliberately stdlib-only: `python-dotenv` is not on the approved dependency list and
    this is a dozen lines. Real environment variables always win, so a per-run override
    like `SOURCE_DIR=... make triage` behaves the way anyone would expect.
    """
    path = Path(path) if path is not None else REPO_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable, rejecting anything ambiguous.

    An unrecognised value is an error rather than a silent `False`: `PDF_ANNOTATIONS=no`
    quietly meaning "on" would be discovered only by noticing absent output, which is the
    hardest kind of bug to see in a converted document.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {os.environ[name]!r}") from exc


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {os.environ[name]!r}") from exc


@dataclass(frozen=True)
class Config:
    """Resolved settings for one run."""

    kb_path: Path
    work_dir: Path
    model_cache_dir: Path
    min_chars_per_page: int
    min_alpha_ratio: float
    max_low_page_fraction: float
    image_page_coverage: float
    csv_max_rows: int
    csv_max_cols: int
    docling_ocr_engine: str
    pdf_engine: str
    pdf_annotations: bool
    pdf_annotation_linking: bool
    pdf_boxed_text: bool
    pdf_boxed_max_area: float
    pdf_annotation_link_tolerance: float
    pdf_heading_size_ratio: float
    pdf_margin_fraction: float
    pdf_repeat_page_fraction: float
    pdf_line_tolerance: float
    pdf_space_width_ratio: float
    pdf_paragraph_gap_ratio: float
    pdf_column_gap_fraction: float
    pdf_cell_gap_ratio: float
    pdf_min_table_rows: int
    pdf_column_align_tolerance: float
    _source_dir: Path | None

    @property
    def manifest_path(self) -> Path:
        return self.kb_path / "corpus.yaml"

    @property
    def kb_dir(self) -> Path:
        return self.kb_path / "kb"

    def source_dir(self) -> Path:
        """The source root, or a clear error explaining how to set it.

        Never falls back to a path inside the repo and never creates the directory.
        """
        if self._source_dir is None:
            raise ConfigError(
                "No source directory. The source documents live outside both repos, so "
                "the pipeline has to be pointed at them explicitly.\n"
                "  Set SOURCE_DIR in .env (copy .env.example), or pass --source-dir PATH."
            )
        path = self._source_dir
        if not path.exists():
            raise ConfigError(f"Source directory does not exist: {path}")
        if not path.is_dir():
            raise ConfigError(f"Source directory is not a directory: {path}")
        if not os.access(path, os.R_OK):
            raise ConfigError(f"Source directory is not readable: {path}")
        return path


def load(source_dir: Path | str | None = None, *, env_file: Path | str | None = None) -> Config:
    """Build a `Config` from the environment, with `source_dir` overriding `SOURCE_DIR`."""
    load_dotenv(env_file)

    raw_source = source_dir if source_dir is not None else os.environ.get("SOURCE_DIR")
    resolved_source = Path(raw_source).expanduser().resolve() if raw_source else None

    kb_path = Path(
        os.environ.get("KB_PATH", REPO_ROOT.parent / "dgx-knowledge")
    ).expanduser().resolve()

    work_dir = Path(os.environ.get("WORK_DIR", kb_path / "work")).expanduser().resolve()

    # Pin the model cache to a project-local, disposable directory rather than inheriting the
    # machine's shared ~/.cache/huggingface. Two reasons, both practical: the model gate needs
    # a well-defined directory that only this project writes to, otherwise unrelated models
    # from other work fail the gate; and models are re-downloadable, so keeping them under
    # WORK_DIR honours the rule that deleting WORK_DIR costs time and never information.
    model_cache_dir = (
        Path(os.environ.get("HF_HOME", work_dir / "models")).expanduser().resolve()
    )
    # Set before anything imports huggingface_hub, which reads HF_HOME at import time.
    os.environ.setdefault("HF_HOME", str(model_cache_dir))

    return Config(
        kb_path=kb_path,
        work_dir=work_dir,
        model_cache_dir=model_cache_dir,
        min_chars_per_page=_env_int("MIN_CHARS_PER_PAGE", 100),
        min_alpha_ratio=_env_float("MIN_ALPHA_RATIO", 0.60),
        max_low_page_fraction=_env_float("MAX_LOW_PAGE_FRACTION", 0.20),
        # Share of a page's area covered by raster images past which the page is reported as
        # mostly picture. Not a classification: a page can be a third diagram and be perfectly
        # fine. It is the threshold for *saying so*, because a form supplied as a screenshot
        # defeats every text metric at once -- its callouts are real text, so character count
        # and alpha ratio both look healthy while the form itself is unreachable.
        image_page_coverage=_env_float("IMAGE_PAGE_COVERAGE", 0.15),
        csv_max_rows=_env_int("CSV_MAX_ROWS", 300),
        csv_max_cols=_env_int("CSV_MAX_COLS", 12),
        docling_ocr_engine=os.environ.get("DOCLING_OCR_ENGINE", DOCLING_OCR_ENGINE),
        pdf_engine=os.environ.get("PDF_ENGINE", PDF_ENGINE_GEOMETRY),
        # Text annotations (Markup callouts, sticky notes) are page content that is not
        # printed on the page. On an annotated form they are frequently the only instructions
        # the document carries, so they are ON by default; the switch exists to isolate them
        # when comparing engines, not because leaving them out is ever the better default.
        pdf_annotations=_env_bool("PDF_ANNOTATIONS", True),
        # Binding each annotation to the field it describes. On by default for the
        # same reason the annotations themselves are: an instruction whose field is
        # unknown is a good deal less useful than one whose field is named, and the
        # binding degrades to today's behaviour when the file says nothing.
        pdf_annotation_linking=_env_bool("PDF_ANNOTATION_LINKING", True),
        # Text set inside a drawn box: a flattened callout, which is page text rather than an
        # annotation object. On by default because the alternative is worse than losing the
        # marking -- boxes that share a baseline merge into one interleaved line, so the text
        # is not merely unlabelled but unreadable.
        pdf_boxed_text=_env_bool("PDF_BOXED_TEXT", True),
        # Largest share of the page a box may cover and still be a note. A page border or a
        # background panel is a filled rectangle around the whole page, and it is not
        # commentary about the page.
        pdf_boxed_max_area=_env_float("PDF_BOXED_MAX_AREA", 0.25),
        # Vertical slack, in points, when testing whether an annotation and a candidate
        # target sit on the same row. A callout is typed at its own size and rarely
        # shares a baseline with the label it describes, so exact overlap is too strict;
        # much more than half a line and a dense form starts matching its neighbours.
        pdf_annotation_link_tolerance=_env_float("PDF_ANNOTATION_LINK_TOLERANCE", 6.0),
        # Geometric PDF extraction. These describe page geometry, not document semantics,
        # which is why they can be constants at all -- a heading is bigger than body text and
        # a running header sits in the margin on most pages, in any typeset document.
        pdf_heading_size_ratio=_env_float("PDF_HEADING_SIZE_RATIO", 1.15),
        pdf_margin_fraction=_env_float("PDF_MARGIN_FRACTION", 0.08),
        pdf_repeat_page_fraction=_env_float("PDF_REPEAT_PAGE_FRACTION", 0.5),
        pdf_line_tolerance=_env_float("PDF_LINE_TOLERANCE", 3.0),
        # Narrowest gap that can be a word space, as a fraction of the type size.
        # pdfplumber splits words on an *absolute* tolerance and additionally wherever a
        # font or size changes, so a word set in two subsets of one face comes back split
        # with no gap at all. Measured across a real handbook the two populations do not
        # overlap: intra-word splits sit at 0.00-0.01 of the type size, real spaces at 0.20
        # and up. 0.10 is the middle of that empty band.
        pdf_space_width_ratio=_env_float("PDF_SPACE_WIDTH_RATIO", 0.10),
        pdf_paragraph_gap_ratio=_env_float("PDF_PARAGRAPH_GAP_RATIO", 1.6),
        pdf_column_gap_fraction=_env_float("PDF_COLUMN_GAP_FRACTION", 0.06),
        # Borderless-table recovery. Strict on purpose: inventing a table inside prose
        # destroys the paragraph it consumes, so a missed table beats a hallucinated one.
        pdf_cell_gap_ratio=_env_float("PDF_CELL_GAP_RATIO", 1.5),
        pdf_min_table_rows=_env_int("PDF_MIN_TABLE_ROWS", 3),
        pdf_column_align_tolerance=_env_float("PDF_COLUMN_ALIGN_TOLERANCE", 8.0),
        _source_dir=resolved_source,
    )
