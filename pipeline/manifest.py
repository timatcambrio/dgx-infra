"""`corpus.yaml` — read, write, and idempotent upsert.

The manifest is the only durable link between a `kb/` markdown file and the source document
it came from, because the sources live outside both repos and are never copied in. Two
properties follow and are load-bearing:

* **Paths are relative to the source root, never absolute.** Two machines holding the same
  documents at different absolute paths must produce the same `corpus.yaml`, so the root's
  own path is stored nowhere.
* **Entries are never silently dropped.** A source file that has disappeared is marked
  `missing` and kept, because the entry is the record that a conversion once happened.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .config import SUPPORTED_EXTENSIONS

MANIFEST_VERSION = 1

STATUS_PRESENT = "present"
STATUS_MISSING = "missing"

TEXT_CLASS_CLEAN = "clean"
TEXT_CLASS_PARTIAL = "partial"
TEXT_CLASS_NEEDS_OCR = "needs_ocr"
#: Not a triage outcome -- a document that could not be read at all (encrypted, corrupt).
#: Surfaced as a STOP-AND-ASK in the report rather than silently classified.
TEXT_CLASS_ERROR = "error"

TEXT_CLASSES = (
    TEXT_CLASS_CLEAN,
    TEXT_CLASS_PARTIAL,
    TEXT_CLASS_NEEDS_OCR,
    TEXT_CLASS_ERROR,
)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def sha256_of(path: Path) -> str:
    """Streaming sha256 so a large PDF does not have to fit in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def slug_for(relative_path: str | PurePosixPath) -> str:
    """A stable, filesystem-safe slug derived from the path relative to the source root.

    Directory structure is preserved as `__` so that `rfsuny/travel.pdf` and
    `gsa/travel.pdf` do not collide.
    """
    pure = PurePosixPath(str(relative_path).replace("\\", "/"))
    parts = list(pure.parts[:-1]) + [pure.stem]
    cleaned = [_SLUG_STRIP.sub("-", part.lower()).strip("-") for part in parts]
    slug = "__".join(part for part in cleaned if part)
    return slug or "document"


def relative_source_path(path: Path, source_root: Path) -> str:
    """POSIX-style path relative to the source root. Errors rather than storing an absolute."""
    try:
        relative = path.resolve().relative_to(source_root.resolve())
    except ValueError as exc:
        raise ValueError(f"{path} is not inside the source root {source_root}") from exc
    return relative.as_posix()


def format_for(path: Path) -> str | None:
    return SUPPORTED_EXTENSIONS.get(path.suffix.lower())


def empty() -> dict[str, Any]:
    return {"version": MANIFEST_VERSION, "documents": []}


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a mapping")
    data.setdefault("version", MANIFEST_VERSION)
    data.setdefault("documents", [])
    return data


def save(manifest: dict[str, Any], path: Path) -> None:
    """Write deterministically: sorted keys, sorted entries, no timestamps."""
    manifest["documents"] = sorted(
        manifest.get("documents", []), key=lambda entry: entry.get("source_file", "")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            manifest,
            sort_keys=True,
            default_flow_style=False,
            allow_unicode=True,
            width=100,
        ),
        encoding="utf-8",
    )


def by_source_file(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["source_file"]: entry for entry in manifest.get("documents", [])}


def new_entry(source_file: str, source_format: str) -> dict[str, Any]:
    """A manifest entry with every field the frontmatter contract will later need.

    `doc_date` starts as UNCONFIRMED and stays that way unless a real date is found in the
    document. A fabricated date is worse than an absent one.
    """
    entry: dict[str, Any] = {
        "slug": slug_for(source_file),
        "source_file": source_file,
        "source_format": source_format,
        "sha256": None,
        "size_bytes": None,
        "status": STATUS_PRESENT,
        "title": None,
        "description": None,
        "source_url": None,
        "doc_date": "UNCONFIRMED",
        "retrieved": None,
        "triage": None,
        "conversion": None,
    }
    if source_format == "csv":
        # The interface is defined now so the manifest schema is stable; `record` mode is
        # not implemented and encountering it is a STOP-AND-ASK.
        entry["csv_mode"] = "table"
    return entry


def upsert(manifest: dict[str, Any], path: Path, source_root: Path) -> tuple[dict[str, Any], str]:
    """Insert or update the entry for `path`. Returns the entry and what happened.

    Outcome is one of `added`, `changed`, `unchanged`, `restored`. A changed sha256 clears
    the stale triage and conversion records rather than leaving numbers that describe a file
    that no longer exists.
    """
    source_file = relative_source_path(path, source_root)
    source_format = format_for(path)
    if source_format is None:
        raise ValueError(f"Unsupported extension: {path.suffix}")

    index = by_source_file(manifest)
    digest = sha256_of(path)
    size = path.stat().st_size

    entry = index.get(source_file)
    if entry is None:
        entry = new_entry(source_file, source_format)
        entry["sha256"] = digest
        entry["size_bytes"] = size
        manifest.setdefault("documents", []).append(entry)
        return entry, "added"

    was_missing = entry.get("status") == STATUS_MISSING
    entry["status"] = STATUS_PRESENT
    entry["source_format"] = source_format
    entry.setdefault("slug", slug_for(source_file))

    if entry.get("sha256") != digest:
        entry["sha256"] = digest
        entry["size_bytes"] = size
        entry["triage"] = None
        entry["conversion"] = None
        return entry, "changed"

    entry["size_bytes"] = size
    return entry, "restored" if was_missing else "unchanged"


def mark_missing(entry: dict[str, Any]) -> None:
    """A source file that has disappeared is recorded, not removed, and never fails a run."""
    entry["status"] = STATUS_MISSING
