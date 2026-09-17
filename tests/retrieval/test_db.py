"""Smoke test (brief §9, S0): `--init` applies the schema and `SELECT` on `chunks` works.

Skips with a clear message if the test database is unreachable — see
`tests/retrieval/conftest.py`.
"""

from __future__ import annotations

import asyncio

from retrieval import db as db_module


def test_init_schema_is_idempotent_and_chunks_is_selectable(db_dsn: str) -> None:
    async def _run() -> None:
        import asyncpg

        conn = await asyncpg.connect(db_dsn)
        try:
            # Applied once already by the session fixture; applying again must not error.
            await db_module.init_schema(conn, embed_model="fake-embedder", embed_dim=8)

            rows = await conn.fetch("SELECT * FROM chunks")
            assert rows == []

            meta = await db_module.get_index_meta(conn)
            assert meta["embed_model"] == "fake-embedder"
            assert meta["embed_dim"] == "8"
            assert meta["schema_version"] == "1"

            tables = await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
            names = {r["table_name"] for r in tables}
            assert {"index_meta", "documents", "blocks", "sections", "chunks"} <= names
        finally:
            await conn.close()

    asyncio.run(_run())


def test_grant_read_role_grants_select_to_existing_role(db_dsn: str) -> None:
    """`kb_read` is created by compose/init-db.sh; `--init` only grants to it."""

    async def _run() -> None:
        import asyncpg

        conn = await asyncpg.connect(db_dsn)
        try:
            role = await db_module.grant_read_role(
                conn, "postgresql://kb_read:kb@localhost:5432/kb_test"
            )
            assert role == "kb_read"
            has_select = await conn.fetchval(
                "SELECT has_table_privilege('kb_read', 'chunks', 'SELECT')"
            )
            assert has_select is True
        finally:
            await conn.close()

    asyncio.run(_run())


def test_grant_read_role_refuses_to_create_a_missing_role(db_dsn: str) -> None:
    """The indexing role has no CREATEROLE on purpose: a missing read role is a clear
    error naming the statement to run, never a silent CREATE ROLE."""

    async def _run() -> None:
        import asyncpg
        import pytest

        conn = await asyncpg.connect(db_dsn)
        try:
            with pytest.raises(db_module.ReadRoleMissing) as excinfo:
                await db_module.grant_read_role(
                    conn, "postgresql://kb_read_absent:secret@localhost:5432/kb_test"
                )
            assert 'CREATE ROLE "kb_read_absent"' in str(excinfo.value)
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname = 'kb_read_absent'"
            )
            assert exists is None
        finally:
            await conn.close()

    asyncio.run(_run())
