"""Failures a user can fix must be one line each, not a traceback (and never a traceback
whose locals panel prints the database password)."""

from __future__ import annotations

import socket

import pytest
from typer.testing import CliRunner

pytest.importorskip("asyncpg", reason="requires `uv sync --extra serve`")

from retrieval.cli import app  # noqa: E402

runner = CliRunner()


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def unreachable_env(monkeypatch, tmp_path):
    port = _closed_port()
    monkeypatch.setenv("KB_PATH", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"postgresql://kb_read:secret-pw@127.0.0.1:{port}/kb")
    monkeypatch.setenv("DATABASE_URL_INDEX", f"postgresql://kb_index:secret-pw@127.0.0.1:{port}/kb")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("KB_URL_BASE", "http://localhost/kb")
    return port


@pytest.mark.parametrize("argv", [["index"], ["index", "--init"], ["search", "anything"], ["eval"]])
def test_unreachable_postgres_is_one_line_with_the_fix(unreachable_env, argv):
    port = unreachable_env
    result = runner.invoke(app, argv)
    assert result.exit_code == 2, result.output
    assert f"127.0.0.1:{port}" in result.output
    assert "cannot reach Postgres" in result.output
    assert "docker compose" in result.output
    assert "Traceback" not in result.output
    assert "secret-pw" not in result.output


def test_an_unapplied_schema_is_one_line_naming_init(monkeypatch, tmp_path):
    """The third failure a fresh deployment hits, and the one the quickstart walked into.

    Measured 2026-10-01 against the real compose stack: `make compose-up` followed by
    `make compose-index` -- the two commands the README's quickstart gives -- ended in a
    raw asyncpg traceback, `UndefinedTableError: relation "index_meta" does not exist`,
    because nothing in the compose path had ever run `kb index --init`. Nothing in that
    output said so.
    """
    import asyncpg

    monkeypatch.setenv("KB_PATH", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL_INDEX", "postgresql://kb_index:secret-pw@127.0.0.1:5432/kb")
    monkeypatch.setenv("DATABASE_URL", "postgresql://kb_read:secret-pw@127.0.0.1:5432/kb")
    monkeypatch.setenv("KB_URL_BASE", "http://localhost/kb")

    def boom(coro, *_args, **_kwargs):
        coro.close()  # nothing awaits it; closing keeps pytest's unraisable hook quiet
        raise asyncpg.UndefinedTableError('relation "index_meta" does not exist')

    monkeypatch.setattr("asyncio.run", boom)

    result = runner.invoke(app, ["index"])
    assert result.exit_code == 2, result.output
    assert "kb index --init" in result.output
    assert "make compose-index ARGS=--init" in result.output
    assert "Traceback" not in result.output
    assert "secret-pw" not in result.output
