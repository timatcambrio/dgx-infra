# CONTINUITY — dgx-infra

Compressed 2026-09-18. Full history through that date, verbatim, in
`.agent/archive/CONTINUITY-2026-09-18.md`; proxy-corpus reasoning in `.agent/PROXY-CORPUS.md`.
Facts only; ISO date + provenance tag; `UNCONFIRMED` where unknown. Project-level decisions
live in `../.agent/CONTINUITY.md`; this file is the code repo's own briefing.

## [PLANS]
- 2026-09-18 [USER] Next (after eval floor and table-row chunking, both DONE): embedding-model
  comparison on the DGX against the recorded floor; optional S5 (catalog summaries via ollama,
  gated on `models.yaml`, needs a go); `get_outline` paging/depth limit; `mcp` 2.x (later).
  Eval cases: `~/Dropbox/Cambrio/dgx-eval/proxy-cases.yaml` (outside the repo).
- 2026-09-15 [USER] Standing rules for builders: `make check` is the verification command
  (`WORK_DIR=/private/tmp/dgx-empty-work` while the Marker cache is in `dgx-knowledge/work`);
  README is written for a user of the pipeline, not a maintainer; no milestone vocabulary in
  user-facing output; public docs never name agent involvement or corpus file names; failing
  tests first, then the fix, then golden regeneration in a separate commit; fixtures synthetic.
- 2026-09-15 [CODE] Open questions carried from the old README: LibreOffice major version pin
  (UNCONFIRMED, `.doc` path unexercised); whether other CSVs are per-row records; whether real
  documents carry a discoverable `doc_date`.

## [DECISIONS]
- 2026-09-24 [DECISION] **FETCH_MAX_CHARS is a ceiling on every fetch path, and a section
  is not a bounded unit.** `_oversize_response` (retrieval/server.py) answers an over-cap
  section, section range, page or chunk the way `_fetch_document` has always answered an
  over-cap document: `truncated: true`, and in place of the text the block range declined
  plus the chunk ids covering it *as an index range* (every index in it a valid id, so the
  reply does not grow with the section) and one landmark line from a dozen sampled chunks.
  Why it was needed: `cfg.fetch_max_chars` was read in one place only, and `sec:daffars:523`
  = 538,454 chars / `sec:gsam:70` = 513,530 came back whole — 2.5–2.7× the cap at which the
  document path refuses, whose refusal said "fetch the section ids below instead", so
  following the server's own advice returned more than it had just declined. One DOCX
  heading spans 1,162 blocks. Consequences, all deliberate: (1) a `chunk:` fetch returns
  that chunk alone instead of widening to its section — the chunk is the only sub-unit
  below a section in these page-less documents, so a widening chunk id would loop the
  breakdown back to itself; `search` still returns section ids only and `get_section` is
  still the wider read; (2) `metadata.kind` gains `"chunk"`, a fourth value where brief
  §6.5.2 lists three; (3) the document refusal now prints each section's size and marks the
  over-cap ones, and only offers a page id when the range spans more than one page (one
  page containing an over-cap unit is at least as large as it); (4) the outline listing is
  bounded too — deeper heading levels drop out before the list is cut, and both say so.
  Tests d5674df (failing first, a generated 320,000-char fixture document), fix 5de0de0.
- 2026-09-23 [DECISION] **A heading is never one cell of a row, whatever size it is set in.**
  `pdf_geometry._reads_as_cell` refuses heading promotion for a line that a neighbour
  corroborates as part of a row: `_shares_columns` (the line directly above or below splits
  into the same number of cells at the same left edges, set in the same size and weight) or
  `_side_by_side` (a line horizontally disjoint from it overlapping it by ≥50% of the
  shorter one's height). Both conditions are measurements; neither asserts a table.
  Corroboration is what keeps the rule off a numbered heading — `1.1<tab>Purpose` splits into
  two cells like any row, and matching type is what keeps it off `4 L1 PROCESSOR PARAMETERS`
  followed by `4.1 Overview`, which share a tab stop but never a size. The asymmetry is
  deliberate: two rows are too little evidence to *reconstruct* a grid (`PDF_MIN_TABLE_ROWS`
  stays 3) and enough to decline to call one of them a section title, because refusing a
  heading costs a `#` while asserting a table costs the text.
- 2026-09-23 [DECISION] **A line never crosses a gutter.** Where `find_column_gutter` (the
  old `detect_columns` body, now returning the gutter's edges) finds one, `to_blocks` groups
  words into lines per column and emits the left column first; `_render_page` orders by the
  gutter instead of the page midpoint, which was wrong whenever the gutter was not at 50%.
  No setting changed. Nothing was tuned: `PDF_HEADING_SIZE_RATIO`, `PDF_MIN_TABLE_ROWS`,
  chunk sizes, `k` and the RRF constant are all untouched.
- 2026-09-22 [DECISION] **GPU passthrough decided outside Compose.** `compose/gpu-detect.sh`
  prints the `-f compose/docker-compose.gpu.yml` overlay (`driver: nvidia`, `count: all`,
  `capabilities: [gpu]`) when `nvidia-smi` lists a GPU AND Docker can pass one through
  (`nvidia` runtime registered, or `nvidia-ctk` present for CDI installs); `KB_GPU=auto|on|
  off` from the environment, else parsed out of `.env` for that one key. stdout is Compose
  arguments only, verdict on stderr, `--quiet` for the `$(shell)` call. `gpu-check` is a
  real recipe because `$(shell ...)` swallows a non-zero exit, so `KB_GPU=on` needs it to
  stop a deploy. `COMPOSE` became recursive (`=`) so `make help` does not probe. Tested with
  fake `nvidia-smi`/`docker` on PATH plus `docker compose config` assertions that the
  overlay changes the reservation and nothing else (`tests/test_compose_gpu.py`).
- 2026-09-18 [DECISION] **Table-row chunking** (`retrieval/chunk.py` rule 1): a table at or
  under `CHUNK_MAX` is never split; above it a pipe table (header row + GFM delimiter row)
  is cut only between rows, each piece = header + delimiter + previous piece's last row +
  rows filled greedily to `CHUNK_MAX`, at least one new row per piece (a row over
  `CHUNK_MAX` stays whole), lines not starting with `|` continue the row before them, the
  table's ordinal is kept on every piece, only the last piece stays open for annotation
  gluing; a `table` block with no pipe rows splits at blank lines like rule 3. Why: the
  old "one oversized chunk" reached 500k chars and the embed client ranks such a chunk on
  the prefix it embeds. Section fetch is unaffected.
- 2026-09-15 [USER] **Objective:** answerability by a frontier model, not fidelity. The converter
  preserves evidence and does not resolve it (emit which cell a callout tip lands in, never
  which label it "means").
- 2026-09-15 [DECISION] Evidence vocabulary: annotations render `[points to: X]` / `[beside: X]`;
  flattened callouts render `> **Boxed text:**` (page text in a rect, no annotation provenance);
  a rect overlapping a ruled table is never a note box; image-dominant pages are reported
  (`IMAGE_PAGE_COVERAGE`), never reclassified; notes suppressed where they would misattribute.
- 2026-09-15 [DECISION] Answerability eval has no model in the loop: literal substring cases over
  converted markdown; an unusable case file raises. `pipeline answerability --cases PATH`.
- 2026-09-15 [DECISION] The released sample is release-filtered; proxy selection targets coverage
  of the plausible population (annotated, filled, scanned, redacted, tracked-changes), not
  resemblance to the sample. Keep annotation/widget machinery even if the sample shows none.
- 2026-09-15 [CODE] Docling has one live call site: DOCX via `MsWordDocumentBackend` driven
  directly (never `DocumentConverter`, which imports the PDF backend). PDF Docling escalation is
  a stub pending `--extra pdf` and the layout-model provenance ruling.
- 2026-09-18 [DECISION] Stage 2 (see `../stage2-retrieval-brief.md`): chunks find, sections are
  read; tables never split, and they keep the annotations/boxed text that follow them; ids
  `sec:/page:/doc:/chunk:`; `mcp>=1.28,<2`; bearer auth is a plain ASGI middleware; roles are
  created only by `compose/init-db.sh` (no CREATEROLE); `KB_PATH` must be absolute for compose.
- 2026-09-18 [DECISION] Word/CSV sidecars: `page: null`, no bbox, ids `<slug>:p000:bNNN` counting
  from 1 like PDF; Word Heading *n* → `#`×n literally; unfamiliar docling items keep their label
  in `confidence` as `docling:<label>`; embedded pictures → `picture` block + INCOMPLETE note +
  `conversion.images`; `office.convert()`/`csv_table.convert()` keep their 2-tuple signatures
  and delegate to `convert_with_provenance`, which returns `(body, converter, provenance,
  extras)`; `extras` flow through `_dispatch` into the manifest's `conversion` record.

## [DISCOVERIES]
- 2026-09-23 [TOOL] **The slide table was never detected as a table, and could not have
  been.** `pebp-…-hsa-education-101-slide-deck.pdf` p11 (960×540): `find_tables` returns 0
  — the table has two horizontal rules and no verticals — and `find_aligned_table_runs`
  returns none, because the year cell is set 28pt beside 24pt values so its top sits 3.4pt
  from theirs (over `PDF_LINE_TOLERANCE` 3.0) and the row does not group into one line,
  while the header is stacked `SINGLE`/`PLAN` over two lines. No three consecutive lines
  share a cell count. Lowering `PDF_MIN_TABLE_ROWS` to 2 would not have recovered it and
  would make hallucinated tables likelier, so it was left at 3. What was wrong was the
  fallback: every cell cleared a heading test (24 and 28pt over 18pt body × 1.15; the 20pt
  bold header via the bold-and-at-least-body fallback) and the slide became 8 blocks, 7 of
  them headings.
- 2026-09-23 [TOOL] **`detect_columns` said 2 while lines were built across the gutter.**
  Page 11's gutter is at x 555–622 on a 960pt page; words were grouped into lines by
  baseline over the whole page, so the table cell `2026` and the callout fragment `2026!`
  became the line `2026 2026!` — text neither column contains. `_render_page` then split
  columns at the page *midpoint* (480), which puts `$8,550` (centre 502) in the right-hand
  column. Both fixed; fixture `slide_table.pdf` reproduces the shape, callout column
  included.
- 2026-09-23 [TOOL] **Two false-positive classes found by running the real corpus, not by
  the fixtures.** A first version demoted any multi-cell line: it killed `1.1 Purpose` and
  every numbered heading in the Sentinel spec (236 → 113 headings), because a tab between
  number and title opens a table-width gap. Requiring an adjacent line with the same
  columns still killed `2 DOCUMENTS` / `2.1 Applicable Documents` pairs. Requiring matching
  size and weight as well leaves the spec at 236 headings — exactly what it had before, the
  only five differences being TOC lines whose dotted leaders now collapse to `…`. Lesson: a
  synthetic fixture cannot find this class; convert the corpus and diff the heading lists.
- 2026-09-23 [TOOL] **Retrieval-side measurement of the slide-table defect** (root cause in
  the three entries above, fixed the same day): `kb eval` scored `hsa-2026-limits` a miss on
  all three legs although the top two fused hits were the right document and the expected
  page 11 — `$8,750` sat alone in section 32 while sections 31 and 34 ranked. The answer's
  section competed with seven near-identical one-line siblings. Retrieval was correct.
- 2026-09-23 [TOOL] **`expected_phrase` + `expected_page` can over-constrain a case, but do
  not here.** Both must be satisfied by the SAME section, and section boundaries are an
  implementation artifact any chunking change moves, so such a case measures the sectioniser
  as much as the retriever. `scripts/check_eval_cases.py` checks for exactly this and finds
  **0 instances in the 21 proxy cases**: all five all-leg misses (`hsa-2026-limits`,
  `newhire-supporting-documents`, `cfap-egg-form-part`, `almanac-officer-accessions`,
  `pca-share-a76`) have a correct phrase and a correct page that are jointly satisfiable —
  `hsa-2026-limits`'s section 32 holds both. They were genuine retrieval misses, not unfair
  cases. SUPERSEDES an earlier claim on the `eval-case-check-and-mcp-stdio-path` branch that
  these five were over-constrained; the checker written afterwards disproved it.
- 2026-09-23 [TOOL] **The lexical 33% is mostly question wording, not retrieval quality.**
  `websearch_to_tsquery` ANDs every unquoted term, so a long natural-language question
  fails the lexical leg whenever any one word is absent from the target section. Already
  noted for the fixtures in `eval/retrieval.yaml`'s header; it applies to the proxy cases
  too and means the lexical column should not be read as a quality score.
- 2026-09-18 [TOOL] **Embedding time is per character, not per text.** Host ollama 0.21 +
  nomic-embed-text on the Intel dev Mac: 1 × 2,358 chars = 1.0 s, 8 = 7.2 s, 32 = 62.6 s,
  over the 60 s `httpx` read timeout; `make index --force` failed twice on the regulation
  DOCX once its 499k-char table became ~200 pieces of ≤2,500 chars. Fix (tests b49b02e, fix 269d685): `embed.py` batches ≤ 32 texts AND ≤ `BATCH_MAX_CHARS` = 40,000 (a
  longer single text goes alone); truncation positions now use a running offset (the old
  `start * BATCH_SIZE` assumed fixed-size batches).
- 2026-09-18 [TOOL] **Real-corpus Word failures and their fixes** (each an invariant with a
  synthetic fixture): (1) Docling's `_walk_linear` crashes on XML comment/PI nodes
  (`etree.QName` on a comment) → `office._strip_non_element_nodes` removes them from every
  WordprocessingML part, reached via the package (section accessors would create parts).
  (2) OPC relationships targeting a directory (`media/`) or a never-packaged part crash
  python-docx → `office._sanitise_package` re-marks them `TargetMode="External"` in memory;
  count in `conversion.dangling_relationships`, INCOMPLETE note, EVIDENCE line. (3) Docling
  drops pictures ≤ 25 px² as spacers (fixture image is 6×6). (4) A 1.6 MB regulation DOCX
  (~39k blocks) takes ~10 min in Docling's walk on the dev Mac; removed from the sample.
- 2026-09-18 [TOOL] **ollama 0.21 + nomic-embed-text:** `truncate: true` does not prevent
  "input length exceeds the context length" on a ~4k-char dotted-leader chunk (100k chars of
  plain words are accepted). Model header says context 2048, modelfile `num_ctx 8192`; rule not
  determined (no tokenize endpoint). `retrieval/embed.py` raises 4xx at once with ollama's
  reason and shrinks a context-refused text by 0.75/step for embedding only (floor 256 →
  error), reported, logged with chunk identity, counted in the summary line.
- 2026-09-18 [USER→TOOL] Dotted leaders DONE (commits 47f840d tests, 37d8661 fix, 8481e1d
  golden): `normalize_markdown` collapses runs of >=4 dots to " … " and splits a line holding
  >=3 "… page" entries into one entry per line (`TOC_MIN_ENTRIES`); fixture `toc_page.pdf`.
  The real report's TOC block went from 3,917 chars / 3,020 dots on one line to 975 chars /
  30 lines. Table-row chunking for huge tables: agreed in principle, to be done conservatively
  (only above a size threshold, split at row boundaries only, header row repeated, one-row
  overlap, non-table-shaped "table" blocks split at blank lines). NOT started.
- 2026-09-18 [TOOL] Compose: `${VAR:?}` breaks the dev path (interpolation is file-wide);
  relative bind-mount sources resolve against `compose/`; `sed -i` fails on a `:ro` mount;
  Caddyfile splits unquoted arguments on spaces (quote the substituted regex); `ollama/ollama`
  has no curl/wget (healthcheck uses `ollama list`).
- 2026-09-18 [TOOL] FastMCP 1.x facts used: `FastMCP(name, instructions=, lifespan=, host=, port=,
  streamable_http_path=, json_response=, stateless_http=, transport_security=)`;
  `@mcp.tool(annotations=ToolAnnotations(...))` on `async def -> TypedDict` yields
  `outputSchema` + `structuredContent` + a JSON text block; `mcp.streamable_http_app()`
  declares its own lifespan that enters `session_manager.run()`; `@mcp.custom_route`;
  `create_connected_server_and_client_session` for tests; `mcp.client.stdio` /
  `mcp.client.streamable_http` clients.
- 2026-09-15 [TOOL] Geometry vs raw text (pypdfium2): decisive for annotations/widgets/image
  coverage (not in the text layer at all), real for multi-column order, modest for boxed text,
  marginal for heading levels and table pipes. Proposed, not approved: a raw-text baseline
  engine measured by the answerability cases.
- 2026-09-15 [TOOL] Improper redaction (opaque rect over live text) is extracted and promoted to
  a boxed-text block: disclosure concern; remedy UNDECIDED (options in PROXY-CORPUS.md).
- 2026-09-15 [TOOL] A form supplied as a screenshot with typed callouts passes every text metric;
  image coverage is the only measurement that separates it (hence EVIDENCE NOTES).
- 2026-09-15 [TOOL] Cell containment uses zero tolerance (cells tile with no gaps); a PDF's ruling
  is a layout grid, not a field map — a tip's cell is evidence, not the field.
- 2026-09-14 [TOOL] Word-splitting root cause: `extract_words(extra_attrs=[size, fontname])`
  splits on font-subset changes with zero gap; gap/size ratio is bimodal (≤0.001 vs ≥0.2).
  Over-heading root cause: `_heading_level` used a 1.001 ratio against the 1.15 config.
  `/CL` is in PDF user space (y up); widget `/T` arrives as bytes; ReportLab needs a
  `FreeTextAnnotation` subclass to emit `CL`.
- 2026-09-15 [CODE] Known, not fixed: `_rejoin_fragments` drops a real space in one NIFA case;
  annotation ordering caveat in the README; `report.py`/`triage.py`/`model_gate.py` docstrings
  still say M1/M2.

## [PROGRESS]
- 2026-09-24 [TOOL] **Fetch size ceiling applied to every path** (branch
  `fix/fetch-size-ceiling-all-paths`, tests d5674df, fix 5de0de0; see [DECISIONS]).
  `fetch("sec:daffars:523")` 540,776 → 2,330 chars; `sec:gsam:70` → 2,100. 505 tests green
  (500 before). `scripts/mcp_probe.py` against `~/Dropbox/Cambrio/dgx-eval/mcp-cases.yaml`
  unchanged at 69 mechanical checks with the same single failure as before the change
  (`intact-table-captains`, evidence `5,913` absent from a 310-char section fetch —
  pre-existing, a sectioniser/retrieval matter, not a size matter). Retrieval ranking is
  untouched, so the 36-case eval floor is unaffected and was not re-run.
- [MILESTONE] 2026-09-22 GPU passthrough for the compose stack: tests e55bc31, fix ccabf4d.
  488 tests green, both gates green.
- [MILESTONE] 2026-09-18 Table-row chunking: tests fcac158, fix 9224229, golden 5d2f4f1
  (handbook fixture 66 → 67 chunks), README dd3af7c. Eval floor recorded f153cb5.
- [MILESTONE] 2026-09-18 Stage 2 S0–S4 shipped (commits 3ed6f8c and predecessors), plus
  hardening: error isolation in `convert` (`conversion_error`, exit 1, report section),
  `--only` case-insensitive substring-or-glob, `pipeline prune` (dry run; `--yes`), one-line
  Postgres-unreachable messages, `pretty_exceptions_show_locals=False` on both CLIs, embed
  client context-overflow handling. 454 tests.
- [MILESTONE] 2026-09-18 Stage 1 sidecar addendum shipped in three commits (tests bed3417,
  implementation 73c9eab, goldens f77b052) + one-based ids (367ecef, df524bb) + XML-comment
  and dangling-relationship fixes.
- [MILESTONE] 2026-09-17 PDF block provenance (anchors + sidecars).
- [MILESTONE] 2026-09-15 Five-item plan done: table-cell targets, evidence profile + EVIDENCE
  NOTES, `[points to]/[beside]` vocabulary, boxed-text separation, answerability eval;
  README rewritten for users; `make profile`; NIFA document removed from sample-data.
- [MILESTONE] 2026-09-14 Annotation field binding; over-heading and word-splitting defects fixed
  with `callout_notes.pdf` fixture; conftest scrubs `PDF_*` env vars.

## [OUTCOMES]
- 2026-09-23 [TOOL] **Slide-table fix measured.** `WORK_DIR=/private/tmp/dgx-empty-work make
  check` green at 500 tests (was 488; +12, and `slide_table.md` golden added). Corpus
  re-converted and re-indexed (8 of 12 reindexed). `kb eval` on the 21 proxy cases:
  lexical 33% / vector 67% / fused 76% — **unchanged in total**. `hsa-2026-limits` went from
  a miss on all three legs to vector 1 / fused 1: p11 is now 2 sections, and `$8,750`, `2026`
  and `$4,400` sit in one block. `forms-equipment-threshold` went the other way, from a
  fused hit to fused rank 6; its target section is byte-identical, a one-line heading
  section (`### List items and dollar amount for each item exceeding $5,000`, p10 b002 to
  b002), displaced by two new sections on p11 of the same form that appeared when the form's
  row-label lines were demoted. That shape — a form's labels promoted to headings, each its
  own one-line section — is the remaining conversion defect behind three of the five
  standing misses. NOT chased: nothing was tuned toward the score.
- 2026-09-23 [TOOL] Heading counts before → after across the proxy corpus, with distinct
  heading texts lost/gained: `27.5051` presentation 183 → 142 (47/6), HSA deck 84 → 75
  (10/1), new-hire deck 70 → 61 (9/0), `Ch 05_b` 74 → 71 (4/1), 2023 report 71 → 70 (1/0),
  annotated forms 112 → 113 (1/2), Sentinel spec 236 → 236 (5/5, the same five TOC lines
  either side of the leader collapse). Every drop is on a deck or a chart page; the two
  regulations (Word) and the specification are untouched.
- 2026-09-18 [TOOL] Proxy-corpus eval, 21 cases, chunker before row splitting: lexical 33%,
  vector 67%, fused 76% hit@5 (README "Recorded floor"). After row splitting + bounded batches, re-embedded
  (12 reindexed, 18 truncations): identical rates, CSV case vector rank 3 → 1; 3,854
  chunks, largest 39,063 chars; 611 over `CHUNK_MAX`, 392 of them one-row pieces of the
  regulation DOCX's cell-padded table (header 1,303 + overlap 1,303 + row 1,303).
- 2026-09-18 [TOOL] `make check` green: 454 passed with the DB up (Stage 2 tests skip cleanly
  without it); both gates pass with `WORK_DIR=/private/tmp/dgx-empty-work`.
- 2026-09-18 [TOOL] Compose stack verified end to end on the dev Mac: `/health` over HTTPS,
  `/kb/*` 401 without token / 200 with, `compose-index` reaches ollama and fails clearly when
  the model is not pulled; image ~600 MB.
