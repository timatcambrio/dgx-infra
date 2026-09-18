"""Retrieval (brief §6.4): lexical + vector legs over `chunks`, fused with RRF, collapsed
to sections.

`search()` is the primary entry point (fused by default). `search_legs()` runs any subset
of `{"lexical", "vector", "fused"}` in one round trip against the same SQL, so `kb search
--leg ...` and `kb eval` (which needs all three per case) never duplicate the queries.

Callers own the connection (a single `asyncpg.Connection` with the pgvector codec
registered — see `retrieval.db.create_pool`'s `init=` callback) and the embedder: this
module never opens a connection or talks to ollama itself, so it is trivial for the S3 MCP
server to reuse against its pool.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Hashable, Iterable, Optional

import asyncpg

#: Top-N per leg before fusion (brief §6.4).
LEXICAL_LIMIT = 20
VECTOR_LIMIT = 20
#: RRF constant (brief §6.4). Not tunable to chase the eval (brief §6.7, hard rule 7).
RRF_K = 60
SNIPPET_CHARS = 300
K_MIN = 1
K_MAX = 25

ChunkId = tuple[str, int]  # (slug, chunk_index)
SectionKey = tuple[str, int]  # (slug, section_index)

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class SectionHit:
    slug: str
    section_index: int
    heading_path: str
    page_first: Optional[int]
    page_last: Optional[int]
    block_first: int
    block_last: int
    title: str
    doc_date: str
    text_class: str
    incomplete_pages: list[int]
    snippet: str
    score: float
    best_chunk_index: int
    #: 1-based rank of the winning chunk in each leg, or `None` when it did not appear in
    #: that leg at all (brief §6.4: "keep them, the eval needs them").
    lexical_rank: Optional[int]
    vector_rank: Optional[int]


def clamp_k(k: int) -> int:
    """`k` clamped to 1..25 (brief §6.4)."""
    return max(K_MIN, min(K_MAX, k))


def collapse_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def make_snippet(text: str) -> str:
    """First 300 chars of `text`, whitespace collapsed (brief §6.4)."""
    return collapse_whitespace(text)[:SNIPPET_CHARS]


def fuse_rrf(legs: Iterable[list[Hashable]], k: int = RRF_K) -> dict[Hashable, float]:
    """Reciprocal rank fusion (brief §6.4): each item in a ranked list (1-based rank =
    position + 1) gets `1/(k+rank)`, summed across every list it appears in.

    Pure function over already-ranked id lists — no database access — so RRF math is
    unit-testable on a hand-built pair of lists without a connection.
    """
    scores: dict[Hashable, float] = defaultdict(float)
    for leg in legs:
        for rank, item in enumerate(leg, start=1):
            scores[item] += 1.0 / (k + rank)
    return dict(scores)


def _filter_clause(
    filters: Optional[dict], alias: str, start: int, need_text_class: bool
) -> tuple[list[str], list]:
    """SQL `WHERE` fragments + params for `filters` (brief §6.4: slug, text_class,
    page_min/page_max applied to `chunks.page_first`), starting at parameter `$start`.
    """
    clauses: list[str] = []
    params: list = []
    idx = start
    filters = filters or {}
    if filters.get("slug"):
        clauses.append(f"{alias}.slug = ${idx}")
        params.append(filters["slug"])
        idx += 1
    if filters.get("page_min") is not None:
        clauses.append(f"{alias}.page_first >= ${idx}")
        params.append(filters["page_min"])
        idx += 1
    if filters.get("page_max") is not None:
        clauses.append(f"{alias}.page_first <= ${idx}")
        params.append(filters["page_max"])
        idx += 1
    if need_text_class and filters.get("text_class"):
        clauses.append(f"d.text_class = ${idx}")
        params.append(filters["text_class"])
        idx += 1
    return clauses, params


async def _lexical_chunks(
    conn: asyncpg.Connection, query: str, filters: Optional[dict]
) -> list[asyncpg.Record]:
    """Top-20 chunks by `ts_rank_cd` (brief §6.4). Skips the leg (returns `[]`) when
    `websearch_to_tsquery` parses to an empty query, e.g. an all-stop-words query — matching
    nothing would look identical, but checking first means we never rely on that
    coincidence (brief §13: "matches nothing; skip the leg rather than fusing an empty
    list")."""
    tsq_text = await conn.fetchval("SELECT websearch_to_tsquery('english', $1)::text", query)
    if not tsq_text:
        return []

    need_tc = bool(filters and filters.get("text_class"))
    join = "JOIN documents d ON d.slug = c.slug" if need_tc else ""
    extra_clauses, extra_params = _filter_clause(filters, "c", 2, need_tc)
    where = " AND ".join(["c.tsv @@ q", *extra_clauses])
    sql = f"""
        SELECT c.slug, c.chunk_index, c.section_index, ts_rank_cd(c.tsv, q) AS score
        FROM chunks c, websearch_to_tsquery('english', $1) q
        {join}
        WHERE {where}
        ORDER BY score DESC
        LIMIT {LEXICAL_LIMIT}
    """
    return await conn.fetch(sql, query, *extra_params)


async def _vector_chunks(
    conn: asyncpg.Connection, vector: list[float], filters: Optional[dict]
) -> list[asyncpg.Record]:
    """Top-20 chunks by cosine similarity (brief §6.4). `$1` is the query embedding, sent
    through the pgvector asyncpg codec registered on `conn`."""
    need_tc = bool(filters and filters.get("text_class"))
    join = "JOIN documents d ON d.slug = c.slug" if need_tc else ""
    extra_clauses, extra_params = _filter_clause(filters, "c", 2, need_tc)
    where = f"WHERE {' AND '.join(extra_clauses)}" if extra_clauses else ""
    sql = f"""
        SELECT c.slug, c.chunk_index, c.section_index, 1 - (c.embedding <=> $1) AS score
        FROM chunks c
        {join}
        {where}
        ORDER BY c.embedding <=> $1
        LIMIT {VECTOR_LIMIT}
    """
    return await conn.fetch(sql, vector, *extra_params)


async def _load_section_meta(
    conn: asyncpg.Connection, keys: list[SectionKey]
) -> dict[SectionKey, asyncpg.Record]:
    if not keys:
        return {}
    slugs = [k[0] for k in keys]
    indices = [k[1] for k in keys]
    rows = await conn.fetch(
        """
        SELECT s.slug, s.section_index, s.heading_path, s.page_first, s.page_last,
               s.block_first, s.block_last,
               d.title, d.doc_date, d.text_class, d.incomplete_pages
        FROM sections s
        JOIN documents d ON d.slug = s.slug
        WHERE (s.slug, s.section_index) IN (SELECT * FROM unnest($1::text[], $2::int[]))
        """,
        slugs,
        indices,
    )
    return {(r["slug"], r["section_index"]): r for r in rows}


async def _load_chunk_texts(
    conn: asyncpg.Connection, keys: list[ChunkId]
) -> dict[ChunkId, str]:
    if not keys:
        return {}
    slugs = [k[0] for k in keys]
    indices = [k[1] for k in keys]
    rows = await conn.fetch(
        """
        SELECT slug, chunk_index, text FROM chunks
        WHERE (slug, chunk_index) IN (SELECT * FROM unnest($1::text[], $2::int[]))
        """,
        slugs,
        indices,
    )
    return {(r["slug"], r["chunk_index"]): r["text"] for r in rows}


async def _collapse_to_sections(
    conn: asyncpg.Connection,
    ordered_chunk_ids: list[ChunkId],
    score_by_chunk: dict[ChunkId, float],
    section_of: dict[ChunkId, int],
    lexical_rank: dict[ChunkId, int],
    vector_rank: dict[ChunkId, int],
    k: int,
) -> list[SectionHit]:
    """Group `ordered_chunk_ids` (already sorted best-first for this leg) by
    `(slug, section_index)`, keep the first (= best) chunk per section as the snippet
    source, sort sections by score desc with a deterministic `(slug, section_index)`
    tie-break, and take `k` (brief §6.4)."""
    best_for_section: dict[SectionKey, ChunkId] = {}
    for cid in ordered_chunk_ids:
        sec_key = (cid[0], section_of[cid])
        best_for_section.setdefault(sec_key, cid)

    section_keys = sorted(
        best_for_section.keys(),
        key=lambda sk: (-score_by_chunk[best_for_section[sk]], sk[0], sk[1]),
    )[: clamp_k(k)]
    if not section_keys:
        return []

    meta = await _load_section_meta(conn, section_keys)
    chosen_chunks = [best_for_section[sk] for sk in section_keys]
    texts = await _load_chunk_texts(conn, chosen_chunks)

    hits: list[SectionHit] = []
    for sk in section_keys:
        cid = best_for_section[sk]
        m = meta[sk]
        hits.append(
            SectionHit(
                slug=sk[0],
                section_index=sk[1],
                heading_path=m["heading_path"],
                page_first=m["page_first"],
                page_last=m["page_last"],
                block_first=m["block_first"],
                block_last=m["block_last"],
                title=m["title"],
                doc_date=m["doc_date"],
                text_class=m["text_class"],
                incomplete_pages=list(m["incomplete_pages"] or []),
                snippet=make_snippet(texts[cid]),
                score=score_by_chunk[cid],
                best_chunk_index=cid[1],
                lexical_rank=lexical_rank.get(cid),
                vector_rank=vector_rank.get(cid),
            )
        )
    return hits


async def search_legs(
    conn: asyncpg.Connection,
    query: str,
    *,
    k: int = 8,
    filters: Optional[dict] = None,
    embed_query_fn: Callable[[str], list[float]],
    legs: tuple[str, ...] = ("lexical", "vector", "fused"),
) -> dict[str, list[SectionHit]]:
    """Run the requested legs against the same underlying SQL and return
    `{leg_name: [SectionHit, ...]}`. `embed_query_fn` is called at most once, and only when
    a vector or fused leg is requested."""
    need_lexical = "lexical" in legs or "fused" in legs
    need_vector = "vector" in legs or "fused" in legs

    lexical_rows = list(await _lexical_chunks(conn, query, filters)) if need_lexical else []
    vector_rows: list[asyncpg.Record] = []
    if need_vector:
        vector = embed_query_fn(query)
        vector_rows = list(await _vector_chunks(conn, vector, filters))

    lexical_ids = [(r["slug"], r["chunk_index"]) for r in lexical_rows]
    vector_ids = [(r["slug"], r["chunk_index"]) for r in vector_rows]
    lexical_rank = {cid: i + 1 for i, cid in enumerate(lexical_ids)}
    vector_rank = {cid: i + 1 for i, cid in enumerate(vector_ids)}

    section_of: dict[ChunkId, int] = {}
    for r in lexical_rows:
        section_of[(r["slug"], r["chunk_index"])] = r["section_index"]
    for r in vector_rows:
        section_of[(r["slug"], r["chunk_index"])] = r["section_index"]

    lexical_score = {cid: r["score"] for cid, r in zip(lexical_ids, lexical_rows)}
    vector_score = {cid: r["score"] for cid, r in zip(vector_ids, vector_rows)}

    out: dict[str, list[SectionHit]] = {}
    if "lexical" in legs:
        out["lexical"] = await _collapse_to_sections(
            conn, lexical_ids, lexical_score, section_of, lexical_rank, {}, k
        )
    if "vector" in legs:
        out["vector"] = await _collapse_to_sections(
            conn, vector_ids, vector_score, section_of, {}, vector_rank, k
        )
    if "fused" in legs:
        fused_score = fuse_rrf([lexical_ids, vector_ids])
        fused_order = sorted(
            fused_score, key=lambda cid: (-fused_score[cid], cid[0], cid[1])
        )
        out["fused"] = await _collapse_to_sections(
            conn, fused_order, fused_score, section_of, lexical_rank, vector_rank, k
        )
    return out


async def search(
    conn: asyncpg.Connection,
    query: str,
    *,
    k: int = 8,
    filters: Optional[dict] = None,
    embed_query_fn: Callable[[str], list[float]],
    leg: str = "fused",
) -> list[SectionHit]:
    """`search(query, k=8, filters=None) -> list[SectionHit]` (brief §6.4). `leg` selects
    `"lexical"`, `"vector"`, or the default `"fused"`, sharing `search_legs`'s SQL."""
    if leg not in ("lexical", "vector", "fused"):
        raise ValueError(f"leg must be lexical|vector|fused, got {leg!r}")
    results = await search_legs(
        conn, query, k=k, filters=filters, embed_query_fn=embed_query_fn, legs=(leg,)
    )
    return results[leg]
