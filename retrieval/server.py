"""The MCP server (brief §6.5): five read-only tools over the indexed `kb/`.

S3 scope: stdio transport only. `build_server(cfg)` returns a configured `FastMCP`; `kb
serve --transport stdio` (`cli.py`) calls `mcp.run(transport="stdio")`. HTTP transport,
bearer auth and transport-security settings are S4 and not implemented here.

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
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

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
`doc_date: UNCONFIRMED` means no reliable date was found; do not invent one.\
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
    kind: str  # "section" | "page" | "document"
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


async def _fetch_document(conn: asyncpg.Connection, cfg: Config, doc: asyncpg.Record) -> FetchResponse:
    slug = doc["slug"]
    if doc["chars"] > cfg.fetch_max_chars:
        outline = json.loads(doc["outline"])
        lines = [
            f"# {doc['title']} — outline only",
            "",
            (
                f"This document is {doc['chars']} characters, over FETCH_MAX_CHARS "
                f"({cfg.fetch_max_chars}). Fetch the section ids below instead of the "
                "whole document."
            ),
            "",
        ]
        for entry in outline:
            heading = entry.get("heading") or "(no heading)"
            pf, pl = entry.get("page_first"), entry.get("page_last")
            pages_note = f" (pages {pf}–{pl})" if pf is not None else ""
            sec_id = ids_module.sec_id(slug, entry["section_index"])
            lines.append(f"- {sec_id}: {heading}{pages_note}")
        text = "\n".join(lines)

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

    return FetchResponse(
        id=ids_module.sec_id(slug, center["section_index"]),
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
        chunk = await conn.fetchrow(
            "SELECT section_index FROM chunks WHERE slug = $1 AND chunk_index = $2", slug, n
        )
        if chunk is None:
            raise ValueError(f"unknown id: {raw_id}")
        sec_idx = chunk["section_index"]
        return await _fetch_section_range(conn, cfg, doc, sec_idx, sec_idx, requested_idx=sec_idx)

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


def build_server(cfg: Config) -> "FastMCP[ServerContext]":
    mcp: "FastMCP[ServerContext]" = FastMCP(
        "dgx-kb",
        instructions=INSTRUCTIONS,
        lifespan=_make_lifespan(cfg),
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
        """Fetch the whole section, page, or document named by `id` (from `search`,
        `list_documents`, or `get_outline`)."""
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
                chars_rows = await conn.fetch(
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
            chars_by_index = {r["section_index"]: r["chars"] for r in chars_rows}
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
