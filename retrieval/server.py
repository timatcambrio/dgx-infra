"""The MCP server (brief §6.5): five read-only tools over the indexed `kb/`.

`build_server(cfg)` returns a configured `FastMCP`, usable either way:

- `kb serve --transport stdio` (`cli.py`) calls `mcp.run(transport="stdio")` directly.
- `kb serve --transport http` (S4, brief §9) calls `build_http_app(cfg)` instead, which
  wraps `mcp.streamable_http_app()` in the bearer middleware (`auth.py`, brief §6.5.6) and
  hands the result to uvicorn (`cli.py`). The host/port/transport-security settings baked
  into `build_server` (brief §6.5.5) are inert under stdio — nothing reads them there — so
  one constructor serves both transports without a second code path.

**No fetch path returns unbounded text.** `FETCH_MAX_CHARS` is a ceiling on every
`FetchResponse.text`, not only on a whole-document fetch: `_oversize_response` is where a
section, page or chunk over the cap goes instead, and a document's outline listing is
bounded the same way. A section is *not* a bounded unit — see `_oversize_response`.

**stdout is the protocol channel in stdio mode.** Nothing in this module or anything it
imports at import time or at call time may `print` or otherwise write to stdout. All
logging — including the one-JSON-line-per-call log (brief §6.5.3) — goes to stderr.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional, TypedDict

import asyncpg
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from . import auth as auth_module
from . import cite as cite_module
from . import db as db_module
from . import ids as ids_module
from . import search as search_module
from .config import Config
from .embed import embed_query

# --------------------------------------------------------------------------------------
# Logging (brief §6.5.3): one JSON line per tool call, to stderr. Never stdout.
# --------------------------------------------------------------------------------------

_CALL_LOGGER = logging.getLogger("kb.server.calls")
_CALL_LOGGER.setLevel(logging.INFO)
_CALL_LOGGER.propagate = False
if not _CALL_LOGGER.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _CALL_LOGGER.addHandler(_handler)


def _log_call(tool: str, args: dict[str, Any], result_ids: list[str], ms: int, error: Optional[str]) -> None:
    line = json.dumps(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool,
            "args": args,
            "result_ids": result_ids,
            "ms": ms,
            "error": error,
        },
        default=str,
    )
    _CALL_LOGGER.info(line)


class _CallTimer:
    """Times one tool call and logs it on exit (brief §6.5.3), success or error.

    A plain (non-async) context manager: the `with` block itself contains `await`s, which
    is fine — only `__enter__`/`__exit__` need to be synchronous.
    """

    def __init__(self, tool: str, args: dict[str, Any]) -> None:
        self.tool = tool
        self.args = args
        self.result_ids: list[str] = []
        self._start = 0.0

    def __enter__(self) -> "_CallTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        error = str(exc) if exc is not None else None
        ms = int((time.monotonic() - self._start) * 1000)
        _log_call(self.tool, self.args, self.result_ids, ms, error)


# --------------------------------------------------------------------------------------
# `instructions` — verbatim, brief §6.5.1.
# --------------------------------------------------------------------------------------

INSTRUCTIONS = """\
This server is a read-only knowledge base of institutional documents converted from PDF,
DOCX and CSV. Workflow: call `search` to locate candidate sections, then call `fetch` on
a result id to read the whole section. If a section looks truncated or a table looks
flattened, fetch the enclosing page (`page:<slug>:pNNN`) or use `get_section` with
`neighbours=1`. For questions about a known document, call `list_documents` then
`get_outline`, and fetch sections directly. Always quote the `citation` string from
`metadata` verbatim in your answer. Converted text may contain flattened tables and
`> **INCOMPLETE**` notes; treat reconstructed structure as an inference and say so.
`doc_date: UNCONFIRMED` means no reliable date was found; do not invent one. A fetch of
anything over FETCH_MAX_CHARS comes back with `metadata.truncated: true` and, in place of
the text, the smaller ids that cover it (chunk ids, and page ids where the document has
pages) — fetch one of those; never quote such a reply as the document's words.\
"""

RO = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


# --------------------------------------------------------------------------------------
# Lifespan context: the asyncpg pool and the embedder, both opened once per server run.
# --------------------------------------------------------------------------------------


@dataclass
class ServerContext:
    pool: asyncpg.Pool
    cfg: Config
    embed_query_fn: Callable[[str], list[float]]


def _make_lifespan(cfg: Config):
    @asynccontextmanager
    async def lifespan(_server: "FastMCP[ServerContext]") -> AsyncIterator[ServerContext]:
        pool = await db_module.create_pool(cfg.require_database_url(), min_size=1, max_size=5)

        def embed_fn(query: str) -> list[float]:
            return embed_query(
                query,
                base_url=cfg.ollama_base_url,
                model=cfg.embed_model,
                embed_dim=cfg.embed_dim,
            )

        try:
            yield ServerContext(pool=pool, cfg=cfg, embed_query_fn=embed_fn)
        finally:
            await pool.close()

    return lifespan


# --------------------------------------------------------------------------------------
# Output shapes (brief §6.5.2) — TypedDicts so FastMCP emits `outputSchema` and
# `structuredContent`, and never `Optional[...]` as a tool's own return type (nullable
# *fields* inside are fine).
# --------------------------------------------------------------------------------------


class SearchResult(TypedDict):
    id: str
    title: str
    url: str
    snippet: str
    pages: Optional[list[int]]
    doc_date: str
    text_class: str
    score: float


class SearchResponse(TypedDict):
    results: list[SearchResult]


class FetchMeta(TypedDict):
    kind: str  # "section" | "page" | "document" | "chunk"
    slug: str
    pages: Optional[list[int]]
    block_ids: Optional[list[str]]
    doc_date: str
    text_class: str
    incomplete_pages: list[int]
    source_file: Optional[str]
    source_url: Optional[str]
    citation: str
    chars: int
    truncated: bool


class FetchResponse(TypedDict):
    id: str
    title: str
    text: str
    url: str
    metadata: FetchMeta


class DocumentEntry(TypedDict):
    id: str
    title: str
    doc_date: str
    text_class: str
    page_count: Optional[int]
    sections: int
    chars: int
    summary: Optional[str]


class ListDocumentsResponse(TypedDict):
    documents: list[DocumentEntry]


class OutlineSection(TypedDict):
    id: str
    level: int
    heading: Optional[str]
    pages: Optional[list[int]]
    chars: int


class OutlineResponse(TypedDict):
    id: str
    title: str
    doc_date: str
    text_class: str
    incomplete_pages: list[int]
    sections: list[OutlineSection]


# --------------------------------------------------------------------------------------
# Small shared helpers.
# --------------------------------------------------------------------------------------


def _strip_title(title: str, heading_path: str) -> str:
    """`heading_path` with the leading `"<title> › "` (or a bare `title`) removed."""
    if heading_path == title:
        return ""
    prefix = f"{title} › "
    if heading_path.startswith(prefix):
        return heading_path[len(prefix) :]
    return heading_path


def _section_title(title: str, heading_path: str) -> str:
    """`f"{doc.title} › {heading_path_without_title}"` (brief §6.5.2), falling back to
    the bare title when there is no heading to append (section 0, no heading)."""
    tail = _strip_title(title, heading_path)
    return f"{title} › {tail}" if tail else title


def _url(cfg: Config, rel_path: str, block_id: Optional[str]) -> str:
    return f"{cfg.kb_url_base}/{rel_path}#dgx:block={block_id}"


async def _require_document(conn: asyncpg.Connection, slug: str, raw_id: str) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM documents WHERE slug = $1", slug)
    if row is None:
        raise ValueError(f"unknown id: {raw_id}")
    return row


async def _require_section(
    conn: asyncpg.Connection, slug: str, section_index: int, raw_id: str
) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT * FROM sections WHERE slug = $1 AND section_index = $2", slug, section_index
    )
    if row is None:
        raise ValueError(f"unknown id: {raw_id}")
    return row


def _build_citation(doc: asyncpg.Record, *, heading_path: str, page_first, page_last, first_block_id, last_block_id) -> str:
    return cite_module.build_citation(
        title=doc["title"],
        heading_path=heading_path,
        source_file=doc["source_file"],
        page_first=page_first,
        page_last=page_last,
        doc_date=doc["doc_date"],
        first_block_id=first_block_id or "",
        last_block_id=last_block_id or "",
    )


#: Chunk ids an oversize breakdown names as landmarks. The breakdown has to be bounded
#: whatever the unit's size, so it names the chunk index *range* (every index in it is a
#: valid id) and samples only this many of them.
_OVERSIZE_SIGNPOSTS = 12


def _landmark(text: str, limit: int = 110) -> str:
    """One line of `text`, clipped to `limit` characters, chosen to tell this chunk from
    its neighbours: the first line normally, but the *last* row of a pipe table, because
    table-row chunking repeats the header row (and the previous piece's last row) on every
    piece — so every piece of one table opens identically. Runs of whitespace collapse: a
    table row is mostly column padding, and 110 characters of padding say nothing.
    """
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    line = lines[-1] if lines[0].startswith("|") else lines[0]
    return line[: limit - 1] + "…" if len(line) > limit else line


async def _section_chars(conn: asyncpg.Connection, slug: str) -> dict[int, int]:
    """`{section_index: length of the text a section fetch would return}` — the same join
    `get_outline` reports `chars` from, so the two agree."""
    rows = await conn.fetch(
        """
        SELECT s.section_index,
               length(coalesce(string_agg(b.text, E'\\n\\n' ORDER BY b.ordinal), '')) AS chars
        FROM sections s
        LEFT JOIN blocks b
          ON b.slug = s.slug AND b.ordinal BETWEEN s.block_first AND s.block_last
        WHERE s.slug = $1
        GROUP BY s.section_index
        """,
        slug,
    )
    return {r["section_index"]: r["chars"] for r in rows}


async def _oversize_response(
    conn: asyncpg.Connection,
    cfg: Config,
    doc: asyncpg.Record,
    *,
    response_id: str,
    title: str,
    kind: str,
    heading_path: str,
    blocks: list[asyncpg.Record],
    chars: int,
    page_first: Optional[int],
    page_last: Optional[int],
    narrow_to_chunks: bool = True,
    extra_notes: tuple[str, ...] = (),
) -> FetchResponse:
    """The bounded stand-in for a unit whose own text is over `FETCH_MAX_CHARS`.

    `_fetch_document` has always refused an over-cap document, but a *section* is not a
    bounded unit: in the DOCX-derived documents one heading can span thousands of blocks,
    and sections of over half a million characters exist — more than the document path
    declines to send, while that refusal recommends section ids. So every fetch path ends
    here instead when its joined text crosses the cap, and returns what the caller needs
    to ask a smaller question: the block range it declined, the chunk ids that cover it
    (`fetch` on one returns that chunk alone), and a landmark line from a sample of them.
    The reply is bounded by construction — a fixed number of landmark lines, never one
    line per block or per chunk.
    """
    slug = doc["slug"]
    block_first = blocks[0]["ordinal"]
    block_last = blocks[-1]["ordinal"]
    first_block_id = blocks[0]["block_id"]
    last_block_id = blocks[-1]["block_id"]

    chunks: list[asyncpg.Record] = []
    if narrow_to_chunks:
        chunks = list(
            await conn.fetch(
                "SELECT chunk_index, page_first, page_last, length(text) AS chars, text "
                "FROM chunks WHERE slug = $1 AND block_last >= $2 AND block_first <= $3 "
                "ORDER BY chunk_index",
                slug,
                block_first,
                block_last,
            )
        )

    lines = [
        f"# {title} — too large to fetch",
        "",
        (
            f"This {kind} is {chars:,} characters, over FETCH_MAX_CHARS "
            f"({cfg.fetch_max_chars:,}), so its text was not returned. It is blocks "
            f"{first_block_id}…{last_block_id} ({block_last - block_first + 1} blocks). "
            "Fetch a smaller unit:"
        ),
        "",
    ]
    if chunks:
        lo = chunks[0]["chunk_index"]
        hi = chunks[-1]["chunk_index"]
        lines.append(
            f"- **Chunks** `{ids_module.chunk_id(slug, lo)}` … "
            f"`{ids_module.chunk_id(slug, hi)}` — {len(chunks)} chunks, every index in "
            "that range a valid id. `fetch` on a chunk id returns that chunk alone."
        )
    if page_first is not None and page_last is not None and page_last > page_first:
        # Only a *range* of pages is worth naming. One page containing an over-cap unit is
        # at least as large as it, so its own fetch would come back here again.
        lines.append(
            f"- **Pages** `{ids_module.page_id(slug, page_first)}` … "
            f"`{ids_module.page_id(slug, page_last)}`."
        )
    lines.extend(f"- {note}" for note in extra_notes)
    if not chunks and not any(line.startswith("- **Pages**") for line in lines):
        lines.append(
            "- Nothing smaller is indexed for this id. Read the markdown at the `url` in "
            "this response instead, and do not treat this reply as the text."
        )

    if chunks:
        step = max(1, -(-len(chunks) // _OVERSIZE_SIGNPOSTS))
        sampled = chunks[::step]
        lines += [
            "",
            (
                f"One line from each of {len(sampled)} chunks sampled across the range, "
                "as landmarks for choosing one:"
            ),
            "",
        ]
        for c in sampled:
            pages_note = (
                f", pages {c['page_first']}–{c['page_last']}"
                if c["page_first"] is not None
                else ""
            )
            lines.append(
                f"- `{ids_module.chunk_id(slug, c['chunk_index'])}` "
                f"({c['chars']:,} chars{pages_note}) — {_landmark(c['text'])}"
            )

    text = "\n".join(lines)
    return FetchResponse(
        id=response_id,
        title=title,
        text=text,
        url=_url(cfg, doc["rel_path"], first_block_id),
        metadata=FetchMeta(
            kind=kind,
            slug=slug,
            pages=[page_first, page_last] if page_first is not None else None,
            block_ids=[first_block_id, last_block_id],
            doc_date=doc["doc_date"],
            text_class=doc["text_class"],
            incomplete_pages=list(doc["incomplete_pages"] or []),
            source_file=doc["source_file"],
            source_url=doc["source_url"],
            citation=_build_citation(
                doc,
                heading_path=heading_path,
                page_first=page_first,
                page_last=page_last,
                first_block_id=first_block_id,
                last_block_id=last_block_id,
            ),
            chars=len(text),
            truncated=True,
        ),
    )


async def _fetch_document(conn: asyncpg.Connection, cfg: Config, doc: asyncpg.Record) -> FetchResponse:
    slug = doc["slug"]
    if doc["chars"] > cfg.fetch_max_chars:
        outline = json.loads(doc["outline"])
        chars_by_index = await _section_chars(conn, slug)
        lines = [
            f"# {doc['title']} — outline only",
            "",
            (
                f"This document is {doc['chars']:,} characters, over FETCH_MAX_CHARS "
                f"({cfg.fetch_max_chars:,}). Fetch the section ids below instead of the "
                "whole document. A section marked **over the cap** is itself larger than "
                "FETCH_MAX_CHARS: fetching it returns a breakdown of smaller ids, not its "
                "text — prefer a section under the cap, or narrow with `search`."
            ),
            "",
        ]

        def _entry_line(entry: dict) -> str:
            heading = entry.get("heading") or "(no heading)"
            pf, pl = entry.get("page_first"), entry.get("page_last")
            pages_note = f" (pages {pf}–{pl})" if pf is not None else ""
            sec_id = ids_module.sec_id(slug, entry["section_index"])
            sec_chars = chars_by_index.get(entry["section_index"], 0)
            size_note = f" — {sec_chars:,} chars"
            if sec_chars > cfg.fetch_max_chars:
                size_note += ", **over the cap**"
            return f"- {sec_id}: {heading}{pages_note}{size_note}"

        # The outline is not bounded either: a 1,500-section manual's full listing can
        # itself approach the cap. Drop to shallower headings until it fits, and only then
        # cut the list — a caller given the top two levels can still `get_outline` for the
        # rest, which a silently clipped listing would not tell them.
        entries = list(outline)
        text = "\n".join(lines + [_entry_line(e) for e in entries])
        for max_level in (6, 5, 4, 3, 2, 1):
            if len(text) <= cfg.fetch_max_chars:
                break
            deeper = [e for e in entries if e["level"] <= max_level]
            if not deeper or len(deeper) == len(entries):
                continue
            entries = deeper
            text = "\n".join(
                lines
                + [
                    f"Headings deeper than level {max_level} are left out; "
                    f"`get_outline` on `{ids_module.doc_id(slug)}` lists every section.",
                    "",
                ]
                + [_entry_line(e) for e in entries]
            )
        if len(text) > cfg.fetch_max_chars:
            # Cut whole entries off the end, never mid-line, and never all of them: a
            # refusal that names no section id at all leaves the caller with nowhere to go.
            cut = (
                f"\n\n(Listing cut here; `get_outline` on `{ids_module.doc_id(slug)}` "
                "lists every section, and `search` finds one directly.)"
            )
            head = "\n".join(lines)
            entry_lines = [_entry_line(e) for e in entries]
            kept = entry_lines[:1]
            budget = cfg.fetch_max_chars - len(head) - len(cut)
            for line in entry_lines[1:]:
                if sum(len(k) + 1 for k in kept) + len(line) + 1 > budget:
                    break
                kept.append(line)
            text = "\n".join([head, *kept]) + cut

        first_block = await conn.fetchval(
            "SELECT block_id FROM blocks WHERE slug = $1 ORDER BY ordinal LIMIT 1", slug
        )
        last_block = await conn.fetchval(
            "SELECT block_id FROM blocks WHERE slug = $1 ORDER BY ordinal DESC LIMIT 1", slug
        )
        page_row = await conn.fetchrow(
            "SELECT min(page) AS lo, max(page) AS hi FROM blocks WHERE slug = $1", slug
        )
        pages = [page_row["lo"], page_row["hi"]] if page_row and page_row["lo"] is not None else None

        return FetchResponse(
            id=ids_module.doc_id(slug),
            title=doc["title"],
            text=text,
            url=_url(cfg, doc["rel_path"], first_block),
            metadata=FetchMeta(
                kind="document",
                slug=slug,
                pages=pages,
                block_ids=[first_block, last_block] if first_block else None,
                doc_date=doc["doc_date"],
                text_class=doc["text_class"],
                incomplete_pages=list(doc["incomplete_pages"] or []),
                source_file=doc["source_file"],
                source_url=doc["source_url"],
                citation=_build_citation(
                    doc,
                    heading_path=doc["title"],
                    page_first=pages[0] if pages else None,
                    page_last=pages[1] if pages else None,
                    first_block_id=first_block,
                    last_block_id=last_block,
                ),
                chars=len(text),
                truncated=True,
            ),
        )

    blocks = await conn.fetch(
        "SELECT ordinal, block_id, page, text FROM blocks WHERE slug = $1 ORDER BY ordinal", slug
    )
    text = "\n\n".join(b["text"] for b in blocks)
    first_block = blocks[0]["block_id"] if blocks else None
    last_block = blocks[-1]["block_id"] if blocks else None
    pages_present = [b["page"] for b in blocks if b["page"] is not None]
    pages = [min(pages_present), max(pages_present)] if pages_present else None

    return FetchResponse(
        id=ids_module.doc_id(slug),
        title=doc["title"],
        text=text,
        url=_url(cfg, doc["rel_path"], first_block),
        metadata=FetchMeta(
            kind="document",
            slug=slug,
            pages=pages,
            block_ids=[first_block, last_block] if first_block else None,
            doc_date=doc["doc_date"],
            text_class=doc["text_class"],
            incomplete_pages=list(doc["incomplete_pages"] or []),
            source_file=doc["source_file"],
            source_url=doc["source_url"],
            citation=_build_citation(
                doc,
                heading_path=doc["title"],
                page_first=pages[0] if pages else None,
                page_last=pages[1] if pages else None,
                first_block_id=first_block,
                last_block_id=last_block,
            ),
            chars=len(text),
            truncated=False,
        ),
    )


async def _fetch_section_range(
    conn: asyncpg.Connection,
    cfg: Config,
    doc: asyncpg.Record,
    lo_idx: int,
    hi_idx: int,
    *,
    requested_idx: int,
    kind: str = "section",
) -> FetchResponse:
    """Fetch sections `lo_idx..hi_idx` inclusive as one reading unit (brief §6.5.2:
    `get_section(neighbours=n)`; also used for a plain `sec:`/`chunk:` fetch with
    `lo_idx == hi_idx == requested_idx`).

    Block ordinals are contiguous across sections in document order, and each section's
    own heading block is already the first block of that section — so concatenating every
    block in the overall ordinal range naturally gives "each [section] with its own
    heading line" (brief §6.5.2) without any synthetic heading construction.
    """
    slug = doc["slug"]
    sections = await conn.fetch(
        "SELECT * FROM sections WHERE slug = $1 AND section_index BETWEEN $2 AND $3 "
        "ORDER BY section_index",
        slug,
        lo_idx,
        hi_idx,
    )
    if not sections:
        raise ValueError(f"unknown id: {ids_module.sec_id(slug, requested_idx)}")

    center = next((s for s in sections if s["section_index"] == requested_idx), sections[0])

    block_first = min(s["block_first"] for s in sections)
    block_last = max(s["block_last"] for s in sections)
    blocks = await conn.fetch(
        "SELECT ordinal, block_id, page, text FROM blocks WHERE slug = $1 AND ordinal "
        "BETWEEN $2 AND $3 ORDER BY ordinal",
        slug,
        block_first,
        block_last,
    )
    text = "\n\n".join(b["text"] for b in blocks)

    pages_first = [s["page_first"] for s in sections if s["page_first"] is not None]
    pages_last = [s["page_last"] for s in sections if s["page_last"] is not None]
    page_first = min(pages_first) if pages_first else None
    page_last = max(pages_last) if pages_last else None

    first_block_id = blocks[0]["block_id"] if blocks else None
    last_block_id = blocks[-1]["block_id"] if blocks else None

    title = _section_title(doc["title"], center["heading_path"])
    response_id = ids_module.sec_id(slug, center["section_index"])

    if len(text) > cfg.fetch_max_chars and blocks:
        extra_notes: tuple[str, ...] = ()
        if len(sections) > 1:
            extra_notes = (
                f"**Fewer neighbours** — this range is {len(sections)} sections; "
                f"`get_section` on `{response_id}` with a smaller `neighbours` (or `fetch` "
                "on it, which is `neighbours=0`) returns less.",
            )
        return await _oversize_response(
            conn,
            cfg,
            doc,
            response_id=response_id,
            title=title,
            kind=kind,
            heading_path=center["heading_path"],
            blocks=list(blocks),
            chars=len(text),
            page_first=page_first,
            page_last=page_last,
            extra_notes=extra_notes,
        )

    return FetchResponse(
        id=response_id,
        title=title,
        text=text,
        url=_url(cfg, doc["rel_path"], first_block_id),
        metadata=FetchMeta(
            kind=kind,
            slug=slug,
            pages=[page_first, page_last] if page_first is not None else None,
            block_ids=[first_block_id, last_block_id] if first_block_id else None,
            doc_date=doc["doc_date"],
            text_class=doc["text_class"],
            incomplete_pages=list(doc["incomplete_pages"] or []),
            source_file=doc["source_file"],
            source_url=doc["source_url"],
            citation=_build_citation(
                doc,
                heading_path=center["heading_path"],
                page_first=page_first,
                page_last=page_last,
                first_block_id=first_block_id,
                last_block_id=last_block_id,
            ),
            chars=len(text),
            truncated=False,
        ),
    )


async def _fetch_page(conn: asyncpg.Connection, cfg: Config, doc: asyncpg.Record, page: int, raw_id: str) -> FetchResponse:
    slug = doc["slug"]
    blocks = await conn.fetch(
        "SELECT ordinal, block_id, page, text FROM blocks WHERE slug = $1 AND page = $2 "
        "ORDER BY ordinal",
        slug,
        page,
    )
    if not blocks:
        raise ValueError(f"unknown id: {raw_id}")

    text = "\n\n".join(b["text"] for b in blocks)
    first_block_id = blocks[0]["block_id"]
    last_block_id = blocks[-1]["block_id"]
    title = f"{doc['title']} — page {page}"

    if len(text) > cfg.fetch_max_chars:
        return await _oversize_response(
            conn,
            cfg,
            doc,
            response_id=ids_module.page_id(slug, page),
            title=title,
            kind="page",
            heading_path=f"page {page}",
            blocks=list(blocks),
            chars=len(text),
            page_first=page,
            page_last=page,
        )

    return FetchResponse(
        id=ids_module.page_id(slug, page),
        title=title,
        text=text,
        url=_url(cfg, doc["rel_path"], first_block_id),
        metadata=FetchMeta(
            kind="page",
            slug=slug,
            pages=[page, page],
            block_ids=[first_block_id, last_block_id],
            doc_date=doc["doc_date"],
            text_class=doc["text_class"],
            incomplete_pages=list(doc["incomplete_pages"] or []),
            source_file=doc["source_file"],
            source_url=doc["source_url"],
            citation=_build_citation(
                doc,
                heading_path=f"page {page}",
                page_first=page,
                page_last=page,
                first_block_id=first_block_id,
                last_block_id=last_block_id,
            ),
            chars=len(text),
            truncated=False,
        ),
    )


async def _fetch_chunk(
    conn: asyncpg.Connection, cfg: Config, doc: asyncpg.Record, chunk_index: int, raw_id: str
) -> FetchResponse:
    """One chunk, and nothing else.

    A `chunk:` fetch used to widen to the whole enclosing section, on the grounds that a
    section is the readable unit. It cannot: a section is unbounded (see
    `_oversize_response`), so an over-cap section's breakdown — whose only sub-unit to
    offer is the chunk — would hand back ids that widen straight to the breakdown again.
    A chunk is what the index bounds (`CHUNK_MAX`, bar a single table row larger than it),
    so it is what a caller can narrow *to*. `get_section` on the section id is still there
    for the wider read.
    """
    slug = doc["slug"]
    chunk = await conn.fetchrow(
        "SELECT chunk_index, section_index, block_first, block_last, page_first, page_last, "
        "text FROM chunks WHERE slug = $1 AND chunk_index = $2",
        slug,
        chunk_index,
    )
    if chunk is None:
        raise ValueError(f"unknown id: {raw_id}")

    section = await conn.fetchrow(
        "SELECT heading_path FROM sections WHERE slug = $1 AND section_index = $2",
        slug,
        chunk["section_index"],
    )
    heading_path = section["heading_path"] if section else doc["title"]
    title = _section_title(doc["title"], heading_path)

    blocks = await conn.fetch(
        "SELECT ordinal, block_id, page, text FROM blocks WHERE slug = $1 AND ordinal "
        "BETWEEN $2 AND $3 ORDER BY ordinal",
        slug,
        chunk["block_first"],
        chunk["block_last"],
    )
    first_block_id = blocks[0]["block_id"] if blocks else None
    last_block_id = blocks[-1]["block_id"] if blocks else None
    text = chunk["text"]

    if len(text) > cfg.fetch_max_chars and blocks:
        # A chunk over the cap is one indivisible table row (brief §6.7's exception); there
        # is no smaller id to offer, and saying so is better than sending the text.
        return await _oversize_response(
            conn,
            cfg,
            doc,
            response_id=ids_module.chunk_id(slug, chunk_index),
            title=title,
            kind="chunk",
            heading_path=heading_path,
            blocks=list(blocks),
            chars=len(text),
            page_first=chunk["page_first"],
            page_last=chunk["page_last"],
            narrow_to_chunks=False,
        )

    return FetchResponse(
        id=ids_module.chunk_id(slug, chunk_index),
        title=title,
        text=text,
        url=_url(cfg, doc["rel_path"], first_block_id),
        metadata=FetchMeta(
            kind="chunk",
            slug=slug,
            pages=(
                [chunk["page_first"], chunk["page_last"]]
                if chunk["page_first"] is not None
                else None
            ),
            block_ids=[first_block_id, last_block_id] if first_block_id else None,
            doc_date=doc["doc_date"],
            text_class=doc["text_class"],
            incomplete_pages=list(doc["incomplete_pages"] or []),
            source_file=doc["source_file"],
            source_url=doc["source_url"],
            citation=_build_citation(
                doc,
                heading_path=heading_path,
                page_first=chunk["page_first"],
                page_last=chunk["page_last"],
                first_block_id=first_block_id,
                last_block_id=last_block_id,
            ),
            chars=len(text),
            truncated=False,
        ),
    )


async def _fetch_any(conn: asyncpg.Connection, cfg: Config, raw_id: str) -> FetchResponse:
    try:
        kind, slug, n = ids_module.parse_id(raw_id)
    except ValueError:
        raise ValueError(f"unknown id: {raw_id}")

    doc = await _require_document(conn, slug, raw_id)

    if kind == "doc":
        return await _fetch_document(conn, cfg, doc)
    if kind == "sec":
        assert n is not None
        await _require_section(conn, slug, n, raw_id)
        return await _fetch_section_range(conn, cfg, doc, n, n, requested_idx=n)
    if kind == "page":
        assert n is not None
        return await _fetch_page(conn, cfg, doc, n, raw_id)
    if kind == "chunk":
        assert n is not None
        return await _fetch_chunk(conn, cfg, doc, n, raw_id)

    raise ValueError(f"unknown id: {raw_id}")  # pragma: no cover — parse_id covers every kind


async def _get_section_any(
    conn: asyncpg.Connection, cfg: Config, raw_id: str, neighbours: int
) -> FetchResponse:
    try:
        kind, slug, n = ids_module.parse_id(raw_id)
    except ValueError:
        raise ValueError(f"unknown id: {raw_id}")
    if kind not in ("sec", "page") or n is None:
        raise ValueError(f"unknown id: {raw_id}")

    doc = await _require_document(conn, slug, raw_id)

    if kind == "sec":
        await _require_section(conn, slug, n, raw_id)
        center_idx = n
    else:
        row = await conn.fetchrow(
            "SELECT section_index FROM sections WHERE slug = $1 AND page_first IS NOT NULL "
            "AND page_first <= $2 AND page_last >= $2 ORDER BY section_index LIMIT 1",
            slug,
            n,
        )
        if row is None:
            row = await conn.fetchrow(
                "SELECT section_index FROM sections WHERE slug = $1 AND page_first IS NOT NULL "
                "ORDER BY abs(page_first - $2) LIMIT 1",
                slug,
                n,
            )
        if row is None:
            raise ValueError(f"unknown id: {raw_id}")
        center_idx = row["section_index"]

    max_idx = await conn.fetchval(
        "SELECT max(section_index) FROM sections WHERE slug = $1", slug
    )
    lo = max(0, center_idx - neighbours)
    hi = min(max_idx, center_idx + neighbours)
    return await _fetch_section_range(conn, cfg, doc, lo, hi, requested_idx=center_idx)


# --------------------------------------------------------------------------------------
# Server construction.
# --------------------------------------------------------------------------------------


def _transport_security(cfg: Config) -> TransportSecuritySettings:
    """Brief §6.5.5: DNS-rebinding protection, explicit because binding `0.0.0.0` (inside
    the container, S4) disables FastMCP's own auto-enable (which only fires for a literal
    `127.0.0.1`/`localhost`/`::1` host — brief §13's "Binding 0.0.0.0 disables FastMCP's
    DNS-rebinding protection" warning). `allowed_hosts` is the brief's list verbatim, plus
    the bare `KB_PUBLIC_HOST` (no port) since Caddy terminates TLS on 443 and a bare Host
    header with no port is what a browser/client sends for the default HTTPS port.
    `allowed_origins` is left to this implementation (brief §6.5.5 says "[...]"): every
    origin a legitimate client could present — the public HTTPS origin at the default and
    an explicit port, and loopback for the stdio-adjacent dev path — nothing else.
    """
    host = cfg.kb_public_host
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, f"{host}:*", "localhost:*", "127.0.0.1:*"],
        allowed_origins=[
            f"https://{host}",
            f"https://{host}:*",
            "http://localhost:*",
            "https://localhost:*",
            "http://127.0.0.1:*",
            "https://127.0.0.1:*",
        ],
    )


def build_server(cfg: Config) -> "FastMCP[ServerContext]":
    mcp: "FastMCP[ServerContext]" = FastMCP(
        "dgx-kb",
        instructions=INSTRUCTIONS,
        lifespan=_make_lifespan(cfg),
        host=cfg.kb_bind_host,
        port=cfg.kb_bind_port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=_transport_security(cfg),
    )

    @mcp.tool(annotations=RO)
    async def search(
        query: str,
        k: int = 8,
        slug: Optional[str] = None,
        text_class: Optional[str] = None,
        *,
        ctx: Context,
    ) -> SearchResponse:
        """Locate candidate sections for a query. Returns section ids for `fetch`."""
        sc: ServerContext = ctx.request_context.lifespan_context
        with _CallTimer("search", {"query": query, "k": k, "slug": slug, "text_class": text_class}) as timer:
            k = search_module.clamp_k(k)
            filters: dict[str, Any] = {}
            if slug:
                filters["slug"] = slug
            if text_class:
                filters["text_class"] = text_class

            async with sc.pool.acquire() as conn:
                hits = await search_module.search(
                    conn,
                    query,
                    k=k,
                    filters=filters or None,
                    embed_query_fn=sc.embed_query_fn,
                    leg="fused",
                )
                results: list[SearchResult] = []
                for h in hits:
                    first_block_id = await conn.fetchval(
                        "SELECT block_id FROM blocks WHERE slug = $1 AND ordinal = $2",
                        h.slug,
                        h.block_first,
                    )
                    rel_path = await conn.fetchval(
                        "SELECT rel_path FROM documents WHERE slug = $1", h.slug
                    )
                    results.append(
                        SearchResult(
                            id=ids_module.sec_id(h.slug, h.section_index),
                            title=_section_title(h.title, h.heading_path),
                            url=_url(cfg, rel_path, first_block_id),
                            snippet=h.snippet,
                            pages=[h.page_first, h.page_last] if h.page_first is not None else None,
                            doc_date=h.doc_date,
                            text_class=h.text_class,
                            score=h.score,
                        )
                    )
            response = SearchResponse(results=results)
            timer.result_ids = [r["id"] for r in results]
            return response

    @mcp.tool(annotations=RO)
    async def fetch(id: str, *, ctx: Context) -> FetchResponse:
        """Fetch the whole section, page, chunk, or document named by `id` (from `search`,
        `list_documents`, or `get_outline`). Anything over FETCH_MAX_CHARS comes back as
        `truncated` with the smaller ids that cover it instead of its text."""
        sc: ServerContext = ctx.request_context.lifespan_context
        with _CallTimer("fetch", {"id": id}) as timer:
            async with sc.pool.acquire() as conn:
                response = await _fetch_any(conn, cfg, id)
            timer.result_ids = [response["id"]]
            return response

    @mcp.tool(annotations=RO)
    async def list_documents(
        text_class: Optional[str] = None,
        title_contains: Optional[str] = None,
        *,
        ctx: Context,
    ) -> ListDocumentsResponse:
        """List every indexed document, sorted by title."""
        sc: ServerContext = ctx.request_context.lifespan_context
        with _CallTimer(
            "list_documents", {"text_class": text_class, "title_contains": title_contains}
        ) as timer:
            async with sc.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT d.slug, d.title, d.doc_date, d.text_class, d.page_count, d.chars,
                           d.summary,
                           (SELECT count(*) FROM sections s WHERE s.slug = d.slug) AS sections
                    FROM documents d
                    WHERE ($1::text IS NULL OR d.text_class = $1)
                      AND ($2::text IS NULL OR d.title ILIKE '%' || $2 || '%')
                    ORDER BY d.title
                    """,
                    text_class,
                    title_contains,
                )
            documents = [
                DocumentEntry(
                    id=ids_module.doc_id(r["slug"]),
                    title=r["title"],
                    doc_date=r["doc_date"],
                    text_class=r["text_class"],
                    page_count=r["page_count"],
                    sections=r["sections"],
                    chars=r["chars"],
                    summary=r["summary"],
                )
                for r in rows
            ]
            response = ListDocumentsResponse(documents=documents)
            timer.result_ids = [d["id"] for d in documents]
            return response

    @mcp.tool(annotations=RO)
    async def get_outline(id: str, *, ctx: Context) -> OutlineResponse:
        """The section-by-section outline of one document (`doc:` id)."""
        sc: ServerContext = ctx.request_context.lifespan_context
        with _CallTimer("get_outline", {"id": id}) as timer:
            try:
                kind, slug, _ = ids_module.parse_id(id)
            except ValueError:
                raise ValueError(f"unknown id: {id}")
            if kind != "doc":
                raise ValueError(f"unknown id: {id}")

            async with sc.pool.acquire() as conn:
                doc = await _require_document(conn, slug, id)
                chars_by_index = await _section_chars(conn, slug)
            outline = json.loads(doc["outline"])
            sections = [
                OutlineSection(
                    id=ids_module.sec_id(slug, e["section_index"]),
                    level=e["level"],
                    heading=e["heading"],
                    pages=(
                        [e["page_first"], e["page_last"]] if e["page_first"] is not None else None
                    ),
                    chars=chars_by_index.get(e["section_index"], 0),
                )
                for e in outline
            ]
            response = OutlineResponse(
                id=ids_module.doc_id(slug),
                title=doc["title"],
                doc_date=doc["doc_date"],
                text_class=doc["text_class"],
                incomplete_pages=list(doc["incomplete_pages"] or []),
                sections=sections,
            )
            timer.result_ids = [response["id"]]
            return response

    @mcp.tool(annotations=RO)
    async def get_section(id: str, neighbours: int = 0, *, ctx: Context) -> FetchResponse:
        """Fetch a section (or the section enclosing a page), with `neighbours` sections
        before and after concatenated in."""
        sc: ServerContext = ctx.request_context.lifespan_context
        with _CallTimer("get_section", {"id": id, "neighbours": neighbours}) as timer:
            async with sc.pool.acquire() as conn:
                response = await _get_section_any(conn, cfg, id, neighbours)
            timer.result_ids = [response["id"]]
            return response

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:  # pragma: no cover — reachable over HTTP only (S4)
        """Unauthenticated health check (brief §6.5.4). Only reachable once `kb serve
        --transport http` (S4) is running; registering it now costs nothing under stdio,
        since a `custom_route` is only ever served by `streamable_http_app()`."""
        try:
            conn = await asyncpg.connect(cfg.require_database_url())
            try:
                documents = await conn.fetchval("SELECT count(*) FROM documents")
            finally:
                await conn.close()
        except Exception:
            documents = None
        return JSONResponse({"ok": True, "documents": documents, "embed_model": cfg.embed_model})

    return mcp


def build_http_app(cfg: Config) -> ASGIApp:
    """The ASGI app for `kb serve --transport http` (brief §9 S4): `build_server(cfg)`'s
    `streamable_http_app()` — a Starlette app whose own `lifespan` enters
    `mcp.session_manager.run()` (see `auth.py`'s module docstring) — wrapped in the bearer
    middleware (brief §6.5.6). `cli.py` runs the result with uvicorn; it must not be run
    any other way, or `/mcp*` is served with no auth.
    """
    mcp = build_server(cfg)
    return auth_module.BearerMiddleware(mcp.streamable_http_app(), cfg.kb_tokens)
