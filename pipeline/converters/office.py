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
import posixpath
import shutil
import subprocess
import zipfile
from io import BytesIO

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
    sanitised, dangling = _sanitise_package(path)
    if dangling:
        _logger.warning(
            "%s: %d relationship(s) point at parts the package does not contain; "
            "treated as external references",
            path.name,
            len(dangling),
        )
    backend = MsWordDocumentBackend(
        in_doc=in_doc, path_or_stream=sanitised if sanitised is not None else path
    )
    _strip_non_element_nodes(backend.docx_obj)
    return backend.convert(), dangling


_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _sanitise_package(path: Path) -> tuple[BytesIO | None, list[str]]:
    """Make an OPC package loadable by re-marking dangling relationships as external.

    OPC (the zip-and-XML container under every Office file) requires an internal
    relationship to target a part that exists in the package. Some publishing tools emit
    relationships whose target is a directory (`media/`) or a file that was never packaged.
    Word ignores those; python-docx tries to load every internal target as a part and
    fails on the first one, taking the whole document with it.

    A relationship with nothing behind it carries no content, so the lossless treatment is
    to mark it `TargetMode="External"`: python-docx then skips it on load, the id stays
    resolvable so nothing that references it raises, and Docling's image resolver already
    skips external references with a log line. Returns `(stream, dangling)`: the rewritten
    package as a stream when anything changed (else `None`), and one description per
    dangling relationship so the caller can record that the document points at content the
    file does not contain.
    """
    from lxml import etree  # noqa: PLC0415 - lazy, alongside the docling imports

    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        part_names = {info.filename for info in infos if not info.is_dir()}
        payload = {info.filename: archive.read(info.filename) for info in infos}

    dangling: list[str] = []
    for name in list(payload):
        if not name.endswith(".rels"):
            continue
        root = etree.fromstring(payload[name])
        base = name.split("_rels/", 1)[0]  # '' for the package rels, 'word/' for document
        changed = False
        for rel in root:
            if not isinstance(rel.tag, str) or etree.QName(rel).localname != "Relationship":
                continue
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target") or ""
            if target.startswith("/"):
                resolved = posixpath.normpath(target).lstrip("/")
            else:
                resolved = posixpath.normpath(posixpath.join(base, target))
            if resolved in part_names:
                continue
            rel.set("TargetMode", "External")
            dangling.append(f"{name}: {rel.get('Id')} -> {target}")
            changed = True
        if changed:
            payload[name] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )

    if not dangling:
        return None, []

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
    buffer.seek(0)
    return buffer, dangling


#: Every WordprocessingML part shares this content-type prefix: the main document, headers,
#: footers, footnotes, endnotes, comments. Docling walks several of them.
_WML_CONTENT_TYPE_PREFIX = "application/vnd.openxmlformats-officedocument.wordprocessingml."


def _strip_non_element_nodes(docx_obj) -> int:
    """Remove XML comment and processing-instruction nodes from every WordprocessingML part
    of an opened document, in place. Returns the number removed.

    Regulation publishing systems emit `<!--Topic ...-->` markers and processing
    instructions between paragraphs. Docling's Word backend walks the body, and then every
    header and footer part, asking lxml for each child's tag name; a comment or processing
    instruction has none, so the walk raised `ValueError: Invalid input tag`.

    The invariant established here is spec-level rather than case-by-case: WordprocessingML
    carries no document content in XML comments or processing instructions (Word itself
    discards both on load; reviewer comments are `w:comment` *elements* in a separate part
    and are untouched), so removing every such node from every WML part loses nothing and
    leaves Docling a tree it can walk anywhere. Parts are reached through the package, not
    through `section.header`, because python-docx's header accessors create a definition
    when one is absent and that would change the document Docling sees.
    """
    from docx.opc.part import XmlPart  # noqa: PLC0415 - lazy, alongside the docling imports
    from lxml import etree  # noqa: PLC0415

    removed = 0
    for part in docx_obj.part.package.iter_parts():
        if not isinstance(part, XmlPart):
            continue
        if not part.content_type.startswith(_WML_CONTENT_TYPE_PREFIX):
            continue
        for node in list(part.element.iter(etree.Comment, etree.ProcessingInstruction)):
            parent = node.getparent()
            if parent is None:
                continue
            # lxml drops a removed node's tail text with it. Between WML elements that tail
            # is insignificant whitespace, but it is kept so nothing else changes.
            if node.tail:
                previous = node.getprevious()
                if previous is not None:
                    previous.tail = (previous.tail or "") + node.tail
                else:
                    parent.text = (parent.text or "") + node.tail
            parent.remove(node)
            removed += 1
    return removed


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
) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
    """Convert a DOCX, or a legacy DOC/DOT via LibreOffice first. Anchors every block.

    Returns `(body, converter, provenance, extras)`. `extras` is what the manifest records
    under `conversion` beyond the standard keys: `images` (embedded pictures, whose content
    is not in the text layer) and `dangling_relationships` (references to parts the package
    does not contain). Both are evidence for the report, not verdicts.

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

    document, dangling = _load_document(docx_path)
    blocks, picture_count, empty_skipped = _walk_items(document)
    if empty_skipped:
        _logger.debug("%s: skipped %d empty item(s)", path.name, empty_skipped)

    if provenance_slug is None:
        body = "\n\n".join(text for text, _, _ in blocks)
        provenance: list[dict[str, Any]] = []
    else:
        body, provenance = render_block_provenance(blocks, provenance_slug)

    notes: list[str] = []
    if picture_count:
        notes.append(
            f"> **INCOMPLETE — this document embeds {picture_count} image(s), and their "
            "content is not in the text layer.** No OCR was attempted. Each is marked in "
            "place below."
        )
    if dangling:
        notes.append(
            f"> **INCOMPLETE — {len(dangling)} reference(s) in this document point at "
            "content the file does not contain** (for example an image that was never "
            "packaged). Nothing can be recovered for them."
        )
    if notes:
        body = "\n\n".join([*notes, body])

    extras = {"images": picture_count, "dangling_relationships": len(dangling)}
    return body, converter, provenance, extras


def convert(path: Path, config: Config) -> tuple[str, str]:
    """Convert a DOCX, or a legacy DOC/DOT via LibreOffice first."""
    body, converter, _, _ = convert_with_provenance(path, config)
    return body, converter
