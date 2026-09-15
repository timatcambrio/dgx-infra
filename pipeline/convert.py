"""Dispatcher: format -> converter, then frontmatter assembly and a deterministic write.

Conversion is idempotent. A second run over unchanged sources rewrites byte-identical files,
which is what makes `test_determinism.py` a meaningful check rather than a tautology.

The sha256 recorded at conversion time is checked against the source on every later run. A
mismatch is a loud error, not a silent re-convert: since the sources live outside the repo,
that digest is the only evidence that a given `kb/` file corresponds to given bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import StopAndAsk
from .config import Config
from .converters import csv_table, normalize_markdown, office, pdf
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


class SourceDigestMismatch(RuntimeError):
    """The source bytes no longer match what the manifest recorded for this output."""


def output_path(entry: dict[str, Any], config: Config) -> Path:
    return config.kb_dir / f"{entry['slug']}.md"


def _title_for(entry: dict[str, Any], source_path: Path) -> str:
    title = entry.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
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
    conversion = entry.get("conversion") or {}
    if (
        not force
        and destination.is_file()
        and conversion.get("source_sha256") == digest
    ):
        return ConversionResult(
            slug, source_file, STATUS_UNCHANGED, destination, conversion.get("converter")
        )

    source_format = entry["source_format"]
    try:
        body, converter, status = _dispatch(entry, source_path, config, text_class)
    except StopAndAsk as exc:
        return ConversionResult(slug, source_file, STATUS_STOP_AND_ASK, message=str(exc))

    body = normalize_markdown(body)

    meta = build_frontmatter(
        title=_title_for(entry, source_path),
        source_file=source_file,
        source_format=source_format,
        converter=converter,
        content_sha256=digest,
        text_class=text_class,
        text_coverage=_text_coverage(entry),
        source_url=entry.get("source_url"),
        doc_date=entry.get("doc_date") or "UNCONFIRMED",
        retrieved=entry.get("retrieved"),
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_frontmatter(meta, body)
    destination.write_text(rendered, encoding="utf-8")

    entry["conversion"] = {
        "converter": converter,
        "output": destination.relative_to(config.kb_path).as_posix(),
        "source_sha256": digest,
    }
    return ConversionResult(slug, source_file, status, destination, converter)


def _dispatch(
    entry: dict[str, Any], source_path: Path, config: Config, text_class: str
) -> tuple[str, str, str]:
    """Route to the right converter. Returns `(body, converter_name, status)`."""
    source_format = entry["source_format"]

    if source_format == "csv":
        body, converter = csv_table.convert(
            source_path,
            config,
            title=entry.get("title"),
            description=entry.get("description"),
            csv_mode=entry.get("csv_mode", "table"),
        )
        return body, converter, STATUS_WRITTEN

    if source_format == "pdf":
        if text_class == TEXT_CLASS_NEEDS_OCR:
            body, converter = pdf.needs_ocr_stub(source_path, config)
            return body, converter, STATUS_STUB
        if text_class in (TEXT_CLASS_CLEAN, TEXT_CLASS_PARTIAL):
            triage = entry.get("triage") or {}
            body, converter = pdf.convert(
                source_path,
                config,
                low_pages=triage.get("low_pages") or (),
                image_pages=triage.get("image_pages") or (),
                max_image_coverage=triage.get("max_image_coverage") or 0.0,
            )
            return body, converter, STATUS_WRITTEN
        raise StopAndAsk(f"unexpected text_class {text_class!r} for {source_path.name}")

    if source_format in ("docx", "doc"):
        body, converter = office.convert(source_path, config)
        return body, converter, STATUS_WRITTEN

    raise StopAndAsk(f"no converter for source_format {source_format!r}")
