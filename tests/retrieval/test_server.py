"""`retrieval.server` (brief §6.5, §8.4): the five MCP tools, against the fixtures indexed
once per session with the fake ollama (same pattern as `test_search.py`'s `indexed_dsn`).

Two flavours of test:

- In-memory, via `mcp.shared.memory.create_connected_server_and_client_session` — fast,
  exercises the tool logic and the dual content/structuredContent encoding.
- One real subprocess over stdio (hard rule 6: stdout is the protocol channel) — the only
  test that would actually fail if a stray `print` or a misconfigured logger wrote to
  stdout, since a polluted stdout breaks JSON-RPC framing and the SDK's stdio client raises
  or hangs rather than quietly succeeding.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from conftest import SERVER_KB_URL_BASE, make_server_config
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from retrieval import server as server_module

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kb"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EMBED_DIM = 8
EMBED_MODEL = "fake-embedder"
KB_URL_BASE = SERVER_KB_URL_BASE
_cfg = make_server_config


def _run(coro_fn):
    return asyncio.run(coro_fn())


# ----------------------------------------------------------------------------------------
# In-memory tests.
# ----------------------------------------------------------------------------------------


def test_list_tools_shows_five_tools_with_readonly_and_output_schema(indexed_dsn: str) -> None:
    cfg = _cfg(FIXTURES.parent, indexed_dsn)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            return await s.list_tools()

    result = _run(_go)
    names = {t.name for t in result.tools}
    assert names == {"search", "fetch", "list_documents", "get_outline", "get_section"}
    for t in result.tools:
        assert t.annotations is not None
        assert t.annotations.readOnlyHint is True
        assert t.outputSchema
        assert t.outputSchema.get("properties")


def test_search_then_fetch_round_trip(indexed_dsn: str, fake_ollama_http: str) -> None:
    cfg = _cfg(FIXTURES.parent, indexed_dsn, ollama_base_url=fake_ollama_http)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            search_res = await s.call_tool("search", {"query": "FORM-7731"})
            search_json = json.loads(search_res.content[0].text)
            first_id = search_json["results"][0]["id"]
            fetch_res = await s.call_tool("fetch", {"id": first_id})
            fetch_json = json.loads(fetch_res.content[0].text)
            return search_json, fetch_json

    search_json, fetch_json = _run(_go)
    assert search_json["results"][0]["id"].startswith("sec:budget-form:")
    assert "FORM-7731" in fetch_json["text"]
    assert fetch_json["metadata"]["kind"] == "section"
    assert "budget-form" in fetch_json["metadata"]["citation"]
    assert fetch_json["url"].startswith(KB_URL_BASE)


def test_dual_encoding_search_and_fetch(indexed_dsn: str, fake_ollama_http: str) -> None:
    cfg = _cfg(FIXTURES.parent, indexed_dsn, ollama_base_url=fake_ollama_http)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            search_res = await s.call_tool("search", {"query": "Mileage"})
            fetch_res = await s.call_tool("fetch", {"id": "doc:handbook"})
            return search_res, fetch_res

    search_res, fetch_res = _run(_go)
    assert json.loads(search_res.content[0].text) == search_res.structuredContent
    assert json.loads(fetch_res.content[0].text) == fetch_res.structuredContent


def test_fetch_document_returns_full_body_with_no_anchor_substring(indexed_dsn: str) -> None:
    cfg = _cfg(FIXTURES.parent, indexed_dsn)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            res = await s.call_tool("fetch", {"id": "doc:handbook"})
            return json.loads(res.content[0].text)

    fetch_json = _run(_go)
    assert "<!-- dgx:block" not in fetch_json["text"]
    assert fetch_json["metadata"]["kind"] == "document"
    assert fetch_json["metadata"]["truncated"] is False


def test_fetch_document_truncated_when_over_fetch_max_chars(indexed_dsn: str) -> None:
    cfg = _cfg(FIXTURES.parent, indexed_dsn, fetch_max_chars=10)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            res = await s.call_tool("fetch", {"id": "doc:handbook"})
            return json.loads(res.content[0].text)

    fetch_json = _run(_go)
    assert fetch_json["metadata"]["kind"] == "document"
    assert fetch_json["metadata"]["truncated"] is True
    assert "sec:handbook:" in fetch_json["text"]


def test_fetch_page_contains_incomplete_callout(indexed_dsn: str) -> None:
    cfg = _cfg(FIXTURES.parent, indexed_dsn)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            res = await s.call_tool("fetch", {"id": "page:budget-form:p002"})
            return json.loads(res.content[0].text)

    fetch_json = _run(_go)
    assert "INCOMPLETE" in fetch_json["text"]
    assert fetch_json["metadata"]["kind"] == "page"


def test_fetch_page_over_the_cap_is_bounded_and_names_how_to_narrow(indexed_dsn: str) -> None:
    """The page path takes the same ceiling as the document path (`test_server_oversize.py`
    covers the section path against a genuinely over-cap fixture; no committed fixture has
    a page anywhere near 200,000 characters, so this lowers the cap instead)."""
    cfg = _cfg(FIXTURES.parent, indexed_dsn, fetch_max_chars=200)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            res = await s.call_tool("fetch", {"id": "page:budget-form:p002"})
            return json.loads(res.content[0].text)

    fetch_json = _run(_go)
    assert fetch_json["metadata"]["kind"] == "page"
    assert fetch_json["metadata"]["truncated"] is True
    assert "FETCH_MAX_CHARS" in fetch_json["text"]
    assert "chunk:budget-form:" in fetch_json["text"]


def test_get_section_neighbours_spans_three_sections(indexed_dsn: str) -> None:
    """`sec:handbook:3` ("Expense Reporting") is a middle section with both a predecessor
    ("Travel Policy", index 2) and a successor ("Approvals", index 4); confirmed against
    the indexed fixture's own `sections` rows."""
    cfg = _cfg(FIXTURES.parent, indexed_dsn)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            res = await s.call_tool("get_section", {"id": "sec:handbook:3", "neighbours": 1})
            return json.loads(res.content[0].text)

    fetch_json = _run(_go)
    text = fetch_json["text"]
    assert "Travel Policy" in text
    assert "Expense Reporting" in text
    assert "Approvals" in text
    block_ids = fetch_json["metadata"]["block_ids"]
    assert block_ids is not None and len(block_ids) == 2
    assert all(bid.startswith("handbook:") for bid in block_ids)
    assert block_ids[0] != block_ids[1]


def test_fetch_section_that_opens_with_table_has_heading_and_table(indexed_dsn: str) -> None:
    """`sec:handbook:1` ("Introduction") opens with a table that is glued to its heading
    block (brief §5.4 rule 1) — confirmed against `sections`/`blocks` for the fixture."""
    cfg = _cfg(FIXTURES.parent, indexed_dsn)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            res = await s.call_tool("fetch", {"id": "sec:handbook:1"})
            return json.loads(res.content[0].text)

    fetch_json = _run(_go)
    assert "Introduction" in fetch_json["text"]
    assert "| Column 1 | Column 2 | Column 3 | Column 4 |" in fetch_json["text"]


def test_fetch_bogus_id_is_a_tool_error(indexed_dsn: str) -> None:
    cfg = _cfg(FIXTURES.parent, indexed_dsn)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            return await s.call_tool("fetch", {"id": "bogus:1"})

    result = _run(_go)
    assert result.isError is True
    assert "unknown id" in result.content[0].text


def test_fetch_unknown_section_is_a_tool_error(indexed_dsn: str) -> None:
    cfg = _cfg(FIXTURES.parent, indexed_dsn)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            return await s.call_tool("fetch", {"id": "sec:nosuch:0"})

    result = _run(_go)
    assert result.isError is True
    assert "unknown id" in result.content[0].text


def test_list_documents_returns_four_sorted_by_title(indexed_dsn: str) -> None:
    cfg = _cfg(FIXTURES.parent, indexed_dsn)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            res = await s.call_tool("list_documents", {})
            return json.loads(res.content[0].text)

    result = _run(_go)
    titles = [d["title"] for d in result["documents"]]
    assert len(titles) == 4
    assert titles == sorted(titles)


def test_get_outline_has_one_entry_per_section_with_sec_ids(indexed_dsn: str) -> None:
    cfg = _cfg(FIXTURES.parent, indexed_dsn)

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            res = await s.call_tool("get_outline", {"id": "doc:handbook"})
            return json.loads(res.content[0].text)

    result = _run(_go)
    assert len(result["sections"]) == 19  # brief expected.json: handbook section_count
    for entry in result["sections"]:
        assert entry["id"].startswith("sec:handbook:")


def test_one_tool_call_produces_exactly_one_json_log_line(
    indexed_dsn: str, fake_ollama_http: str, tmp_path: Path
) -> None:
    """A real subprocess (like the stdio test below), so the assertion is against actual
    bytes written to the process's stderr fd rather than against Python-level log-capture
    machinery, which a `logging.StreamHandler` cached at import time can evade."""
    kb_bin = REPO_ROOT / ".venv" / "bin" / "kb"
    if not kb_bin.is_file():
        pytest.skip(f"console script not found at {kb_bin} (needs `uv sync --extra serve`)")

    env = {
        "PATH": os.environ.get("PATH", ""),
        "KB_PATH": str(FIXTURES.parent),
        "DATABASE_URL": indexed_dsn,
        "OLLAMA_BASE_URL": fake_ollama_http,
        "EMBED_MODEL": EMBED_MODEL,
        "EMBED_DIM": str(EMBED_DIM),
        "KB_URL_BASE": KB_URL_BASE,
    }
    errlog_path = tmp_path / "server.stderr"

    async def _go():
        with open(errlog_path, "w", encoding="utf-8") as errlog:
            params = StdioServerParameters(
                command=str(kb_bin), args=["serve", "--transport", "stdio"], env=env, cwd=str(REPO_ROOT)
            )
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await session.call_tool("list_documents", {})

    _run(_go)
    lines = errlog_path.read_text(encoding="utf-8").splitlines()
    json_lines = [line for line in lines if line.startswith("{")]
    assert len(json_lines) == 1
    record = json.loads(json_lines[0])
    assert set(record.keys()) == {"ts", "tool", "args", "result_ids", "ms", "error"}
    assert record["tool"] == "list_documents"
    assert record["error"] is None


# ----------------------------------------------------------------------------------------
# Real subprocess over stdio (hard rule 6).
# ----------------------------------------------------------------------------------------


def test_stdio_subprocess_initialize_list_search_fetch(
    indexed_dsn: str, fake_ollama_http: str
) -> None:
    """Starts `kb serve --transport stdio` as a real subprocess and drives it with the
    SDK's stdio client. A stray `print` (or any non-JSON-RPC bytes) on the server's
    stdout breaks the framing this test relies on, so this is the test hard rule 6 asks
    for: it fails, rather than passing by coincidence, if stdout is ever polluted."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "KB_PATH": str(FIXTURES.parent),
        "DATABASE_URL": indexed_dsn,
        "OLLAMA_BASE_URL": fake_ollama_http,
        "EMBED_MODEL": EMBED_MODEL,
        "EMBED_DIM": str(EMBED_DIM),
        "KB_URL_BASE": KB_URL_BASE,
    }

    kb_bin = REPO_ROOT / ".venv" / "bin" / "kb"
    if not kb_bin.is_file():
        pytest.skip(f"console script not found at {kb_bin} (needs `uv sync --extra serve`)")

    async def _go():
        params = StdioServerParameters(
            command=str(kb_bin),
            args=["serve", "--transport", "stdio"],
            env=env,
            cwd=str(REPO_ROOT),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                search_res = await session.call_tool("search", {"query": "FORM-7731"})
                search_json = json.loads(search_res.content[0].text)
                first_id = search_json["results"][0]["id"]
                fetch_res = await session.call_tool("fetch", {"id": first_id})
                fetch_json = json.loads(fetch_res.content[0].text)
                return tools, search_json, fetch_json

    tools, search_json, fetch_json = _run(_go)
    assert {t.name for t in tools.tools} == {
        "search",
        "fetch",
        "list_documents",
        "get_outline",
        "get_section",
    }
    assert search_json["results"][0]["id"].startswith("sec:budget-form:")
    assert "FORM-7731" in fetch_json["text"]
