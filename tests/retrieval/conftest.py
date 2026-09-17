"""Shared fixtures for `tests/retrieval/`.

Async tests are plain `def` functions that call `asyncio.run(...)` themselves rather than
using `pytest-asyncio` — that dependency is not on the brief's approved list (§2.5), and one
`asyncio.run` per test is all these need.

The test database defaults to `DATABASE_URL_TEST=postgresql://kb_index:kb@localhost:5432/
kb_test` (the compose `dev` profile's `db` service publishes 5432 to localhost). Any test
that needs it is skipped, with a clear message, if the database is unreachable — `kb`'s own
test suite must not require Docker to be running for the parts that do not touch Postgres.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "kb"

DEFAULT_DATABASE_URL_TEST = "postgresql://kb_index:kb@localhost:5432/kb_test"

#: Every env var retrieval/config.py reads. Scrubbed by the autouse fixture below so a
#: developer's real .env or exported shell variables cannot leak into the suite.
RETRIEVAL_ENV_VARS = (
    "KB_PATH",
    "DATABASE_URL",
    "DATABASE_URL_INDEX",
    "DATABASE_URL_TEST",
    "OLLAMA_BASE_URL",
    "EMBED_MODEL",
    "EMBED_DIM",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "KB_URL_BASE",
    "KB_BIND",
    "KB_PUBLIC_HOST",
    "KB_TOKENS",
    "FETCH_MAX_CHARS",
    "CHUNK_TARGET",
    "CHUNK_MAX",
)


@pytest.fixture(autouse=True)
def _scrubbed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in RETRIEVAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def database_url_test() -> str:
    return os.environ.get("DATABASE_URL_TEST", DEFAULT_DATABASE_URL_TEST)


def _can_connect(dsn: str) -> bool:
    import asyncpg

    async def _try() -> bool:
        try:
            conn = await asyncpg.connect(dsn, timeout=2)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_try())


@pytest.fixture(scope="session")
def test_db_dsn() -> str:
    """The test database DSN, or a skip with a clear message if it is unreachable."""
    pytest.importorskip("asyncpg", reason="requires `uv sync --extra serve`")
    dsn = database_url_test()
    if not _can_connect(dsn):
        pytest.skip(
            f"test database unreachable at {dsn} — start it with "
            "`docker compose -f compose/docker-compose.yml --profile dev up -d db` "
            "(see compose/docker-compose.yml) and create kb_test, or set DATABASE_URL_TEST"
        )
    return dsn


@pytest.fixture(scope="session")
def _schema_ready(test_db_dsn: str) -> str:
    """Schema recreated once per test session against `test_db_dsn`."""
    import asyncpg

    async def _reset() -> None:
        conn = await asyncpg.connect(test_db_dsn)
        try:
            await conn.execute(
                "DROP TABLE IF EXISTS chunks, sections, blocks, documents, index_meta CASCADE"
            )
            from retrieval import db as db_module

            await db_module.init_schema(conn, embed_model="fake-embedder", embed_dim=8)
        finally:
            await conn.close()

    asyncio.run(_reset())
    return test_db_dsn


@pytest.fixture()
def db_dsn(_schema_ready: str) -> str:
    """The ready test database DSN, truncated to empty content tables for this test."""
    import asyncpg

    async def _truncate() -> None:
        conn = await asyncpg.connect(_schema_ready)
        try:
            await conn.execute("TRUNCATE documents CASCADE")
        finally:
            await conn.close()

    asyncio.run(_truncate())
    return _schema_ready
