"""`kb serve --transport http` (brief §9 S4, §8.4): a real subprocess, bound to
`127.0.0.1` on a free port, against the fixtures indexed once per session with the fake
ollama (same `indexed_dsn`/`fake_ollama_http` fixtures as `test_server.py`). Driven with
`mcp.client.streamable_http` + `ClientSession`, plus plain `httpx` for `/health` and the
no-token 401 check.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from conftest import SERVER_EMBED_DIM, SERVER_EMBED_MODEL, SERVER_KB_URL_BASE
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from retrieval import tokens as tokens_module

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kb"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EMBED_DIM = SERVER_EMBED_DIM
EMBED_MODEL = SERVER_EMBED_MODEL
KB_URL_BASE = SERVER_KB_URL_BASE
TOKEN = "testtoken"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kb_bin() -> Path:
    return REPO_ROOT / ".venv" / "bin" / "kb"


def _base_env(indexed_dsn: str, fake_ollama_http: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "KB_PATH": str(FIXTURES.parent),
        "DATABASE_URL": indexed_dsn,
        "OLLAMA_BASE_URL": fake_ollama_http,
        "EMBED_MODEL": EMBED_MODEL,
        "EMBED_DIM": str(EMBED_DIM),
        "KB_URL_BASE": KB_URL_BASE,
        "KB_PUBLIC_HOST": "localhost",
        # Explicitly no token store: unset would mean `config.DEFAULT_TOKENS_FILE`, the
        # repo root's gitignored tokens.json, which a developer who has run
        # `kb token issue` locally does have. Tests that want a store set this.
        "KB_TOKENS_FILE": "",
    }


def _wait_for_health(base_url: str, proc: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else b""
            raise RuntimeError(f"server exited early (code {proc.returncode}): {out!r}")
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1.0)
            if resp.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001 — still starting up
            last_exc = exc
        time.sleep(0.2)
    raise RuntimeError(f"server never became healthy at {base_url}") from last_exc


@pytest.fixture()
def http_server(indexed_dsn: str, fake_ollama_http: str):
    kb_bin = _kb_bin()
    if not kb_bin.is_file():
        pytest.skip(f"console script not found at {kb_bin} (needs `uv sync --extra serve`)")

    port = _free_port()
    env = _base_env(indexed_dsn, fake_ollama_http)
    env["KB_TOKENS"] = TOKEN
    env["KB_BIND"] = f"127.0.0.1:{port}"

    proc = subprocess.Popen(
        [str(kb_bin), "serve", "--transport", "http"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url, proc)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_health_is_open_and_reports_ok(http_server: str) -> None:
    resp = httpx.get(f"{http_server}/health", timeout=5.0)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


def test_mcp_without_token_is_401(http_server: str) -> None:
    resp = httpx.post(
        f"{http_server}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Accept": "application/json, text/event-stream"},
        timeout=5.0,
    )
    assert resp.status_code == 401


def test_initialize_list_tools_search_fetch_over_http(http_server: str) -> None:
    async def _go():
        client = httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"})
        async with streamable_http_client(f"{http_server}/mcp", http_client=client) as (
            read,
            write,
            _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                search_res = await session.call_tool("search", {"query": "FORM-7731"})
                search_json = json.loads(search_res.content[0].text)
                first_id = search_json["results"][0]["id"]
                fetch_res = await session.call_tool("fetch", {"id": first_id})
                fetch_json = json.loads(fetch_res.content[0].text)
                return tools, search_json, fetch_json

    tools, search_json, fetch_json = asyncio.run(_go())

    assert {t.name for t in tools.tools} == {
        "search",
        "fetch",
        "list_documents",
        "get_outline",
        "get_section",
    }
    assert search_json["results"][0]["id"].startswith("sec:budget-form:")
    assert "FORM-7731" in fetch_json["text"]


def test_serve_http_with_no_credential_and_no_allow_anonymous_exits_2(
    indexed_dsn: str, fake_ollama_http: str
) -> None:
    """Hard rule 6: never start an unauthenticated HTTP server by accident. The check is
    now "can anything authenticate" across both sources, not "is KB_TOKENS non-empty"."""
    kb_bin = _kb_bin()
    if not kb_bin.is_file():
        pytest.skip(f"console script not found at {kb_bin} (needs `uv sync --extra serve`)")

    env = _base_env(indexed_dsn, fake_ollama_http)
    env["KB_TOKENS"] = ""
    env["KB_BIND"] = f"127.0.0.1:{_free_port()}"

    result = subprocess.run(
        [str(kb_bin), "serve", "--transport", "http"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert "no usable credential" in result.stderr
    # The message has to say what to do about it, not just what is wrong.
    assert "kb token issue" in result.stderr


def test_serve_http_with_only_revoked_tokens_exits_2(
    indexed_dsn: str, fake_ollama_http: str, tmp_path: Path
) -> None:
    """A store is present but everything in it has been withdrawn — still not a usable
    server, and the old `KB_TOKENS`-is-non-empty check could not have seen it."""
    kb_bin = _kb_bin()
    if not kb_bin.is_file():
        pytest.skip(f"console script not found at {kb_bin} (needs `uv sync --extra serve`)")

    token_file = tmp_path / "tokens.json"
    _, record = tokens_module.issue(token_file, "alice@example.com")
    tokens_module.revoke(token_file, record.id)

    env = _base_env(indexed_dsn, fake_ollama_http)
    env["KB_TOKENS_FILE"] = str(token_file)
    env["KB_BIND"] = f"127.0.0.1:{_free_port()}"

    result = subprocess.run(
        [str(kb_bin), "serve", "--transport", "http"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert "no usable credential" in result.stderr


def test_serve_http_with_a_broken_token_file_exits_2(
    indexed_dsn: str, fake_ollama_http: str, tmp_path: Path
) -> None:
    """A store that cannot be parsed must not start a server that authenticates nobody.
    (Breaking it *after* startup is the opposite case — the last good copy keeps serving;
    see `test_tokens.py`.)"""
    kb_bin = _kb_bin()
    if not kb_bin.is_file():
        pytest.skip(f"console script not found at {kb_bin} (needs `uv sync --extra serve`)")

    token_file = tmp_path / "tokens.json"
    token_file.write_text("{ broken")

    env = _base_env(indexed_dsn, fake_ollama_http)
    env["KB_TOKENS_FILE"] = str(token_file)
    env["KB_BIND"] = f"127.0.0.1:{_free_port()}"

    result = subprocess.run(
        [str(kb_bin), "serve", "--transport", "http"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert "not valid JSON" in result.stderr


# --------------------------------------------------------------------------------------
# The revocation fix, end to end against a real server process.
# --------------------------------------------------------------------------------------


@pytest.fixture()
def http_server_with_token_file(indexed_dsn: str, fake_ollama_http: str, tmp_path: Path):
    """A real `kb serve --transport http` backed by a token file, plus the file's path and
    two issued credentials. `KB_TOKEN_CACHE_SECONDS=0` so the test does not sleep — the
    caching itself is covered in `test_tokens.py` with a fake clock."""
    kb_bin = _kb_bin()
    if not kb_bin.is_file():
        pytest.skip(f"console script not found at {kb_bin} (needs `uv sync --extra serve`)")

    token_file = tmp_path / "tokens.json"
    alice_secret, alice = tokens_module.issue(token_file, "alice@example.com")
    bob_secret, _ = tokens_module.issue(token_file, "bob@example.com")

    port = _free_port()
    env = _base_env(indexed_dsn, fake_ollama_http)
    env["KB_TOKENS_FILE"] = str(token_file)
    env["KB_TOKEN_CACHE_SECONDS"] = "0"
    env["KB_BIND"] = f"127.0.0.1:{port}"

    proc = subprocess.Popen(
        [str(kb_bin), "serve", "--transport", "http"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url, proc)
        yield base_url, token_file, alice, alice_secret, bob_secret
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _post_mcp(base_url: str, secret: str) -> int:
    resp = httpx.post(
        f"{base_url}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={
            "Authorization": f"Bearer {secret}",
            "Accept": "application/json, text/event-stream",
        },
        timeout=5.0,
    )
    return resp.status_code


def test_revoking_one_token_leaves_the_server_up_and_everyone_else_working(
    http_server_with_token_file,
) -> None:
    """The fix, stated as a test: withdrawing one person's access is a file write against
    a running server. Before this, it meant editing `.env` and restarting, which dropped
    every user — so in practice it never happened."""
    base_url, token_file, alice, alice_secret, bob_secret = http_server_with_token_file

    assert _post_mcp(base_url, alice_secret) == 200
    assert _post_mcp(base_url, bob_secret) == 200

    tokens_module.revoke(token_file, alice.id)

    assert _post_mcp(base_url, alice_secret) == 401
    assert _post_mcp(base_url, bob_secret) == 200
    # And the server is still the same process, serving.
    assert httpx.get(f"{base_url}/health", timeout=5.0).status_code == 200


def test_a_newly_issued_token_works_against_the_running_server(
    http_server_with_token_file,
) -> None:
    """The other half: onboarding needs no restart either."""
    base_url, token_file, _, _, _ = http_server_with_token_file
    carol_secret, _ = tokens_module.issue(token_file, "carol@example.com")
    assert _post_mcp(base_url, carol_secret) == 200


def test_auth_check_answers_for_kb_files_over_http(http_server_with_token_file) -> None:
    """What Caddy asks on behalf of `/kb/*` (`compose/Caddyfile`), against the real
    server: 204 while the token is good, 401 once it is revoked — so a revocation closes
    the static file route at the same moment it closes `/mcp`."""
    base_url, token_file, alice, alice_secret, _ = http_server_with_token_file

    def check(secret: str) -> int:
        return httpx.get(
            f"{base_url}/auth/check",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=5.0,
        ).status_code

    assert check(alice_secret) == 204
    tokens_module.revoke(token_file, alice.id)
    assert check(alice_secret) == 401
    assert httpx.get(f"{base_url}/auth/check", timeout=5.0).status_code == 401
