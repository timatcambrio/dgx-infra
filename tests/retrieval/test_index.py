"""`kb index` (brief §6.2) acceptance tests, against the test DB and a fake ollama.

Skips (via `db_dsn`) with a clear message if the test database is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest

from fake_ollama import fixed_dim_handler

from retrieval import index as index_module
from retrieval.config import Config

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kb"
EMBED_DIM = 8  # matches conftest's _schema_ready (embed_model="fake-embedder", embed_dim=8)
EMBED_MODEL = "fake-embedder"


def _copy_fixtures(dst_kb_dir: Path) -> None:
    dst_kb_dir.mkdir(parents=True, exist_ok=True)
    for f in FIXTURES.iterdir():
        if f.name == "expected.json":
            continue
        shutil.copy2(f, dst_kb_dir / f.name)


def _cfg(tmp_path: Path, db_dsn: str, *, embed_model: str = EMBED_MODEL, embed_dim: int = EMBED_DIM) -> Config:
    return Config(
        kb_path=tmp_path,
        database_url=None,
        database_url_index=db_dsn,
        ollama_base_url="http://fake-ollama",
        embed_model=embed_model,
        embed_dim=embed_dim,
        llm_base_url="http://localhost:11434/v1",
        llm_model="granite4:3b",
        kb_url_base=None,
        kb_bind="127.0.0.1:8765",
        kb_public_host="localhost",
        kb_tokens=(),
        fetch_max_chars=200_000,
        chunk_target=1200,
        chunk_max=2500,
    )


def _embed_client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fixed_dim_handler(EMBED_DIM)))


def _run(cfg: Config, **kwargs) -> index_module.IndexResult:
    kwargs.setdefault("embed_client", _embed_client())
    return asyncio.run(index_module.run_index(cfg, **kwargs))


async def _fetch_counts(dsn: str, slug: str) -> dict:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        blocks = await conn.fetchval("SELECT count(*) FROM blocks WHERE slug=$1", slug)
        sections = await conn.fetchval("SELECT count(*) FROM sections WHERE slug=$1", slug)
        chunks = await conn.fetchval("SELECT count(*) FROM chunks WHERE slug=$1", slug)
        return {"blocks": blocks, "sections": sections, "chunks": chunks}
    finally:
        await conn.close()


async def _index_meta(dsn: str) -> dict:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT key, value FROM index_meta")
        return {r["key"]: r["value"] for r in rows}
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
def _reset_index_meta(db_dsn: str) -> None:
    """`db_dsn` truncates the content tables per test but leaves `index_meta` alone (it
    is not a content table); reset it to this module's baseline so tests that mutate it
    (the --reindex-all / EMBED_MODEL-change tests) cannot leak into later tests."""

    async def _reset() -> None:
        import asyncpg

        conn = await asyncpg.connect(db_dsn)
        try:
            for key, value in (("embed_model", EMBED_MODEL), ("embed_dim", str(EMBED_DIM))):
                await conn.execute(
                    "INSERT INTO index_meta (key, value) VALUES ($1, $2) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    key,
                    value,
                )
        finally:
            await conn.close()

    asyncio.run(_reset())


def test_index_from_empty_matches_expected_counts(tmp_path: Path, db_dsn: str) -> None:
    _copy_fixtures(tmp_path / "kb")
    cfg = _cfg(tmp_path, db_dsn)
    result = _run(cfg)

    assert result.errors == []
    assert result.reindexed == 4
    assert result.unchanged == 0
    assert result.deleted == 0

    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))["documents"]
    for slug, exp in expected.items():
        counts = asyncio.run(_fetch_counts(db_dsn, slug))
        assert counts["blocks"] == exp["block_count"] - exp.get("dropped_empty_blocks", 0), slug
        assert counts["sections"] == exp["section_count"], slug
        assert counts["chunks"] == exp["chunk_count"], slug


def test_second_run_is_all_unchanged(tmp_path: Path, db_dsn: str) -> None:
    _copy_fixtures(tmp_path / "kb")
    cfg = _cfg(tmp_path, db_dsn)
    _run(cfg)

    result = _run(cfg)
    assert result.summary_line == "4 unchanged, 0 reindexed, 0 deleted, 0 errors"


def test_modifying_one_fixture_byte_reindexes_only_that_document(tmp_path: Path, db_dsn: str) -> None:
    kb_dir = tmp_path / "kb"
    _copy_fixtures(kb_dir)
    cfg = _cfg(tmp_path, db_dsn)
    _run(cfg)

    target = kb_dir / "deck.md"
    text = target.read_text(encoding="utf-8")
    # A byte change that keeps the frontmatter/sha contract irrelevant: kb_sha256 is the
    # markdown file's own bytes, computed fresh every run — it is not re-validated against
    # anything, so any byte edit is enough to change it.
    target.write_text(text + " ", encoding="utf-8")

    result = _run(cfg)
    assert result.summary_line == "3 unchanged, 1 reindexed, 0 deleted, 0 errors"


def test_deleting_one_fixture_deletes_its_rows(tmp_path: Path, db_dsn: str) -> None:
    kb_dir = tmp_path / "kb"
    _copy_fixtures(kb_dir)
    cfg = _cfg(tmp_path, db_dsn)
    _run(cfg)

    (kb_dir / "reference-table.md").unlink()

    result = _run(cfg)
    assert result.summary_line == "3 unchanged, 0 reindexed, 1 deleted, 0 errors"
    counts = asyncio.run(_fetch_counts(db_dsn, "reference-table"))
    assert counts == {"blocks": 0, "sections": 0, "chunks": 0}


def test_untouched_document_row_counts_identical_across_runs(tmp_path: Path, db_dsn: str) -> None:
    kb_dir = tmp_path / "kb"
    _copy_fixtures(kb_dir)
    cfg = _cfg(tmp_path, db_dsn)
    _run(cfg)
    before = asyncio.run(_fetch_counts(db_dsn, "handbook"))

    # touch an unrelated document so *something* reindexes, "handbook" itself untouched
    (kb_dir / "deck.md").write_text(
        (kb_dir / "deck.md").read_text(encoding="utf-8") + " ", encoding="utf-8"
    )
    _run(cfg)
    after = asyncio.run(_fetch_counts(db_dsn, "handbook"))
    assert before == after


def test_corrupted_fixture_is_reported_and_old_rows_survive(tmp_path: Path, db_dsn: str) -> None:
    kb_dir = tmp_path / "kb"
    _copy_fixtures(kb_dir)
    cfg = _cfg(tmp_path, db_dsn)
    _run(cfg)
    before = asyncio.run(_fetch_counts(db_dsn, "budget-form"))

    # Corrupt just this document's provenance sidecar sha so it no longer matches
    # frontmatter's content_sha256 -- the sha-mismatch KbFileError.
    sidecar_path = kb_dir / "budget-form.provenance.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["content_sha256"] = "0" * 64
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    # Force a reindex attempt (kb_sha256 of the .md is unchanged, but --force makes sure
    # the corrupted sidecar is actually parsed rather than skipped as "unchanged").
    result = _run(cfg, force=True)

    assert len(result.errors) == 1
    assert result.errors[0][0].name == "budget-form.md"
    assert "sha mismatch" in result.errors[0][1]

    after = asyncio.run(_fetch_counts(db_dsn, "budget-form"))
    assert before == after


def test_reindex_all_after_changing_embed_model_rebuilds_and_updates_meta(
    tmp_path: Path, db_dsn: str
) -> None:
    _copy_fixtures(tmp_path / "kb")
    cfg = _cfg(tmp_path, db_dsn)
    _run(cfg)

    new_cfg = _cfg(tmp_path, db_dsn, embed_model="fake-embedder-v2", embed_dim=EMBED_DIM)
    result = _run(new_cfg, reindex_all=True)
    assert result.reindexed == 4
    assert result.errors == []

    meta = asyncio.run(_index_meta(db_dsn))
    assert meta["embed_model"] == "fake-embedder-v2"
    assert meta["embed_dim"] == str(EMBED_DIM)


def test_mismatched_embed_model_without_reindex_all_exits_with_both_names(
    tmp_path: Path, db_dsn: str
) -> None:
    _copy_fixtures(tmp_path / "kb")
    cfg = _cfg(tmp_path, db_dsn)
    _run(cfg)

    mismatched_cfg = _cfg(tmp_path, db_dsn, embed_model="a-different-model", embed_dim=EMBED_DIM)
    with pytest.raises(index_module.IndexConfigError) as excinfo:
        _run(mismatched_cfg)
    message = str(excinfo.value)
    assert "fake-embedder" in message
    assert "a-different-model" in message
    assert "--reindex-all" in message
