"""The citation string (brief §6.5.2), and the small lookups it needs from `documents`/
`blocks`. Kept separate from `search.py` so `kb search`'s CLI output and the S3 MCP
server's `fetch`/`get_section` share one implementation instead of two.

Citation format: `"{title}, {heading_path} (source: {source_file}, pages {a}-{b}, dated
{doc_date}; blocks {first_block_id}...{last_block_id})"`, omitting the pages clause when
pages are null. `source_file` and the block ids are looked up, never guessed.
"""

from __future__ import annotations

from typing import Optional

import asyncpg


def build_citation(
    *,
    title: str,
    heading_path: str,
    source_file: Optional[str],
    page_first: Optional[int],
    page_last: Optional[int],
    doc_date: str,
    first_block_id: str,
    last_block_id: str,
) -> str:
    pages_clause = ""
    if page_first is not None and page_last is not None:
        pages_clause = f", pages {page_first}–{page_last}"
    return (
        f"{title}, {heading_path} (source: {source_file}{pages_clause}, "
        f"dated {doc_date}; blocks {first_block_id}…{last_block_id})"
    )


async def fetch_source_file(conn: asyncpg.Connection, slug: str) -> Optional[str]:
    return await conn.fetchval("SELECT source_file FROM documents WHERE slug = $1", slug)


async def fetch_block_ids(
    conn: asyncpg.Connection, slug: str, first_ordinal: int, last_ordinal: int
) -> tuple[str, str]:
    """The `block_id` strings for the ordinals `sections.block_first`/`block_last` (or a
    chunk's) record — those columns are integer ordinals, not the `<slug>:pNNN:bNNN` ids a
    citation needs."""
    first = await conn.fetchval(
        "SELECT block_id FROM blocks WHERE slug = $1 AND ordinal = $2", slug, first_ordinal
    )
    last = await conn.fetchval(
        "SELECT block_id FROM blocks WHERE slug = $1 AND ordinal = $2", slug, last_ordinal
    )
    return first, last
