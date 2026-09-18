"""DOCX, and legacy DOC/DOT via LibreOffice.

Legacy `.doc` / `.dot` go through `soffice` as a **subprocess**. That is the whole reason
LibreOffice is usable here: invoking a separate program is not linking, so its copyleft
obligations do not reach our code, whereas importing a copyleft library would. If `soffice`
is missing the run fails with an actionable error — it never falls back to a copyleft Python
library such as PyMuPDF.

The intermediate `.docx` is written into `WORK_DIR`, never back into `SOURCE_DIR`, which is
strictly read-only.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from pathlib import Path
from typing import Any

from ..config import Config
from . import render_block_provenance

CONVERTER_DOCLING = "docling (docx)"
CONVERTER_LIBREOFFICE_DOCLING = "libreoffice+docling"

SOFFICE_BINARY = "soffice"
#: Timeout for one LibreOffice invocation. It is a GUI application in headless clothing and
#: will hang forever on a malformed file rather than exit non-zero.
SOFFICE_TIMEOUT_SECONDS = 180

#: What a picture block renders as. Matches the register of the PDF path's own markers.
PICTURE_MARKER = "> **Image:** embedded picture, not extracted. No OCR was attempted."

#: Text-bearing labels that are ordinary body content. Anything else that reaches
#: `_walk_items` as a `TextItem` (footnote, page header/footer, form fields, ...) is real but
#: unfamiliar, so its docling label is recorded in `confidence` instead of being silently
#: flattened into indistinguishable prose.
_STRUCTURAL_TEXT_LABELS = {"text", "paragraph", "caption"}

_logger = logging.getLogger(__name__)


class SofficeMissingError(RuntimeError):
    """LibreOffice is not installed. Never silently substituted with anything else."""


class SofficeConversionError(RuntimeError):
    """LibreOffice ran but produced no usable `.docx`."""


def find_soffice() -> str:
    """Locate the `soffice` binary, including the macOS app-bundle location."""
    found = shutil.which(SOFFICE_BINARY)
    if found:
        return found

    mac_bundle = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if mac_bundle.is_file():
        return str(mac_bundle)

    raise SofficeMissingError(
        f"{SOFFICE_BINARY!r} not found on PATH. Legacy .doc/.dot conversion needs "
        "LibreOffice, invoked as a subprocess. Install it and pin its major version -- see "
        "README section 'LibreOffice (subprocess only)'. There is no fallback: a copyleft "
        "Python library is not an option."
    )


def doc_to_docx(path: Path, config: Config) -> Path:
    """Convert a legacy `.doc`/`.dot` to `.docx` inside `WORK_DIR`.

    `WORK_DIR` is disposable: deleting it must only ever cost time, never information.
    """
    soffice = find_soffice()
    outdir = config.work_dir / "doc2docx"
    outdir.mkdir(parents=True, exist_ok=True)

    completed = subprocess.run(  # noqa: S603 - fixed binary, no shell, path-only argument
        [soffice, "--headless", "--convert-to", "docx", "--outdir", str(outdir), str(path)],
        capture_output=True,
        text=True,
        timeout=SOFFICE_TIMEOUT_SECONDS,
        check=False,
    )

    produced = outdir / f"{path.stem}.docx"
    if not produced.is_file():
        raise SofficeConversionError(
            f"LibreOffice produced no .docx for {path.name} "
            f"(exit {completed.returncode}).\nstdout: {completed.stdout.strip()}\n"
            f"stderr: {completed.stderr.strip()}"
        )
    return produced


def _load_document(path: Path):
    """DOCX -> docling `DoclingDocument`, via Docling's Word backend. Fetches no model.

    Deliberately drives `MsWordDocumentBackend` directly rather than going through
    `DocumentConverter`. Two reasons, and the first is the important one:

    * `docling.document_converter` imports Docling's PDF backend unconditionally, so merely
      importing it drags in the PDF machinery. Using the Word backend makes it structurally
      impossible for the DOCX path to reach a layout or table model, rather than merely
      unlikely -- which is what lets DOCX ship while the PDF path is still gated.
    * It keeps the DOCX install to `docling-slim[format-docx]`, with no ML runtime at all.
    """
    from docling.backend.msword_backend import (  # noqa: PLC0415 - lazy by design
        MsWordDocumentBackend,
    )
    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
    from docling.datamodel.document import InputDocument  # noqa: PLC0415

    in_doc = InputDocument(
        path_or_stream=path,
        format=InputFormat.DOCX,
        backend=MsWordDocumentBackend,
        filename=path.name,
    )
    return MsWordDocumentBackend(in_doc=in_doc, path_or_stream=path).convert()


def _heading_block(level: int, text: str) -> str:
    return f"{'#' * min(level, 6)} {text}"


def _list_line(item, depth: int) -> str:
    indent = "  " * depth
    marker = item.marker if item.enumerated and item.marker else "-"
    return f"{indent}{marker} {item.text.strip()}"


def _walk_items(document) -> tuple[list[tuple[str, str, str]], int, int]:
    """Per-item walk over a `DoclingDocument`, in document order.

    Produces `(markdown, kind, confidence)` triples instead of calling
    `document.export_to_markdown()`, so each block keeps its own kind -- a table stays a
    `table` block and a picture stays a `picture` block rather than collapsing into
    undifferentiated prose. See the Stage 1 sidecar addendum §3.2 for the mapping this
    implements; verified against the installed docling-core's item classes and labels rather
    than assumed.
    """
    from docling_core.types.doc import (  # noqa: PLC0415 - lazy, matches the rest of the file
        ListItem,
        PictureItem,
        SectionHeaderItem,
        TableItem,
        TextItem,
        TitleItem,
    )

    blocks: list[tuple[str, str, str]] = []
    picture_count = 0
    empty_skipped = 0

    list_run: list[str] = []
    list_base_depth: int | None = None

    def flush_list() -> None:
        nonlocal list_run, list_base_depth
        if list_run:
            blocks.append(("\n".join(list_run), "list", "structural"))
        list_run = []
        list_base_depth = None

    for item, depth in document.iterate_items():
        if isinstance(item, ListItem):
            text = item.text.strip()
            if not text:
                empty_skipped += 1
                continue
            if list_base_depth is None:
                list_base_depth = depth
            list_run.append(_list_line(item, max(0, depth - list_base_depth)))
            continue
        flush_list()

        if isinstance(item, TitleItem):
            text = item.text.strip()
            if not text:
                empty_skipped += 1
                continue
            blocks.append((f"# {text}", "heading", "structural"))
        elif isinstance(item, SectionHeaderItem):
            text = item.text.strip()
            if not text:
                empty_skipped += 1
                continue
            blocks.append((_heading_block(item.level, text), "heading", "structural"))
        elif isinstance(item, TableItem):
            markdown = item.export_to_markdown(doc=document).strip()
            if not markdown:
                empty_skipped += 1
                continue
            blocks.append((markdown, "table", "structural"))
        elif isinstance(item, PictureItem):
            picture_count += 1
            blocks.append((PICTURE_MARKER, "picture", "structural"))
        elif isinstance(item, TextItem):
            text = item.text.strip()
            if not text:
                empty_skipped += 1
                continue
            confidence = (
                "structural" if item.label in _STRUCTURAL_TEXT_LABELS else f"docling:{item.label}"
            )
            blocks.append((text, "paragraph", confidence))
        else:
            # An item type this mapping has not met. Record it rather than silently
            # flattening it -- an unfamiliar type should be visible in the sidecar.
            text = str(getattr(item, "text", "") or "").strip()
            if not text:
                empty_skipped += 1
                continue
            label = getattr(item, "label", None) or type(item).__name__
            blocks.append((text, "paragraph", f"docling:{label}"))

    flush_list()
    return blocks, picture_count, empty_skipped


def convert_with_provenance(
    path: Path, config: Config, *, provenance_slug: str | None = None
) -> tuple[str, str, list[dict[str, Any]]]:
    """Convert a DOCX, or a legacy DOC/DOT via LibreOffice first. Anchors every block.

    Legacy `.doc`/`.dot` need no separate item handling here: `doc_to_docx` turns them into a
    `.docx` first, and everything below is format-agnostic over the result.
    """
    suffix = path.suffix.lower()
    if suffix in (".doc", ".dot"):
        docx_path = doc_to_docx(path, config)
        converter = CONVERTER_LIBREOFFICE_DOCLING
    else:
        docx_path = path
        converter = CONVERTER_DOCLING

    document = _load_document(docx_path)
    blocks, picture_count, empty_skipped = _walk_items(document)
    if empty_skipped:
        _logger.debug("%s: skipped %d empty item(s)", path.name, empty_skipped)

    if provenance_slug is None:
        body = "\n\n".join(text for text, _, _ in blocks)
        provenance: list[dict[str, Any]] = []
    else:
        body, provenance = render_block_provenance(blocks, provenance_slug)

    if picture_count:
        note = (
            f"> **INCOMPLETE — this document embeds {picture_count} image(s), and their "
            "content is not in the text layer.** No OCR was attempted. Each is marked in "
            "place below."
        )
        body = "\n\n".join([note, body])

    return body, converter, provenance


def convert(path: Path, config: Config) -> tuple[str, str]:
    """Convert a DOCX, or a legacy DOC/DOT via LibreOffice first."""
    body, converter, _ = convert_with_provenance(path, config)
    return body, converter
