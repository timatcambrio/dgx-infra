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

import shutil
import subprocess

from pathlib import Path

from ..config import Config

CONVERTER_DOCLING = "docling (docx)"
CONVERTER_LIBREOFFICE_DOCLING = "libreoffice+docling"

SOFFICE_BINARY = "soffice"
#: Timeout for one LibreOffice invocation. It is a GUI application in headless clothing and
#: will hang forever on a malformed file rather than exit non-zero.
SOFFICE_TIMEOUT_SECONDS = 180


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


def docx_to_markdown(path: Path) -> str:
    """DOCX -> markdown via Docling's Word backend. Fetches no model.

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
    document = MsWordDocumentBackend(in_doc=in_doc, path_or_stream=path).convert()
    return document.export_to_markdown()


def convert(path: Path, config: Config) -> tuple[str, str]:
    """Convert a DOCX, or a legacy DOC/DOT via LibreOffice first."""
    suffix = path.suffix.lower()
    if suffix in (".doc", ".dot"):
        docx_path = doc_to_docx(path, config)
        return docx_to_markdown(docx_path), CONVERTER_LIBREOFFICE_DOCLING
    return docx_to_markdown(path), CONVERTER_DOCLING
