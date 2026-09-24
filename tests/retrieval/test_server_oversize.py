"""`fetch`/`get_section` against a unit that is larger than `FETCH_MAX_CHARS`.

`_fetch_document` has always refused an over-cap document, but a *section* is not a
bounded unit: in DOCX-derived documents one heading can span thousands of blocks, and the
converted corpus has sections of over half a million characters — more than the document
path refuses to send. These tests hold every fetch path to the same ceiling.

The oversize document is generated here rather than committed to `fixtures/kb/`: it has to
be bigger than the real 200,000-char default to be worth testing at that default, and it
would otherwise be indexed for (and asserted against by) every other server test. Its
shape is the shape that causes the problem — no sidecar, so no pages, one level-2 heading
covering everything.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from conftest import make_server_config

from retrieval import server as server_module

#: Comfortably over the 200,000-char default, so the default cap is what these tests
#: exercise — not a lowered one.
_TARGET_CHARS = 320_000

_WORDS = (
    "grentin dansith entrivan corvel nixplat limnara oswent implor quexel vexrona brindle "
    "kelbron bexnit juxtane zynth yellum zathum ivrixal umblist sembrik orquist torvane"
).split()


def _paragraph(n: int) -> str:
    words = [_WORDS[(n * 7 + i) % len(_WORDS)] for i in range(40)]
    return f"Paragraph {n}: " + " ".join(words) + "."


def _big_markdown() -> str:
    body_parts = ["# Oversize Manual", "", "## MP5301.6 CAREER DEVELOPMENT", ""]
    n = 0
    size = 0
    while size < _TARGET_CHARS:
        para = _paragraph(n)
        body_parts.extend([para, ""])
        size += len(para) + 2
        n += 1
    body = "\n".join(body_parts)
    sha = hashlib.sha256(body.encode()).hexdigest()
    return (
        "---\n"
        "title: Oversize Manual\n"
        "source_file: oversize.docx\n"
        "source_format: docx\n"
        "source_url: null\n"
        "doc_date: UNCONFIRMED\n"
        "retrieved: '2026-09-24'\n"
        "converter: libreoffice-docx\n"
        "text_coverage: null\n"
        "text_class: clean\n"
        "needs_ocr: false\n"
        f"content_sha256: {sha}\n"
        "---\n\n" + body + "\n"
    )


#: The deep-outline document: enough level-3 headings that listing them all is over the cap
#: a test sets, few enough level-2 headings that listing only those is under it.
_DEEP_PARENTS = 40
_DEEP_CHILDREN = 8


def _deep_markdown() -> str:
    """A document whose *outline listing* is the thing that exceeds the cap, not any one
    section: `_DEEP_PARENTS * (1 + _DEEP_CHILDREN)` sections, all of them small."""
    body_parts = ["# Deep Outline", ""]
    for parent in range(_DEEP_PARENTS):
        body_parts += [f"## Subpart {parent:02d} — Contracting Authority And Responsibilities", ""]
        for child in range(_DEEP_CHILDREN):
            body_parts += [
                f"### MP53{parent:02d}.{child} Approval Authority For This Numbered Matter",
                "",
                _paragraph(parent * _DEEP_CHILDREN + child),
                "",
            ]
    body = "\n".join(body_parts)
    sha = hashlib.sha256(body.encode()).hexdigest()
    return (
        "---\n"
        "title: Deep Outline\n"
        "source_file: deep-outline.docx\n"
        "source_format: docx\n"
        "source_url: null\n"
        "doc_date: UNCONFIRMED\n"
        "retrieved: '2026-09-24'\n"
        "converter: libreoffice-docx\n"
        "text_coverage: null\n"
        "text_class: clean\n"
        "needs_ocr: false\n"
        f"content_sha256: {sha}\n"
        "---\n\n" + body + "\n"
    )


@pytest.fixture(scope="module")
def oversize_cfg(indexed_dsn: str, tmp_path_factory: pytest.TempPathFactory):
    """The generated document indexed *alongside* the committed fixtures, into the same
    session database, with the deep-outline document beside it — additively, so nothing
    another module indexed is disturbed (the kb
    directory holds the fixtures too, since `kb index` prunes slugs it does not find, and
    they index as `unchanged`), and dropped again afterwards so a module that asserts on
    the fixture document *count* sees the same four however the files are ordered."""
    import shutil

    import asyncpg
    import httpx
    from conftest import FIXTURES_DIR
    from fake_ollama import fixed_dim_handler

    from retrieval import index as index_module

    kb_dir = tmp_path_factory.mktemp("oversize-fixtures") / "kb"
    kb_dir.mkdir(parents=True)
    for f in FIXTURES_DIR.iterdir():
        if f.name != "expected.json":
            shutil.copy2(f, kb_dir / f.name)
    (kb_dir / "oversize.md").write_text(_big_markdown(), encoding="utf-8")
    (kb_dir / "deep-outline.md").write_text(_deep_markdown(), encoding="utf-8")

    cfg = make_server_config(kb_dir.parent, indexed_dsn, database_url_index=indexed_dsn)
    embed_client = httpx.Client(transport=httpx.MockTransport(fixed_dim_handler(8)))

    async def _index() -> None:
        result = await index_module.run_index(cfg, embed_client=embed_client)
        assert result.errors == []

    asyncio.run(_index())
    yield cfg

    async def _drop() -> None:
        conn = await asyncpg.connect(indexed_dsn)
        try:
            await conn.execute(
                "DELETE FROM documents WHERE slug = ANY($1::text[])",
                ["oversize", "deep-outline"],
            )
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


def _oversize_section_id(cfg) -> str:
    """The `sec:` id of the one section that carries the generated body."""
    outline = _call(cfg, "get_outline", {"id": "doc:oversize"})
    biggest = max(outline["sections"], key=lambda s: s["chars"])
    assert biggest["chars"] > cfg.fetch_max_chars, biggest
    return biggest["id"]


def test_fetch_oversized_section_is_bounded_and_names_how_to_narrow(oversize_cfg) -> None:
    cfg = oversize_cfg
    sec_id = _oversize_section_id(cfg)

    fetched = _call(cfg, "fetch", {"id": sec_id})

    assert fetched["metadata"]["truncated"] is True
    assert fetched["metadata"]["kind"] == "section"
    assert len(fetched["text"]) <= cfg.fetch_max_chars
    # Bounded, not merely under the cap: a breakdown is small whatever the section's size.
    assert len(fetched["text"]) < 20_000
    assert fetched["metadata"]["chars"] == len(fetched["text"])
    assert "FETCH_MAX_CHARS" in fetched["text"]
    assert "chunk:oversize:" in fetched["text"]


def test_a_chunk_id_named_by_the_breakdown_fetches_that_chunk_alone(oversize_cfg) -> None:
    """The breakdown's advice has to be true: a chunk id it names must return bounded text,
    not the enclosing over-cap section again."""
    cfg = oversize_cfg
    sec_id = _oversize_section_id(cfg)
    breakdown = _call(cfg, "fetch", {"id": sec_id})

    ids = [
        word.strip("`.,;")
        for word in breakdown["text"].split()
        if word.strip("`.,;").startswith("chunk:oversize:")
    ]
    assert ids, breakdown["text"]

    fetched = _call(cfg, "fetch", {"id": ids[-1]})
    assert fetched["metadata"]["truncated"] is False
    assert len(fetched["text"]) <= cfg.chunk_max * 5
    assert fetched["text"].strip()
    assert "Paragraph" in fetched["text"]


def test_get_section_with_neighbours_is_bounded_too(oversize_cfg) -> None:
    cfg = oversize_cfg
    sec_id = _oversize_section_id(cfg)

    fetched = _call(cfg, "get_section", {"id": sec_id, "neighbours": 1})

    assert fetched["metadata"]["truncated"] is True
    assert len(fetched["text"]) < 20_000
    assert "chunk:oversize:" in fetched["text"]


def test_document_refusal_says_which_sections_are_themselves_over_the_cap(oversize_cfg) -> None:
    cfg = make_server_config(
        oversize_cfg.kb_path, oversize_cfg.database_url, fetch_max_chars=1_000
    )

    fetched = _call(cfg, "fetch", {"id": "doc:oversize"})

    assert fetched["metadata"]["truncated"] is True
    assert len(fetched["text"]) <= cfg.fetch_max_chars
    assert "over the cap" in fetched["text"]


def test_document_outline_drops_deeper_headings_before_it_is_cut(oversize_cfg) -> None:
    """A document can have too many *headings* to list, not just too much text: 360 outline
    entries are over this cap where the 40 level-2 entries are not. Trimming by depth has
    to come before cutting the list short — a caller handed the top two levels can still
    `get_outline` for the rest, and the reply says the deeper ones are missing either way.
    """
    cfg = make_server_config(
        oversize_cfg.kb_path, oversize_cfg.database_url, fetch_max_chars=10_000
    )

    fetched = _call(cfg, "fetch", {"id": "doc:deep-outline"})
    text = fetched["text"]

    assert fetched["metadata"]["truncated"] is True
    assert len(text) <= cfg.fetch_max_chars
    assert "Headings deeper than level 2 are left out" in text
    # Trimming was enough on its own; nothing was cut off the end.
    assert "Listing cut here" not in text
    # Every level-2 section is still named (plus the level-1 title section), and the
    # level-3 ones are gone.
    assert text.count("- sec:deep-outline:") == _DEEP_PARENTS + 1
    assert "Approval Authority For This Numbered Matter" not in text
