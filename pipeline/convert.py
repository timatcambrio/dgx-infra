"""Dispatcher: format -> converter, then frontmatter assembly and a deterministic write.

Conversion is idempotent. A second run over unchanged sources rewrites byte-identical files,
which is what makes `test_determinism.py` a meaningful check rather than a tautology.

The sha256 recorded at conversion time is checked against the source on every later run. A
mismatch is a loud error, not a silent re-convert: since the sources live outside the repo,
that digest is the only evidence that a given `kb/` file corresponds to given bytes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter as frontmatter_lib
import yaml

from . import StopAndAsk
from .config import Config
from .converters import csv_table, markdown, normalize_markdown, office, pdf, slides
from .frontmatter import build as build_frontmatter
from .frontmatter import render as render_frontmatter
from .manifest import (
    TEXT_CLASS_CLEAN,
    TEXT_CLASS_ERROR,
    TEXT_CLASS_NEEDS_OCR,
    TEXT_CLASS_PARTIAL,
    sha256_of,
)

STATUS_WRITTEN = "written"
STATUS_UNCHANGED = "unchanged"
STATUS_STUB = "stub"
STATUS_SKIPPED = "skipped"
STATUS_STOP_AND_ASK = "stop_and_ask"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class ConversionResult:
    slug: str
    source_file: str
    status: str
    output: Path | None = None
    converter: str | None = None
    message: str | None = None
    #: Files this entry wrote under an earlier name and that this run removed.
    superseded: tuple[str, ...] = ()


class SourceDigestMismatch(RuntimeError):
    """The source bytes no longer match what the manifest recorded for this output."""


class SharedKbName(RuntimeError):
    """This entry's kb/ file name already holds another source's conversion.

    Raised, not returned as STOP-AND-ASK: `convert` exits 0 on those, and a document
    missing from the knowledge base must fail the run and stay recorded on the entry.
    """


def output_path(entry: dict[str, Any], config: Config) -> Path:
    return config.kb_dir / f"{entry['slug']}.md"


def provenance_path(output: Path) -> Path:
    return output.with_suffix(".provenance.json")


def _title_for(entry: dict[str, Any], source_path: Path, declared: str | None = None) -> str:
    title = entry.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    if declared:
        return declared
    return csv_table.prettify_filename(source_path)


def _text_class(entry: dict[str, Any]) -> str:
    triage = entry.get("triage") or {}
    return triage.get("text_class") or TEXT_CLASS_CLEAN


def _text_coverage(entry: dict[str, Any]) -> float | None:
    triage = entry.get("triage") or {}
    return triage.get("chars_per_page_mean")


def convert_entry(
    entry: dict[str, Any], config: Config, *, force: bool = False
) -> ConversionResult:
    """Convert one manifest entry into a `kb/` markdown file."""
    slug = entry["slug"]
    source_file = entry["source_file"]
    source_path = config.source_dir() / source_file

    if not source_path.is_file():
        return ConversionResult(
            slug, source_file, STATUS_SKIPPED, message="source file is MISSING"
        )

    digest = sha256_of(source_path)
    recorded = entry.get("sha256")
    if recorded and recorded != digest:
        raise SourceDigestMismatch(
            f"{source_file} has changed on disk since it was inventoried "
            f"(manifest {recorded[:12]}..., file {digest[:12]}...). "
            "Re-run `pipeline inventory` to record the new bytes deliberately."
        )

    text_class = _text_class(entry)
    if text_class == TEXT_CLASS_ERROR:
        triage = entry.get("triage") or {}
        return ConversionResult(
            slug,
            source_file,
            STATUS_STOP_AND_ASK,
            message=triage.get("error") or "document could not be read",
        )

    destination = output_path(entry, config)
    owner = _foreign_owner(destination, source_file)
    if owner is not None:
        path, other = owner
        raise SharedKbName(
            f"{path.relative_to(config.kb_path).as_posix()} already holds {other}, which "
            "has the same kb/ name. Rename or exclude one of the two in SOURCE_DIR; if "
            f"{other} was renamed to this file, run `pipeline prune --yes` first."
        )
    conversion = entry.get("conversion") or {}
    previous = conversion
    source_format = entry["source_format"]
    sidecar_required = _needs_provenance_sidecar(source_format, text_class)
    if (
        not force
        and destination.is_file()
        and conversion.get("source_sha256") == digest
        and (not sidecar_required or provenance_path(destination).is_file())
    ):
        return ConversionResult(
            slug, source_file, STATUS_UNCHANGED, destination, conversion.get("converter")
        )

    try:
        body, converter, status, provenance_blocks, extras = _dispatch(
            entry, source_path, config, text_class
        )
    except StopAndAsk as exc:
        return ConversionResult(slug, source_file, STATUS_STOP_AND_ASK, message=str(exc))

    # A Markdown source is the author's own markdown: tidying it would change their text.
    if source_format != "md":
        body = normalize_markdown(body)

    # What a source declares about itself (Markdown front matter) fills only what the
    # manifest leaves open; it is not a conversion fact, so it is not recorded there.
    declared = extras.pop("declared", None) or {}
    doc_date = entry.get("doc_date") or "UNCONFIRMED"
    if doc_date == "UNCONFIRMED":
        doc_date = declared.get("doc_date", doc_date)

    meta = build_frontmatter(
        title=_title_for(entry, source_path, declared.get("title")),
        source_file=source_file,
        source_format=source_format,
        converter=converter,
        content_sha256=digest,
        text_class=text_class,
        text_coverage=_text_coverage(entry),
        source_url=entry.get("source_url"),
        doc_date=doc_date,
        retrieved=entry.get("retrieved"),
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_frontmatter(meta, body)
    destination.write_text(rendered, encoding="utf-8")
    sidecar = None
    if provenance_blocks is not None:
        sidecar = provenance_path(destination)
        sidecar.write_text(
            _render_provenance_sidecar(
                source_file=source_file,
                source_format=source_format,
                content_sha256=digest,
                converter=converter,
                blocks=provenance_blocks,
            ),
            encoding="utf-8",
        )

    entry["conversion"] = {
        "converter": converter,
        "output": destination.relative_to(config.kb_path).as_posix(),
        "source_sha256": digest,
    }
    if sidecar is not None:
        entry["conversion"]["provenance"] = sidecar.relative_to(config.kb_path).as_posix()
    entry["conversion"].update(extras)
    written = {destination} | ({sidecar} if sidecar is not None else set())
    superseded = _remove_superseded(previous, written, source_file, config)
    return ConversionResult(
        slug, source_file, status, destination, converter, superseded=superseded
    )


def _remove_superseded(
    previous: dict[str, Any], written: set[Path], source_file: str, config: Config
) -> tuple[str, ...]:
    """Delete the files an earlier conversion of this entry wrote under another name.

    A slug change (the extension became part of every slug) moves an entry's output from
    `kb/deck.md` to `kb/deck-pdf.md`. Left in place, the old file is still indexed by
    `kb index`, which reads every `kb/*.md`, and every passage in it is served twice. Only
    a file inside `kb/` that the manifest recorded for this entry AND that still names
    this source is removed: another source may have been given that name since.
    """
    kb_root = config.kb_dir.resolve()
    keep = {path.resolve() for path in written}
    removed: list[str] = []
    for key in ("output", "provenance"):
        rel = previous.get(key)
        if not rel:
            continue
        candidate = (config.kb_path / rel).resolve()
        if candidate in keep or kb_root not in candidate.parents or not candidate.is_file():
            continue
        if _declared_source(candidate) != source_file:
            continue
        candidate.unlink()
        removed.append(rel)
    return tuple(removed)


def _foreign_owner(destination: Path, source_file: str) -> tuple[Path, str] | None:
    """A kb/ file at this entry's name that says it came from another source, and that source.

    Two sources differing only in punctuation or case share a slug; without this the second
    conversion overwrote the first's markdown and sidecar without a word. A file naming no
    source (unreadable, or written by hand) is not claimed by anyone and may be replaced.
    """
    for path in (destination, provenance_path(destination)):
        if not path.is_file():
            continue
        declared = _declared_source(path)
        if declared is not None and declared != source_file:
            return path, declared
    return None


def _declared_source(path: Path) -> str | None:
    """The `source_file` a kb/ markdown file or sidecar names, or None if unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
        if path.name.endswith(".provenance.json"):
            return json.loads(text).get("source_file")
        return frontmatter_lib.loads(text).metadata.get("source_file")
    except (OSError, ValueError, AttributeError, yaml.YAMLError):
        return None


def _dispatch(
    entry: dict[str, Any], source_path: Path, config: Config, text_class: str
) -> tuple[str, str, str, list[dict[str, Any]] | None, dict[str, Any]]:
    """Route to the right converter.

    Returns `(body, converter_name, status, provenance, extras)`; `extras` are converter
    specific facts recorded verbatim under the manifest entry's `conversion` key.
    """
    source_format = entry["source_format"]

    if source_format == "csv":
        body, converter, provenance = csv_table.convert_with_provenance(
            source_path,
            config,
            provenance_slug=entry["slug"],
            title=entry.get("title"),
            description=entry.get("description"),
            csv_mode=entry.get("csv_mode", "table"),
        )
        return body, converter, STATUS_WRITTEN, provenance, {}

    if source_format == "pdf":
        if text_class == TEXT_CLASS_NEEDS_OCR:
            body, converter = pdf.needs_ocr_stub(source_path, config)
            return body, converter, STATUS_STUB, None, {}
        if text_class in (TEXT_CLASS_CLEAN, TEXT_CLASS_PARTIAL):
            triage = entry.get("triage") or {}
            body, converter, provenance = pdf.convert_with_provenance(
                source_path,
                config,
                provenance_slug=entry["slug"],
                low_pages=triage.get("low_pages") or (),
                image_pages=triage.get("image_pages") or (),
                max_image_coverage=triage.get("max_image_coverage") or 0.0,
            )
            return body, converter, STATUS_WRITTEN, provenance, {}
        raise StopAndAsk(f"unexpected text_class {text_class!r} for {source_path.name}")

    if source_format in ("docx", "doc"):
        body, converter, provenance, extras = office.convert_with_provenance(
            source_path, config, provenance_slug=entry["slug"]
        )
        return body, converter, STATUS_WRITTEN, provenance, extras

    if source_format in ("pptx", "ppt", "odp"):
        body, converter, provenance, extras = slides.convert_with_provenance(
            source_path, config, provenance_slug=entry["slug"]
        )
        return body, converter, STATUS_WRITTEN, provenance, extras

    if source_format == "md":
        body, converter, provenance, extras = markdown.convert_with_provenance(
            source_path, provenance_slug=entry["slug"]
        )
        return body, converter, STATUS_WRITTEN, provenance, extras

    raise StopAndAsk(f"no converter for source_format {source_format!r}")


def _needs_provenance_sidecar(source_format: str, text_class: str) -> bool:
    if source_format == "pdf":
        return text_class in (TEXT_CLASS_CLEAN, TEXT_CLASS_PARTIAL)
    return source_format in ("docx", "doc", "csv", "md", "pptx", "ppt", "odp")


def _render_provenance_sidecar(
    *,
    source_file: str,
    source_format: str,
    content_sha256: str,
    converter: str,
    blocks: list[dict[str, Any]],
) -> str:
    payload = {
        "version": 1,
        "source_file": source_file,
        "source_format": source_format,
        "content_sha256": content_sha256,
        "converter": converter,
        "blocks": blocks,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
