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


def test_serve_http_with_empty_kb_tokens_and_no_allow_anonymous_exits_2(
    indexed_dsn: str, fake_ollama_http: str
) -> None:
    kb_bin = _kb_bin()
    if not kb_bin.is_file():
        pytest.skip(f"console script not found at {kb_bin} (needs `uv sync --extra serve`)")

    port = _free_port()
    env = _base_env(indexed_dsn, fake_ollama_http)
    env["KB_TOKENS"] = ""
    env["KB_BIND"] = f"127.0.0.1:{port}"

    result = subprocess.run(
        [str(kb_bin), "serve", "--transport", "http"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert "KB_TOKENS" in result.stderr
