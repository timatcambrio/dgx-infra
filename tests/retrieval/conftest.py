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
import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "kb"

DEFAULT_DATABASE_URL_TEST = "postgresql://kb_index:kb@localhost:5432/kb_test"

#: Shared by `test_server.py` and `test_server_http.py` (and anything else that wants the
#: fixtures indexed with the fake ollama): the embed dimension/model of the fake embedder,
#: and the citation `url` base used throughout those two files' assertions.
SERVER_EMBED_DIM = 8
SERVER_EMBED_MODEL = "fake-embedder"
SERVER_KB_URL_BASE = "https://kb.example.test/kb"

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


def make_server_config(kb_path: Path, db_dsn: str, **overrides):
    """A `retrieval.config.Config` for the server tests, defaulting to the fake embedder
    and a fixed `KB_URL_BASE` — shared by `test_server.py` and `test_server_http.py`."""
    from retrieval.config import Config

    base = dict(
        kb_path=kb_path,
        database_url=db_dsn,
        database_url_index=None,
        ollama_base_url="http://fake-ollama",
        embed_model=SERVER_EMBED_MODEL,
        embed_dim=SERVER_EMBED_DIM,
        llm_base_url="http://localhost:11434/v1",
        llm_model="granite4:3b",
        kb_url_base=SERVER_KB_URL_BASE,
        kb_bind="127.0.0.1:8765",
        kb_public_host="localhost",
        kb_tokens=(),
        fetch_max_chars=200_000,
        chunk_target=1200,
        chunk_max=2500,
    )
    base.update(overrides)
    return Config(**base)


@pytest.fixture(scope="session")
def indexed_dsn(_schema_ready: str, tmp_path_factory: pytest.TempPathFactory) -> str:
    """The `tests/retrieval/fixtures/kb/` documents indexed once per session against
    `_schema_ready`, with the fake ollama (identical pattern to `test_search.py`). Shared
    by `test_server.py` (in-memory + stdio subprocess) and `test_server_http.py` (HTTP
    subprocess) so both pay the indexing cost once."""
    import asyncpg
    import httpx
    from fake_ollama import fixed_dim_handler

    from retrieval import index as index_module

    kb_dir = tmp_path_factory.mktemp("server-fixtures") / "kb"
    kb_dir.mkdir(parents=True)
    for f in FIXTURES_DIR.iterdir():
        if f.name != "expected.json":
            shutil.copy2(f, kb_dir / f.name)

    cfg = make_server_config(kb_dir.parent, _schema_ready, database_url_index=_schema_ready)
    embed_client = httpx.Client(transport=httpx.MockTransport(fixed_dim_handler(SERVER_EMBED_DIM)))

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


class _EmbedHandler(BaseHTTPRequestHandler):
    """A real (loopback) HTTP `/api/embed`, for subprocess tests — a subprocess cannot
    share this process's `httpx.MockTransport`, so it needs an actual server to talk to
    (brief §8.2: "a tiny ... HTTP server on a free port for the CLI tests")."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_POST(self) -> None:  # noqa: N802
        from fake_ollama import deterministic_vector

        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        vectors = [deterministic_vector(t, SERVER_EMBED_DIM) for t in body["input"]]
        payload = json.dumps({"embeddings": vectors}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture(scope="session")
def fake_ollama_http() -> str:
    """A real (loopback) fake `/api/embed` HTTP server, for the subprocess tests in
    `test_server.py` (stdio) and `test_server_http.py` (HTTP)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbedHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
