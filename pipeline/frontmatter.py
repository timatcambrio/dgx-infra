"""The `kb/` frontmatter contract: build, render, and validate.

`content_sha256` is the load-bearing field. Because the source documents live outside the
repo, it is the only link back from a converted markdown file to the bytes it came from — a
mismatch on a later run is a loud error rather than a silent re-convert.

`doc_date` is the date of the *source content*, never the conversion date. If no honest
date can be found in the document it stays `UNCONFIRMED`. Stale-source risk is the top
product risk on this project and a fabricated date is worse than an absent one.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import frontmatter as frontmatter_lib
import yaml

#: Explicit field order. Deterministic by construction (dicts preserve insertion order) and
#: it keeps the emitted file in the order the contract documents, which alphabetical
#: sorting would scramble.
FIELD_ORDER = (
    "title",
    "source_file",
    "source_format",
    "source_url",
    "doc_date",
    "retrieved",
    "converter",
    "text_coverage",
    "text_class",
    "needs_ocr",
    "content_sha256",
)

VALID_FORMATS = frozenset({"pdf", "docx", "doc", "csv"})
VALID_TEXT_CLASSES = frozenset({"clean", "partial", "needs_ocr"})
UNCONFIRMED = "UNCONFIRMED"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FrontmatterError(ValueError):
    """Frontmatter does not satisfy the contract."""


def build(
    *,
    title: str,
    source_file: str,
    source_format: str,
    converter: str,
    content_sha256: str,
    text_class: str,
    text_coverage: float | None = None,
    needs_ocr: bool | None = None,
    source_url: str | None = None,
    doc_date: str = UNCONFIRMED,
    retrieved: str | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "source_file": source_file,
        "source_format": source_format,
        "source_url": source_url,
        "doc_date": doc_date or UNCONFIRMED,
        "retrieved": retrieved,
        "converter": converter,
        "text_coverage": text_coverage,
        "text_class": text_class,
        "needs_ocr": (text_class == "needs_ocr") if needs_ocr is None else needs_ocr,
        "content_sha256": content_sha256,
    }


def validate(meta: dict[str, Any]) -> list[str]:
    """Return a list of contract violations. Empty means valid."""
    problems: list[str] = []

    missing = [field for field in FIELD_ORDER if field not in meta]
    if missing:
        problems.append(f"missing fields: {', '.join(missing)}")

    unexpected = sorted(set(meta) - set(FIELD_ORDER))
    if unexpected:
        problems.append(f"unexpected fields: {', '.join(unexpected)}")

    title = meta.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append("title must be a non-empty string")

    source_file = meta.get("source_file")
    if not isinstance(source_file, str) or not source_file:
        problems.append("source_file must be a non-empty string")
    elif Path(source_file).is_absolute() or source_file.startswith("~"):
        problems.append(f"source_file must be relative to SOURCE_DIR, got {source_file!r}")

    if meta.get("source_format") not in VALID_FORMATS:
        problems.append(
            f"source_format must be one of {sorted(VALID_FORMATS)}, "
            f"got {meta.get('source_format')!r}"
        )

    source_url = meta.get("source_url")
    if source_url is not None and not isinstance(source_url, str):
        problems.append("source_url must be a string or null")

    doc_date = meta.get("doc_date")
    if not isinstance(doc_date, str) or not (
        doc_date == UNCONFIRMED or _ISO_DATE.match(doc_date)
    ):
        problems.append(
            f"doc_date must be an ISO date or {UNCONFIRMED!r}, got {doc_date!r}"
        )

    retrieved = meta.get("retrieved")
    if retrieved is not None and not (
        isinstance(retrieved, str) and _ISO_DATE.match(retrieved)
    ):
        problems.append("retrieved must be an ISO date or null")

    converter = meta.get("converter")
    if not isinstance(converter, str) or not converter.strip():
        problems.append("converter must be a non-empty string")

    coverage = meta.get("text_coverage")
    if coverage is not None and not isinstance(coverage, (int, float)):
        problems.append("text_coverage must be a number or null")

    text_class = meta.get("text_class")
    if text_class not in VALID_TEXT_CLASSES:
        problems.append(
            f"text_class must be one of {sorted(VALID_TEXT_CLASSES)}, got {text_class!r}"
        )

    needs_ocr = meta.get("needs_ocr")
    if not isinstance(needs_ocr, bool):
        problems.append("needs_ocr must be a boolean")
    elif text_class in VALID_TEXT_CLASSES and needs_ocr != (text_class == "needs_ocr"):
        problems.append(
            f"needs_ocr ({needs_ocr}) contradicts text_class ({text_class})"
        )

    digest = meta.get("content_sha256")
    if not isinstance(digest, str) or not _SHA256.match(digest):
        problems.append("content_sha256 must be a lowercase 64-character sha256 hex digest")

    return problems


def render(meta: dict[str, Any], body: str) -> str:
    """Serialise to a markdown file with a YAML frontmatter block.

    Written by hand rather than through `python-frontmatter`'s dumper so that field order,
    trailing whitespace, and the final newline are all fixed — byte-identical output on
    every run is a hard requirement.
    """
    problems = validate(meta)
    if problems:
        raise FrontmatterError("; ".join(problems))

    ordered = {field: meta[field] for field in FIELD_ORDER}
    block = yaml.safe_dump(
        ordered, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000
    )
    text = body.strip("\n")
    return f"---\n{block}---\n\n{text}\n"


def parse_file(path: Path) -> dict[str, Any]:
    """Read the frontmatter of a `kb/` file."""
    return dict(frontmatter_lib.loads(path.read_text(encoding="utf-8")).metadata)
