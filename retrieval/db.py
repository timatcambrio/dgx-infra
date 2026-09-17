"""Postgres access: connection pool with the pgvector codec, and schema application
(brief §6.1).

The pgvector codec is registered in the pool's `init=` callback so that **every** pooled
connection gets it — registering it once on a single connection and reusing the pool is the
mistake the brief calls out in §13 ("vectors arrive as strings").
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
from pgvector.asyncpg import register_vector

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    """Open a pool against `dsn` with the pgvector codec registered on every connection."""
    return await asyncpg.create_pool(
        dsn, min_size=min_size, max_size=max_size, init=_init_connection
    )


def render_schema(embed_dim: int) -> str:
    """`schema.sql` with the `{EMBED_DIM}` placeholder substituted."""
    return SCHEMA_PATH.read_text(encoding="utf-8").replace("{EMBED_DIM}", str(embed_dim))


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class ReadRoleMissing(RuntimeError):
    """The read-only role named by `DATABASE_URL` does not exist in the database."""


async def grant_read_role(conn: asyncpg.Connection, read_dsn: str) -> str:
    """Grant `SELECT` on every table (now and later) to the role named by `read_dsn`.

    The role must already exist: `compose/init-db.sh` creates it, and this function never
    does. Creating roles needs `CREATEROLE`, and the indexing role deliberately does not
    have it. Raises `ReadRoleMissing` with the statement to run when the role is absent.
    Returns the role name.
    """
    parsed = urlsplit(read_dsn)
    role = parsed.username or "kb_read"

    exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
    if not exists:
        raise ReadRoleMissing(
            f"role {role!r} does not exist. It is created by compose/init-db.sh on first "
            f"start; on a database that script never ran against, create it as a "
            f"superuser first:\n  CREATE ROLE {_quote_ident(role)} LOGIN PASSWORD '...';\n"
            f"then re-run `kb index --init`."
        )

    await conn.execute(f"GRANT USAGE ON SCHEMA public TO {_quote_ident(role)}")
    await conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {_quote_ident(role)}")
    await conn.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO "
        f"{_quote_ident(role)}"
    )
    return role


async def init_schema(
    conn: asyncpg.Connection,
    *,
    embed_model: str,
    embed_dim: int,
    read_dsn: str | None = None,
) -> None:
    """Apply `schema.sql` (idempotent) and record `index_meta`. Optionally grant `SELECT`
    on all tables to the read role named by `read_dsn` (which must already exist).
    """
    await conn.execute(render_schema(embed_dim))
    await conn.execute(
        """
        INSERT INTO index_meta (key, value) VALUES ('embed_model', $1)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        embed_model,
    )
    await conn.execute(
        """
        INSERT INTO index_meta (key, value) VALUES ('embed_dim', $1)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        str(embed_dim),
    )
    await conn.execute(
        """
        INSERT INTO index_meta (key, value) VALUES ('schema_version', '1')
        ON CONFLICT (key) DO NOTHING
        """
    )
    if read_dsn is not None:
        await grant_read_role(conn, read_dsn)


async def get_index_meta(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch("SELECT key, value FROM index_meta")
    return {r["key"]: r["value"] for r in rows}
