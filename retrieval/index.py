"""`kb index` — idempotent load of `kb/` into Postgres (brief §6.2).

Kept free of `typer`/CLI concerns: `cli.py` calls `run_index` and prints the result.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import asyncpg
import httpx
from pgvector.asyncpg import register_vector

from .chunk import build_chunks
from .config import Config
from .embed import embed_documents
from .kbfiles import KbFileError, load_document
from .sections import build_sections

_log = logging.getLogger(__name__)

CONTENT_TABLES = ("chunks", "sections", "blocks", "documents")


class IndexConfigError(RuntimeError):
    """`index_meta` disagrees with the current config, or the schema was never applied."""


@dataclass
class IndexResult:
    unchanged: int = 0
    reindexed: int = 0
    deleted: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)
    #: Chunks whose text had to be shortened for the embedding model (embed.py). The stored
    #: text is intact; only the vector saw a prefix. Counted so it is never silent.
    embeddings_truncated: int = 0

    @property
    def summary_line(self) -> str:
        line = (
            f"{self.unchanged} unchanged, {self.reindexed} reindexed, "
            f"{self.deleted} deleted, {len(self.errors)} errors"
        )
        if self.embeddings_truncated:
            line += f", {self.embeddings_truncated} embeddings truncated (see stderr)"
        return line


async def _check_index_meta(conn: asyncpg.Connection, cfg: Config, *, reindex_all: bool) -> None:
    rows = await conn.fetch("SELECT key, value FROM index_meta")
    meta = {r["key"]: r["value"] for r in rows}
    if not meta:
        raise IndexConfigError(
            "index_meta is empty: run `kb index --init` first to apply the schema."
        )

    mismatch = meta.get("embed_model") != cfg.embed_model or meta.get("embed_dim") != str(
        cfg.embed_dim
    )
    if mismatch and not reindex_all:
        raise IndexConfigError(
            f"index_meta says embed_model={meta.get('embed_model')!r} "
            f"embed_dim={meta.get('embed_dim')!r}, but config says "
            f"embed_model={cfg.embed_model!r} embed_dim={cfg.embed_dim!r}. Run "
            f"`kb index --reindex-all` to rebuild with the new model."
        )


async def _reindex_all_reset(conn: asyncpg.Connection, cfg: Config) -> None:
    async with conn.transaction():
        await conn.execute(f"TRUNCATE {', '.join(CONTENT_TABLES)}")
        for key, value in (
            ("embed_model", cfg.embed_model),
            ("embed_dim", str(cfg.embed_dim)),
        ):
            await conn.execute(
                "INSERT INTO index_meta (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                key,
                value,
            )


async def run_index(
    cfg: Config,
    *,
    force: bool = False,
    reindex_all: bool = False,
    embed_client: Optional[httpx.Client] = None,
) -> IndexResult:
    dsn = cfg.require_database_url_index()
    conn = await asyncpg.connect(dsn)
    try:
        await register_vector(conn)

        await _check_index_meta(conn, cfg, reindex_all=reindex_all)
        if reindex_all:
            await _reindex_all_reset(conn, cfg)

        files = sorted(cfg.kb_dir.glob("**/*.md"))
        existing_rows = await conn.fetch("SELECT slug, kb_sha256 FROM documents")
        existing = {r["slug"]: r["kb_sha256"] for r in existing_rows}

        result = IndexResult()
        seen: set[str] = set()

        for f in files:
            slug = f.stem
            seen.add(slug)
            kb_sha = hashlib.sha256(f.read_bytes()).hexdigest()
            if not force and not reindex_all and existing.get(slug) == kb_sha:
                result.unchanged += 1
                continue

            try:
                doc = load_document(f)
            except KbFileError as exc:
                result.errors.append((f, str(exc)))
                continue

            sections = build_sections(doc)
            chunks = build_chunks(sections, cfg.chunk_target, cfg.chunk_max)
            section_by_index = {s.index: s for s in sections}

            vectors: list[list[float]] = []
            if chunks:
                texts = [
                    f"{section_by_index[c.section_index].heading_path}\n\n{c.text}"
                    for c in chunks
                ]
                truncations: list = []
                vectors = embed_documents(
                    texts,
                    base_url=cfg.ollama_base_url,
                    model=cfg.embed_model,
                    embed_dim=cfg.embed_dim,
                    client=embed_client,
                    truncations=truncations,
                )
                for position, original, kept in truncations:
                    chunk = chunks[position]
                    _log.warning(
                        "%s chunk %d (section %d, blocks %d-%d): embedded the first %d of %d "
                        "characters; the model refused the whole text as over its context",
                        slug, position, chunk.section_index,
                        chunk.block_first, chunk.block_last, kept, original,
                    )
                result.embeddings_truncated += len(truncations)

            outline = [
                {
                    "section_index": s.index,
                    "level": s.level,
                    "heading": s.heading,
                    "page_first": s.page_first,
                    "page_last": s.page_last,
                }
                for s in sections
            ]

            async with conn.transaction():
                await conn.execute("DELETE FROM documents WHERE slug = $1", slug)
                await conn.execute(
                    """
                    INSERT INTO documents (
                        slug, rel_path, title, source_file, source_format, source_url,
                        doc_date, text_class, needs_ocr, has_sidecar, page_count, chars,
                        incomplete_pages, kb_sha256, content_sha256, outline
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                    """,
                    slug,
                    f"kb/{f.name}",
                    doc.meta["title"],
                    doc.meta.get("source_file"),
                    doc.meta.get("source_format"),
                    doc.meta.get("source_url"),
                    doc.meta["doc_date"],
                    doc.meta["text_class"],
                    doc.meta["needs_ocr"],
                    doc.has_sidecar,
                    doc.page_count,
                    doc.chars,
                    doc.incomplete_pages,
                    kb_sha,
                    doc.meta["content_sha256"],
                    json.dumps(outline),
                )
                if doc.blocks:
                    await conn.executemany(
                        """
                        INSERT INTO blocks (slug, ordinal, block_id, page, kind,
                            confidence, bbox, text)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                        """,
                        [
                            (slug, b.ordinal, b.block_id, b.page, b.kind, b.confidence, b.bbox, b.text)
                            for b in doc.blocks
                        ],
                    )
                if sections:
                    await conn.executemany(
                        """
                        INSERT INTO sections (slug, section_index, level, heading,
                            heading_path, block_first, block_last, page_first, page_last)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                        """,
                        [
                            (
                                slug,
                                s.index,
                                s.level,
                                s.heading,
                                s.heading_path,
                                s.block_first,
                                s.block_last,
                                s.page_first,
                                s.page_last,
                            )
                            for s in sections
                        ],
                    )
                if chunks:
                    await conn.executemany(
                        """
                        INSERT INTO chunks (slug, chunk_index, section_index, block_first,
                            block_last, page_first, page_last, text, embedding)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                        """,
                        [
                            (
                                slug,
                                i,
                                c.section_index,
                                c.block_first,
                                c.block_last,
                                c.page_first,
                                c.page_last,
                                c.text,
                                vectors[i],
                            )
                            for i, c in enumerate(chunks)
                        ],
                    )
            result.reindexed += 1

        gone = set(existing) - seen
        for slug in gone:
            await conn.execute("DELETE FROM documents WHERE slug = $1", slug)
            result.deleted += 1

        if result.reindexed > 0:
            await conn.execute("ANALYZE chunks")

        return result
    finally:
        await conn.close()
