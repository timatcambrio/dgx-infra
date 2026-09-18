"""`retrieval.search` (brief §6.4) against the fixtures, indexed once per session with the
fake ollama (reusing `test_index.py`'s setup).

Every asyncpg connection is opened and closed inside a single `asyncio.run(...)` call per
test: asyncpg connections are bound to the event loop that created them, and a connection
opened in a session-scoped fixture would outlive that loop the moment a second test called
`asyncio.run` again. So `indexed_dsn` (session-scoped: the fixtures are loaded only once)
is the shared state, and each test connects fresh within its own coroutine.

Skips (via `_schema_ready`) with a clear message if the test database is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Optional

import asyncpg
import httpx
import pytest
from fake_ollama import fixed_dim_handler
from pgvector.asyncpg import register_vector

from retrieval import index as index_module
from retrieval import search as search_module
from retrieval.config import Config
from retrieval.embed import embed_query

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kb"
EMBED_DIM = 8
EMBED_MODEL = "fake-embedder"


def _cfg(kb_path: Path, db_dsn: str) -> Config:
    return Config(
        kb_path=kb_path,
        database_url=None,
        database_url_index=db_dsn,
        ollama_base_url="http://fake-ollama",
        embed_model=EMBED_MODEL,
        embed_dim=EMBED_DIM,
        llm_base_url="http://localhost:11434/v1",
        llm_model="granite4:3b",
        kb_url_base=None,
        kb_bind="127.0.0.1:8765",
        kb_public_host="localhost",
        kb_tokens=(),
        fetch_max_chars=200_000,
        chunk_target=1200,
        chunk_max=2500,
    )


@pytest.fixture(scope="session")
def indexed_dsn(_schema_ready: str, tmp_path_factory: pytest.TempPathFactory) -> str:
    """The fixtures indexed once per session against `_schema_ready`, with the fake
    ollama."""
    kb_dir = tmp_path_factory.mktemp("search-fixtures") / "kb"
    kb_dir.mkdir(parents=True)
    for f in FIXTURES.iterdir():
        if f.name != "expected.json":
            shutil.copy2(f, kb_dir / f.name)

    cfg = _cfg(kb_dir.parent, _schema_ready)
    embed_client = httpx.Client(transport=httpx.MockTransport(fixed_dim_handler(EMBED_DIM)))

    async def _index() -> None:
        conn = await asyncpg.connect(_schema_ready)
        try:
            await conn.execute("TRUNCATE documents CASCADE")
        finally:
            await conn.close()
        result = await index_module.run_index(cfg, embed_client=embed_client)
        assert result.errors == []

    asyncio.run(_index())
    return _schema_ready


def _embed_fn(query: str, *, requests: Optional[list] = None) -> list[float]:
    client = httpx.Client(
        transport=httpx.MockTransport(fixed_dim_handler(EMBED_DIM, requests=requests))
    )
    try:
        return embed_query(
            query, base_url="http://fake-ollama", model=EMBED_MODEL, embed_dim=EMBED_DIM, client=client
        )
    finally:
        client.close()


async def _with_conn(dsn: str, fn):
    """Open a connection (with the pgvector codec), run the async `fn(conn)`, close it --
    all inside the caller's single `asyncio.run`, so the connection never crosses loops."""
    conn = await asyncpg.connect(dsn)
    await register_vector(conn)
    try:
        return await fn(conn)
    finally:
        await conn.close()


def _run(dsn: str, fn):
    return asyncio.run(_with_conn(dsn, fn))


def test_planted_token_is_first_hit_in_lexical_and_fused(indexed_dsn: str) -> None:
    async def _go(conn: asyncpg.Connection):
        lexical = await search_module.search(
            conn, "FORM-7731", embed_query_fn=_embed_fn, leg="lexical"
        )
        fused = await search_module.search(
            conn, "FORM-7731", embed_query_fn=_embed_fn, leg="fused"
        )
        return lexical, fused

    lexical, fused = _run(indexed_dsn, _go)
    assert lexical[0].slug == "budget-form"
    assert lexical[0].lexical_rank == 1
    assert fused[0].slug == "budget-form"


def test_stopword_only_query_returns_vector_leg_results_without_erroring(indexed_dsn: str) -> None:
    async def _go(conn: asyncpg.Connection):
        lexical = await search_module.search(
            conn, "the of and", embed_query_fn=_embed_fn, leg="lexical"
        )
        fused = await search_module.search(
            conn, "the of and", embed_query_fn=_embed_fn, leg="fused"
        )
        return lexical, fused

    lexical, fused = _run(indexed_dsn, _go)
    assert lexical == []
    assert len(fused) > 0
    assert all(h.lexical_rank is None for h in fused)


def test_slug_filter_restricts_results(indexed_dsn: str) -> None:
    async def _go(conn: asyncpg.Connection):
        return await search_module.search(
            conn, "Mileage", embed_query_fn=_embed_fn, leg="fused", filters={"slug": "budget-form"}
        )

    hits = _run(indexed_dsn, _go)
    assert hits
    assert all(h.slug == "budget-form" for h in hits)


def test_k_is_clamped(indexed_dsn: str) -> None:
    assert search_module.clamp_k(0) == 1
    assert search_module.clamp_k(100) == 25
    assert search_module.clamp_k(5) == 5

    async def _go(conn: asyncpg.Connection):
        return await search_module.search(conn, "the", embed_query_fn=_embed_fn, leg="vector", k=1000)

    hits = _run(indexed_dsn, _go)
    assert len(hits) <= 25


def test_tie_break_is_deterministic(indexed_dsn: str) -> None:
    async def _go(conn: asyncpg.Connection):
        first = await search_module.search(conn, "Mileage", embed_query_fn=_embed_fn, leg="fused")
        second = await search_module.search(conn, "Mileage", embed_query_fn=_embed_fn, leg="fused")
        return first, second

    first, second = _run(indexed_dsn, _go)
    ids1 = [(h.slug, h.section_index) for h in first]
    ids2 = [(h.slug, h.section_index) for h in second]
    assert ids1 == ids2


def test_snippet_is_at_most_300_chars_and_whitespace_collapsed(indexed_dsn: str) -> None:
    async def _go(conn: asyncpg.Connection):
        return await search_module.search(conn, "Mileage", embed_query_fn=_embed_fn, leg="lexical")

    hits = _run(indexed_dsn, _go)
    assert hits
    for h in hits:
        assert len(h.snippet) <= 300
        assert "\n" not in h.snippet
        assert "  " not in h.snippet


def test_collapse_keeps_one_row_per_section(indexed_dsn: str) -> None:
    async def _go(conn: asyncpg.Connection):
        return await search_module.search(conn, "the", embed_query_fn=_embed_fn, leg="vector", k=25)

    hits = _run(indexed_dsn, _go)
    keys = [(h.slug, h.section_index) for h in hits]
    assert len(keys) == len(set(keys))


def test_search_calls_embedder_with_search_query_prefix(indexed_dsn: str) -> None:
    requests: list = []

    def recording_embed_fn(query: str) -> list[float]:
        return _embed_fn(query, requests=requests)

    async def _go(conn: asyncpg.Connection):
        return await search_module.search(
            conn, "FORM-7731", embed_query_fn=recording_embed_fn, leg="vector"
        )

    _run(indexed_dsn, _go)
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["input"] == ["search_query: FORM-7731"]


def test_rrf_math_on_hand_built_ranked_lists() -> None:
    lexical = ["a", "b", "c"]
    vector = ["b", "d"]
    fused = search_module.fuse_rrf([lexical, vector], k=60)
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 63)
    assert fused["d"] == pytest.approx(1 / 62)
