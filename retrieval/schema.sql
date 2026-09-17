-- Stage 2 schema (brief §6.1). Applied by `kb index --init`, idempotently.
--
-- `{EMBED_DIM}` is substituted with the configured EMBED_DIM by `db.py` before this file is
-- executed. Section text is not stored anywhere: it is assembled from `blocks` on fetch, so
-- there is exactly one copy of every word.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS index_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);   -- rows: embed_model, embed_dim, schema_version='1'

CREATE TABLE IF NOT EXISTS documents (
  slug TEXT PRIMARY KEY,
  rel_path TEXT NOT NULL,             -- 'kb/<slug>.md'
  title TEXT NOT NULL,
  source_file TEXT, source_format TEXT, source_url TEXT,
  doc_date TEXT NOT NULL,             -- ISO date or 'UNCONFIRMED', verbatim
  text_class TEXT NOT NULL,
  needs_ocr BOOLEAN NOT NULL,
  has_sidecar BOOLEAN NOT NULL,
  page_count INT,
  chars INT NOT NULL,
  incomplete_pages INT[] NOT NULL DEFAULT '{}',
  kb_sha256 TEXT NOT NULL,            -- sha256 of the .md file bytes: idempotency key
  content_sha256 TEXT NOT NULL,       -- from frontmatter: the SOURCE bytes
  outline JSONB NOT NULL,             -- [{"section_index","level","heading","page_first","page_last"}]
  summary TEXT,
  indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS blocks (
  slug TEXT NOT NULL REFERENCES documents ON DELETE CASCADE,
  ordinal INT NOT NULL,
  block_id TEXT NOT NULL UNIQUE,
  page INT, kind TEXT NOT NULL, confidence TEXT, bbox REAL[],
  text TEXT NOT NULL,
  PRIMARY KEY (slug, ordinal)
);

CREATE TABLE IF NOT EXISTS sections (
  slug TEXT NOT NULL REFERENCES documents ON DELETE CASCADE,
  section_index INT NOT NULL,
  level INT NOT NULL, heading TEXT, heading_path TEXT NOT NULL,
  block_first INT NOT NULL, block_last INT NOT NULL,
  page_first INT, page_last INT,
  PRIMARY KEY (slug, section_index)
);

CREATE TABLE IF NOT EXISTS chunks (
  slug TEXT NOT NULL REFERENCES documents ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  section_index INT NOT NULL,
  block_first INT NOT NULL, block_last INT NOT NULL,
  page_first INT, page_last INT,
  text TEXT NOT NULL,
  tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  embedding VECTOR({EMBED_DIM}) NOT NULL,
  PRIMARY KEY (slug, chunk_index)
);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_section_idx ON chunks (slug, section_index);
