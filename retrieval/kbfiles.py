"""Reading `kb/`: frontmatter + body + sidecar -> `Document` (brief §5.1-§5.3).

`retrieval/` may import `pipeline.frontmatter` and nothing else from `pipeline/` (brief
§4): this module is the one place that happens, to validate frontmatter with the exact
contract Stage 1 writes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import frontmatter as frontmatter_lib

from pipeline import frontmatter as pipeline_frontmatter

#: Anchors: `<!-- dgx:block=<slug>:pNNN:bNNN -->` at the start of a line, brief §5.2 step 3.
_ANCHOR_RE = re.compile(r"^<!-- dgx:block=([^ ]+) -->\n", re.M)

#: `INCOMPLETE — pages 1, 2, 3 ...` — accept an em dash or a plain hyphen (brief §5.2 step
#: 7 says "also accept a plain hyphen for the dash").
_INCOMPLETE_RE = re.compile(r"INCOMPLETE\s+(?:—|-)\s+pages?\s+([\d, ]+)")

#: Fallback split for documents with no sidecar (brief §5.3): heading lines at level 1-3.
_FALLBACK_HEADING_RE = re.compile(r"(?m)^(#{1,3}[ \t]+.*)$")


class KbFileError(ValueError):
    """A `kb/` document does not satisfy the Stage 1 contract."""


@dataclass
class Block:
    ordinal: int
    block_id: str
    page: Optional[int]
    kind: str
    confidence: Optional[str]
    bbox: Optional[list]
    text: str
    #: Only meaningful for kind == 'heading'.
    level: Optional[int] = None


@dataclass
class Document:
    slug: str
    md_path: Path
    meta: dict[str, Any]
    blocks: list[Block]
    has_sidecar: bool
    incomplete_pages: list[int]
    page_count: Optional[int]
    dropped_empty_blocks: int
    #: Length of the raw markdown body (post-frontmatter), brief §6.2: "documents.chars =
    #: the body length."
    chars: int

    @property
    def title(self) -> str:
        return self.meta["title"]


def _heading_level(text: str) -> int:
    """Leading '#' count of the first line, 1-6; 3 if the block does not start with '#'
    (brief §5.2 step 6)."""
    first_line = text.splitlines()[0] if text else ""
    m = re.match(r"^(#{1,6})(?:\s|$)", first_line)
    if m:
        return len(m.group(1))
    return 3


def _parse_incomplete_pages(body: str) -> list[int]:
    pages: set[int] = set()
    for m in _INCOMPLETE_RE.finditer(body):
        pages.update(int(n) for n in re.findall(r"\d+", m.group(1)))
    return sorted(pages)


def load_document(md_path: Path) -> Document:
    """Parse one `kb/<slug>.md` (+ its sidecar, if any) into a `Document` (brief §5.2)."""
    md_path = Path(md_path)
    slug = md_path.stem
    raw = md_path.read_text(encoding="utf-8")
    post = frontmatter_lib.loads(raw)
    meta = dict(post.metadata)
    body = post.content

    problems = pipeline_frontmatter.validate(meta)
    if problems:
        raise KbFileError(f"{md_path}: frontmatter invalid: {'; '.join(problems)}")

    sidecar_path = md_path.with_suffix(".provenance.json")
    if sidecar_path.exists():
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if sidecar.get("content_sha256") != meta.get("content_sha256"):
            raise KbFileError(f"{md_path}: sidecar/frontmatter sha mismatch")
        return _load_with_sidecar(slug, md_path, meta, body, sidecar)

    return _load_fallback(slug, md_path, meta, body)


def _load_with_sidecar(
    slug: str, md_path: Path, meta: dict[str, Any], body: str, sidecar: dict[str, Any]
) -> Document:
    parts = _ANCHOR_RE.split(body)
    preamble = parts[0]
    rest = parts[1:]
    pairs = list(zip(rest[0::2], rest[1::2]))  # [(block_id, text), ...]

    sidecar_by_id = {b["block_id"]: b for b in sidecar.get("blocks", [])}

    # (block_id, text, sidecar_entry|None) — entry is None only for the synthetic preamble.
    raw: list[tuple[str, str, Optional[dict]]] = []
    if preamble.strip():
        raw.append((f"{slug}:p000:b000", preamble, None))

    md_ids: set[str] = set()
    for block_id, text in pairs:
        entry = sidecar_by_id.get(block_id)
        if entry is None:
            raise KbFileError(f"{md_path}: block {block_id!r} is missing from the sidecar")
        md_ids.add(block_id)
        raw.append((block_id, text, entry))

    missing_in_md = set(sidecar_by_id) - md_ids
    if missing_in_md:
        raise KbFileError(
            f"{md_path}: sidecar block(s) missing from the markdown: "
            f"{', '.join(sorted(missing_in_md))}"
        )

    blocks: list[Block] = []
    dropped = 0
    ordinal = 0
    for block_id, text, entry in raw:
        stripped = text.strip("\n")
        if not stripped.strip():
            dropped += 1
            continue
        if entry is None:
            kind, page, confidence, bbox = "paragraph", None, None, None
        else:
            kind = entry["kind"]
            page = entry.get("page")
            confidence = entry.get("confidence")
            bbox = entry.get("bbox")
        level = _heading_level(stripped) if kind == "heading" else None
        blocks.append(
            Block(
                ordinal=ordinal,
                block_id=block_id,
                page=page,
                kind=kind,
                confidence=confidence,
                bbox=bbox,
                text=stripped,
                level=level,
            )
        )
        ordinal += 1

    pages = [b.page for b in blocks if b.page is not None]
    page_count = max(pages) if pages else None

    return Document(
        slug=slug,
        md_path=md_path,
        meta=meta,
        blocks=blocks,
        has_sidecar=True,
        incomplete_pages=_parse_incomplete_pages(body),
        page_count=page_count,
        dropped_empty_blocks=dropped,
        chars=len(body),
    )


def _load_fallback(slug: str, md_path: Path, meta: dict[str, Any], body: str) -> Document:
    """§5.3: documents with no sidecar (DOCX, CSV). Split at heading lines; a body that is
    a single markdown table becomes one `kind='table'` block."""
    text = body.strip("\n")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    is_single_table = (
        len(lines) >= 2 and all(ln.strip().startswith("|") for ln in lines)
    )

    blocks: list[Block] = []
    if is_single_table:
        blocks.append(
            Block(
                ordinal=0,
                block_id=f"{slug}:p000:b000",
                page=None,
                kind="table",
                confidence=None,
                bbox=None,
                text=text,
            )
        )
    else:
        pieces = _FALLBACK_HEADING_RE.split(text)
        ordinal = 0

        def _add(kind: str, piece_text: str, level: Optional[int] = None) -> None:
            nonlocal ordinal
            stripped = piece_text.strip("\n")
            if not stripped.strip():
                return
            blocks.append(
                Block(
                    ordinal=ordinal,
                    block_id=f"{slug}:p000:b{ordinal:03d}",
                    page=None,
                    kind=kind,
                    confidence=None,
                    bbox=None,
                    text=stripped,
                    level=level,
                )
            )
            ordinal += 1

        _add("paragraph", pieces[0])
        for i in range(1, len(pieces), 2):
            heading_line = pieces[i]
            body_piece = pieces[i + 1] if i + 1 < len(pieces) else ""
            level = len(re.match(r"^#+", heading_line.strip()).group(0))
            _add("heading", heading_line, level=level)
            _add("paragraph", body_piece)

    return Document(
        slug=slug,
        md_path=md_path,
        meta=meta,
        blocks=blocks,
        has_sidecar=False,
        incomplete_pages=_parse_incomplete_pages(body),
        page_count=None,
        dropped_empty_blocks=0,
        chars=len(body),
    )
