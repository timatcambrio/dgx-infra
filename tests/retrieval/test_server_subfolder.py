"""A document converted from a SOURCE_DIR subfolder, served by id.

`slug_for` turns `gsa/handbook.pdf` into `gsa__handbook-pdf`, and `kb index` stores that
slug. The id parser allowed no underscore, so the document came back from `search` (whose
`sec:` ids are built, not parsed) but every `fetch`, `get_outline` and `get_section` on one
of its ids failed with "unknown id". The proxy corpus is flat, so it never showed.

The document is a copy of the committed handbook fixture under the nested name, with its
block anchors renamed to match, indexed additively beside the fixtures and dropped
afterwards (the pattern `test_server_oversize.py` uses, for the same reasons).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from conftest import make_server_config

from retrieval import server as server_module

SLUG = "gsa__handbook-pdf"


@pytest.fixture(scope="module")
def nested_cfg(indexed_dsn: str, fake_ollama_http: str, tmp_path_factory: pytest.TempPathFactory):
    import shutil

    import asyncpg
    import httpx
    from conftest import FIXTURES_DIR
    from fake_ollama import fixed_dim_handler

    from retrieval import index as index_module

    kb_dir = tmp_path_factory.mktemp("subfolder-fixtures") / "kb"
    kb_dir.mkdir(parents=True)
    for f in FIXTURES_DIR.iterdir():
        if f.name != "expected.json":
            shutil.copy2(f, kb_dir / f.name)
    for suffix in (".md", ".provenance.json"):
        text = (FIXTURES_DIR / f"handbook-pdf{suffix}").read_text(encoding="utf-8")
        (kb_dir / f"{SLUG}{suffix}").write_text(
            text.replace("handbook-pdf:p", f"{SLUG}:p"), encoding="utf-8"
        )

    cfg = make_server_config(
        kb_dir.parent, indexed_dsn, database_url_index=indexed_dsn, ollama_base_url=fake_ollama_http
    )
    embed_client = httpx.Client(transport=httpx.MockTransport(fixed_dim_handler(8)))

    async def _index() -> None:
        result = await index_module.run_index(cfg, embed_client=embed_client)
        assert result.errors == []

    asyncio.run(_index())
    yield cfg

    async def _drop() -> None:
        conn = await asyncpg.connect(indexed_dsn)
        try:
            await conn.execute("DELETE FROM documents WHERE slug = $1", SLUG)
        finally:
            await conn.close()

    asyncio.run(_drop())


def _call(cfg, tool: str, args: dict) -> dict:
    from mcp.shared.memory import create_connected_server_and_client_session

    async def _go():
        srv = server_module.build_server(cfg)
        async with create_connected_server_and_client_session(srv, raise_exceptions=True) as s:
            res = await s.call_tool(tool, args)
            return json.loads(res.content[0].text)

    return asyncio.run(_go())


def test_search_then_fetch_a_document_from_a_subfolder(nested_cfg) -> None:
    found = _call(nested_cfg, "search", {"query": "Mileage", "slug": SLUG})
    assert found["results"], found
    sec = found["results"][0]["id"]
    assert sec.startswith(f"sec:{SLUG}:")

    fetched = _call(nested_cfg, "fetch", {"id": sec})
    assert fetched["metadata"]["kind"] == "section"
    assert "Mileage" in fetched["text"]


def test_every_id_kind_resolves_for_a_document_from_a_subfolder(nested_cfg) -> None:
    outline = _call(nested_cfg, "get_outline", {"id": f"doc:{SLUG}"})
    assert outline["id"] == f"doc:{SLUG}"
    assert outline["sections"]
    assert all(s["id"].startswith(f"sec:{SLUG}:") for s in outline["sections"])

    assert _call(nested_cfg, "fetch", {"id": f"doc:{SLUG}"})["metadata"]["kind"] == "document"
    assert _call(nested_cfg, "fetch", {"id": f"page:{SLUG}:p001"})["metadata"]["kind"] == "page"
    assert _call(nested_cfg, "fetch", {"id": f"chunk:{SLUG}:0"})["text"]
    around = _call(nested_cfg, "get_section", {"id": outline["sections"][0]["id"], "neighbours": 1})
    assert around["metadata"]["kind"] == "section"
