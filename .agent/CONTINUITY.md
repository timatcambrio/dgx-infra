# CONTINUITY — dgx-infra

Compressed 2026-09-18. Full history through that date, verbatim, in
`.agent/archive/CONTINUITY-2026-09-18.md`; proxy-corpus reasoning in `.agent/PROXY-CORPUS.md`.
Facts only; ISO date + provenance tag; `UNCONFIRMED` where unknown. Project-level decisions
live in `../.agent/CONTINUITY.md`; this file is the code repo's own briefing.

## [PLANS]
- 2026-10-07 [USER] Rewrote the DGX runbook for operators as `docs/deployment-dgx-guide.md`
  (old `docs/deployment-dgx.md` kept, not yet removed). Decisions: own self-signed cert via
  `scripts/make_tls_cert.sh` only, no client-CA path; DGX assumed online; `EMBED_MODEL` pinned
  to `nomic-embed-text:v1.5` (same digest `0a109f422b47` as `latest` on 2026-10-07 [TOOL]);
  full probe runs on the host only (23 checks), client machine uses curl plus Codex; test on a
  few documents first, then load the full set (inventory, prune, convert, report, index).
  Codex over HTTPS with that cert: see the 2026-10-07 entry below (SUPERSEDES "never run").
- 2026-10-07 [USER] Codex (ChatGPT desktop, Tim's Mac) against the AWS stack over HTTPS with
  the `make_tls_cert.sh` self-signed cert: before trusting the cert in the System keychain,
  Codex reported no tools for the server; after `security add-trusted-cert`, it searched and
  answered. So Codex verifies certificates and accepts this one once trusted; Caddy's
  `tls internal` fallback was not needed. Windows step 14 remains UNCONFIRMED (not run).
  AWS instance to be terminated.
- 2026-10-07 [USER] Guide follow-ups: step 14 has Mac and Windows versions (some users are on
  Windows; the first user is probably on a Mac); second computer is assumed to SSH to the DGX;
  operator may work at the DGX console or over SSH; contact is "the Cambrio team"; the old
  `docs/deployment-dgx.md` is kept. Plan: rehearse the guide end to end on a fresh AWS GPU
  instance, including step 14 from Tim's Mac, before the on-site day.
- 2026-10-07 [CODE] `bootstrap_host.sh` fixed: when it adds the user to `docker`, the
  passthrough check now runs via sudo (membership applies only at next login, so plain
  `docker run` was refused and reported as a GPU fault); `--check` without the group fails
  naming the group. Pinned by `tests/test_bootstrap_host.py`. `make check`: 663 passed,
  2 skipped (Stage 2 db tests ran; WORK_DIR=/private/tmp/dgx-empty-work).
- 2026-10-06 [TOOL] **The in-container conversion ownership question is DECIDED, and only
  one half of it still wants a Linux host.** The conversion run passes
  `--user "$(id -u):$(id -g)" -e UV_CACHE_DIR=/tmp/uv-cache-"$(id -u)"`, so the markdown is
  written as the login user into a tree that belongs to the login user, and step 5's rule —
  the content tree is yours, uid 10001 only has to READ it — needs no relaxing at all. This
  SUPERSEDES the group-grant (`chgrp -R 10001` + `g+rwX`) that `docs/deployment-dgx.md`
  first documented, which would have left the output owned by a uid the operator cannot
  edit as. Measured on the dev Mac: all four `pipeline` commands run under an overridden
  uid, output comes out mode 644, and the cache override is **required** rather than
  belt-and-braces — without it `uv` dies at `failed to open file
  /tmp/uv-cache/CACHEDIR.TAG: Permission denied`, because the Dockerfile chowns that
  directory to 10001. `-e HOME` is NOT needed; an earlier run that set both led to thinking
  it was.
  **CONFIRMED on Linux 2026-10-07** (the AWS g4dn host, `ubuntu` 1000:1000, umask 0002),
  which is what the Mac could not show: all four `pipeline` commands pass under `--user`,
  everything written is owned by the login user, files come out 644 and directories 755 or
  775, every directory is traversable, and the operator can still edit the output
  afterwards — so git, rsync and re-conversion all work and uid 10001 can serve what was
  written. The same run also confirmed the premise: as the image's own uid, into a tree
  owned by the login user, it is refused. This question is closed.
- 2026-10-07 [USER] **SUPERSEDED, investigation closed by decision.** The entry below
  named one experiment as decisive; five were run, all five eliminated their hypothesis,
  and the cause is not established. Both measurements are now recorded side by side in the
  README with their conditions. Not to be reopened without a new idea; the ideas not yet
  tried are listed in [DISCOVERIES] 2026-10-07.
- 2026-10-06 [TOOL] **Open question from the AWS run, and the one experiment that settles
  it: why vector recall is 28 points above the recorded floor.** Measured on the GPU host:
  lexical **33%** (identical to the floor, case for case), vector **86%** (floor 58%),
  fused **89%** (floor 72%), 36 cases, `--k 5`. Three causes are ELIMINATED: the model
  (`nomic-embed-text` digest `0a109f422b47` on both the dev Mac and the host), the text
  (the lexical leg never touches embeddings and did not move by a single case), and the
  code (only `9dbbe9b` touched the retrieval path since the floor, and that is the commit
  the floor was re-recorded for). Two remain: (1) the Mac's floor was depressed by
  something environmental — embed batches there ran 63 s against a 60 s timeout, so a
  partially-embedded index would read exactly like this, low vector with perfect lexical;
  (2) GPU-versus-CPU numerics in ollama, which fits the magnitude far worse. **The
  decisive test: re-run `kb eval` on the dev Mac now, same model, same cases, against a
  freshly built index.** ≈86% there means the recorded floor is wrong and the GPU is not
  the cause; ≈58% means the difference is real and hardware-dependent. Until that runs,
  **the README floor table is deliberately NOT updated** — adopting the flattering number
  without a cause is how a measurement becomes folklore.
- 2026-10-06 [TOOL] **`EMBED_MODEL=nomic-embed-text` is an unpinned tag, and should not
  stay that way for the client.** The deployment pulls whatever `latest` points at, and
  `index_meta` records the model NAME, not its digest, so a retag changes every embedding
  with nothing in the index or the logs to show it. On this run the digests happened to
  match; that was luck, not a guarantee. For an on-prem federal deployment this is a
  reproducibility and supply-chain gap and is the kind of thing the access proposal's §8
  audience asks about. Not yet decided: pin by digest in `.env.example`, record the digest
  in `index_meta`, or both.
- 2026-10-02 [USER] **Next run: the full stack on a fresh AWS GPU instance**, to close the
  three things the 2026-10-01 remote test could not reach. Runbook written and ready to
  execute: `../aws-gpu-deployment-runbook.md`, twelve steps. It closes (1) the real
  embedding model end to end over HTTPS — `nomic-embed-text` pulled and indexed with,
  instead of the deterministic double; (2) GPU passthrough, verified in four places
  (toolkit installed, passthrough proven in a throwaway container, the Compose reservation
  resolving, and `ollama ps` reporting `100% GPU` after an index — that last line is the
  only one that proves the MODEL used the card); (3) `TLS_CERT`/`TLS_KEY` instead of `tls
  internal`, via a self-signed pair standing in for the client's internal CA. It also
  re-runs the proxy-corpus eval with the real model, which would be the first check that
  the recorded 33% / 58% / 72% floor is a property of the pipeline and not of the dev Mac.
  [USER] decisions taken 2026-10-02: Tim launches the instance (no AWS API calls from the
  agent); BOTH corpora, fixtures first then the proxy corpus; self-signed cert via
  TLS_CERT/TLS_KEY; the host gets the code by `git clone` from GitHub.
- 2026-10-01 [TOOL] **What the remote test did NOT cover, and is the next thing to do on real
  hardware.** The run was on a fresh Linux host with no GPU and no access to
  `registry.ollama.ai`, so `nomic-embed-text` could not be pulled and the stack was indexed
  against the repo's own deterministic embedding double (hashes of the text, not
  embeddings). Everything about the *deployment* was therefore exercised — TLS, Caddy, both
  routes, the forward_auth hop, the live token store, revocation, MCP over streamable HTTP,
  indexing, search, fetch, and a citation followed to its document — and **nothing about
  retrieval quality was**: no ranking claim from that run means anything, and the recorded
  33% / 58% / 72% floor was neither re-run nor affected. Still untested anywhere: the real
  embedding model end to end over HTTPS, the GPU passthrough path (`KB_GPU=on`, which this
  host could not exercise), and `TLS_CERT`/`TLS_KEY` instead of `tls internal`.
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
- 2026-10-01 [TOOL→DECISION] **`KB_URL_BASE` is the site root, and `kb-static` stays rooted
  at the corpus.** The doubled `/kb` segment in every citation url had two self-consistent
  repairs and only one of them is safe. Rooting `kb-static` at `${KB_PATH}` would make
  `https://host/kb/kb/<file>.md` resolve — and would serve the whole knowledge repo (source
  documents, `corpus.yaml`, the disposable work cache) to anyone holding a token, to fix a
  URL. Refused. `KB_URL_BASE` is therefore documented as the site root with no `/kb`,
  `.env.example` corrected, and `make compose-up` refuses a value ending in `/kb` because
  the failure is otherwise silent until someone clicks a citation.
  The alternative inside the code — have `_url` strip the `kb/` prefix so the old
  documented value would work — was also refused: `rel_path` is hardcoded as `kb/<name>` at
  index time and exists only to build URLs, so the join is coherent as written, and changing
  it would break any deployment that had already set the value correctly.
  `tests/test_compose_citation_url.py` pins all four facts the rule rests on.
- 2026-10-01 [TOOL→DECISION] **Token management is container-first, and the store is written
  by the uid that reads it.** `make token` runs `kb token` in a one-off container built from
  the same image as the server, so the store it creates is owned by uid 10001 and mode 0600
  — readable by `kb-mcp` with no chown and without widening the mode. The server's own mount
  stays read-only: exactly one thing in the stack writes the store, and it is not the server.
  The one host command this leaves is `install -d -o 10001 -g 10001 -m 700 <dir>`, once,
  which `make compose-up` checks for and prints.
- 2026-09-28 [USER→DECISION] **A heading that introduces nothing does not open a section.**
  `retrieval.sections.build_sections`: a heading run with no body of its own joins the run
  below it; the swallowed heading stays in that section's blocks, so its words are still
  indexed, still searchable and still rendered on fetch. Nothing moves and nothing is lost —
  only the boundary changes. The section keeps the NAME and the LEVEL of the heading that
  opened the run, and a swallowed heading extends the heading path only when it is genuinely
  deeper (`4` then `4.1` still cites as both; two headings at the same level are not a
  nesting, and letting the second name the section would restore exactly the heading this
  removes). Naming by the deepest swallowed heading would read better but would take its
  level too, and level is what `get_outline` trims by — every section would fall past a
  depth limit and the outline would empty. Navigation beats a better title.
  **This reverses the 2026-09-25 decision** to fix the split in conversion rather than work
  around it at index time. The reason it was rejected — that an index-layer merge would mask
  a conversion defect — did not survive contact: the conversion fix landed, corrected a real
  defect, and moved the eval floor by nothing, and the form case it was meant to reach turned
  out not to be a conversion defect at all. Every word of `Annotated_Forms_SmallBus_FORMS-f`
  p10 is present, in order, on the right page; an assistant reading that page answers the
  question without difficulty. The defect was the cut, not the text.
  Measured: sections 1,577 → 1,134; **no-body sections 443 → 0**; sections able to answer on
  their own 48% → 67%; names shared with another section 112 → 55; median section 208 → 446
  chars, p99 18k → 21k, and the count over `FETCH_MAX_CHARS` stays at 2 — the two that were
  already there, so no new section is too big to return. 530 tests green.
- 2026-09-25 [DECISION] **A table's header row belongs to the table, not to the heading
  path.** `pdf_geometry._header_row` / `_absorb_header_rows`: a line directly above a ruled
  table is that table's header row when each of its cells falls wholly inside exactly one
  of the columns `_column_spans` measured the rows in, no two cells share a column, and
  they arrive in the grid's order. It is then moved off `page["lines"]` and inserted as the
  table's first row, and the region's bbox is extended upward so provenance still covers
  every word the block renders. Continues PR #1's line — a heading is never one cell of a
  row — with the grid's own rules as the corroborating neighbour instead of another line of
  text. **Absorbed rather than dropped, deliberately:** dropping fixes the section boundary
  and leaves the table headed by its first *data* row, so `<22` reads as the name of the
  column whose values are ages; absorbing also gives the caption above the table it
  introduces, so one section holds both the searchable words and the figures.
  Refusals, all found by measuring the proxy corpus: a grid with any unmeasurable column; a
  line more than `_HEADER_ROW_GAP` (1.5 × its own height) above the grid — at 3× the
  Sentinel spec's running header matches the table below it on eight pages; a line that
  fits two grids at once (almanac p8), since geometry cannot say which it heads; and any
  line `find_repeated_margin_lines` reports, which is why `to_blocks` now computes the
  repeated margin lines *before* it absorbs anything. `Line.cells` is now derived from a new
  `Line.cell_spans`, which keeps each cell's right edge — containment, not the left edge
  alone, is what separates `age | number | percent` from `2.1  Travel Rates`.
  Measured, page by page, against the words each page draws: duplication and loss unchanged
  on every page of all nine PDFs. Only `ch-05-b` changes at all (71 → 62 headings, all ten
  claimed lines table header rows); `kb index` reindexed 1 of 12. Sections 1,586 → 1,577,
  empty-bodied 452 → 443 (all nine in `ch-05-b`, 28 → 19), heading paths shared by more than
  one section 103/34 → 95/32 — the two 4× paths `age number percent` and `rank number
  percent` are gone. 524 tests green.
  **What it did NOT move, stated plainly:** `kb eval` is unchanged at lexical 31% / vector
  53% / fused 67%, case for case, and the MCP case `intact-table-captains` still fails. See
  [OUTCOMES].
- 2026-09-24 [DECISION] **A cell with no rule around it is recovered from the column its
  ruled rows measure — or not at all.** `pdf_geometry._table_rows` is now the single way
  both the renderer (`_table_regions`) and the cell index (`_table_cells`) read a ruled
  table, so a recovered cell is in both or in neither. `_column_spans` measures each column
  from the rows that are ruled and reports it only where every one of them puts it in the
  same place within `_COLUMN_EDGE_SLACK` (1.0 pt); a cell pdfplumber returned as `None` is
  then filled with that span crossed with the row's own band, and with the words the page
  draws inside it. Why it was needed: a cell exists for pdfplumber only where a rule bounds
  it on every side, and a banded table boxes its shaded rows and nothing else, so on the
  unshaded rows the outer cells had no vertical, came back `None`, and their text was
  dropped outright. Three refusals, each added after measuring the proxy corpus, not from
  first principles: (1) a column its ruled rows disagree about is not measured — that is a
  merged cell, and the average of the two spans overlaps its neighbour and copies a
  paragraph into both; (2) a table with any unmeasurable column recovers nothing at all —
  that is what `find_tables` returns for a bar chart's axis labels; (3) a candidate cell
  overlapping a ruled cell of the same table or another table on the page is abandoned —
  pdfplumber returns rows nested in a taller row and tables nested in a table, and the
  words there are already rendered once. A cell that is ruled and empty is never filled.
  No tolerance was widened; `PDF_LINE_TOLERANCE` is untouched. Tests 3f0518e (failing
  first, `banded_table.pdf` fixture), fix cadb4a4, guard rails 2e7bdd3, README 984994f.
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
  Tests a8e5cec (failing first, a generated 320,000-char fixture document), fix e9a5b80,
  outline-trimming test 6ce8c11. Brief §6.5.1/§6.5.2 updated in the design repo to match,
  so `"chunk"` and the ceiling are spec, not an undocumented deviation.
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
- 2026-10-07 [TOOL] **The 28 point vector-leg difference between the dev Mac and the AWS
  GPU host is UNEXPLAINED, and five hypotheses were eliminated by measurement.** Recorded
  in full because the eliminations are the durable part: anyone revisiting this should not
  repeat them.
  * **Measurements.** AWS `g4dn.xlarge`, GPU, ollama 0.35.1, pgvector 0.8.7: lexical 33% /
    vector 86% / fused 89%. Dev Mac, CPU, ollama 0.21.0, pgvector 0.8.6: 33% / 58% / 72%,
    on the September index AND again on a fresh `kb index --reindex-all`, per case
    identical between those two runs.
  * **Not a degraded index.** A full re-embed on the Mac reproduced the floor exactly, so
    the original index was not built from failed batches. This was the leading hypothesis
    and it is wrong.
  * **Not the model.** `nomic-embed-text` digest `0a109f422b47` on both hosts.
  * **Not the text or the chunking.** The lexical leg is identical case for case, and that
    leg never touches an embedding.
  * **Not short-input embedding.** The same string embedded on both servers agrees to five
    decimal places, both unit norm, so the runtimes are not producing different vectors.
  * **Not approximate search recall.** `chunks_embedding_idx` is HNSW, so vector search is
    approximate by default (`hnsw.ef_search` 40). Setting it to 1000 on the Mac, which
    makes the search effectively exact, changed **nothing, not one case**.
  * **Not server-side truncation of long inputs.** A prefix sweep against a 14,906
    character input plateaus at cosine 1.0 at 4,000 characters on BOTH hosts, agreeing to
    six decimals, so both discard the same tail. The 15 truncations the Mac reported are
    real but confined to `gsam`, whose three cases hit on both machines, so they are not
    the cause either. (Those chunks reach 39k characters because `chunk.py` keeps a single
    table row whole above `CHUNK_MAX`; that is by design and already documented there.)
  * **Still untried, if anyone revisits it:** compare the stored vectors for one specific
    failing case (`almanac-captains-count`, `nd-private-markets-aum`) between the two
    indexes rather than for synthetic strings; check whether the QUERY embedding path
    differs from the document path across ollama versions; compare chunk counts per
    document in the two indexes. The failing cases cluster in the table and CSV heavy
    documents (`almanac`, `nd`, `lac`, `pca`), which is the only pattern that survived.
- 2026-10-07 [TOOL] **An unwritable `KB_PATH` fails as a raw traceback, not as a named
  error, and it fails at the first command.** Found while confirming the conversion
  ownership question on Linux: dropping `--user` makes `pipeline inventory` die inside
  `manifest.save` (`pipeline/manifest.py:103`), which calls `path.write_text` with no
  guard, so the operator sees a `PermissionError` traceback through rich rather than one
  line naming the output directory and the fix. `convert.py:148` and `:152` write the
  markdown and its sidecar the same way. This is the failure mode of the single most
  likely mistake in the new runbook's longest command, and the house style is explicit
  that a line naming the fix beats a traceback. `docs/deployment-dgx.md`'s symptom table
  now translates it. **FIXED** the same day: `Config.writable_kb_path` is the mirror of
  `source_dir` on the output side, and `inventory`, `triage`, `convert` and `prune` call it
  before touching anything, so the run stops before converting half a corpus. Two choices
  worth keeping. It asks about the nearest EXISTING ancestor, not about `kb_path` itself —
  the writers create their own parents, so a check demanding the directory exist would
  refuse every fresh clone, and `tests/test_output_writable.py` pins that case. And the
  message names the uid, because without it the error reads as nonsense to the person
  holding the terminal: the directory *is* writable by them. Tests first (6ffc9b6, all
  seven failing), fix after (4f0b392). `make check`: **660 passed, 2 skipped**.
- 2026-10-06 [TOOL] **A `docker compose run -v` at a container path the compose file
  already declares is silently ignored; at a new path it is added.** Measured both ways
  with a two-line compose file: a `-v host:/kb-repo:rw` override against the stack's
  `${KB_PATH}:/kb-repo:ro` did not take — the write was refused as read-only, with no
  error and no warning that the override had been dropped — while a `-v host:/out:rw` at a
  path the file does not mention worked. This is what shapes the conversion step in
  `docs/deployment-dgx.md`: Stage 1 in the deployment image cannot be pointed at
  `/kb-repo`, it has to be given `/sources` and `/out` and have `SOURCE_DIR` and `KB_PATH`
  overridden to match. Worth keeping because the failure mode is a silent read-only mount
  rather than a refusal, so the obvious command reads as a permissions problem.
- 2026-10-06 [TOOL] **Stage 1 conversion runs in the `kb-mcp` image with no host Python,
  no database and no model server.** Verified end to end — `pipeline inventory`, `triage`,
  `convert`, `report` over a PDF, a DOCX and a CSV fixture — writing `kb/*.md` plus the
  `.provenance.json` sidecars. This closes a gap neither previous deployment touched: the
  AWS run transferred an already-converted corpus, so nothing had ever converted a
  document on a deployment host, and the container-first claim was untested for Stage 1.
  Three facts the runbook now rests on: `--no-deps` is required (Compose otherwise starts
  `db` and `ollama` for a job that uses neither, and on a host already running ollama the
  port bind fails outright); the image has **no `soffice`**, so legacy `.doc`/`.dot` cannot
  convert there; and it has **no `torch`**, so the Docling PDF escalation is unavailable —
  geometry PDF, DOCX and CSV only. Both absences are deliberate (`--extra serve`, no
  `--extra pdf`) and both are reported rather than silent.
- 2026-10-06 [TOOL] **Docker creates a missing bind-mount source as root, and that broke
  the corpus transfer.** `KB_PATH` was set in runbook step 3 and the directory created in
  step 9, with `make compose-up` between them. `kb-static` mounts `${KB_PATH}/kb`, so
  Compose created it — owned by root, four minutes after the parent the login user had
  made. `rsync` then failed with permission denied and `rm` needed `sudo`. The two
  directories a deployment needs have OPPOSITE requirements and were a step apart with
  nothing saying so: the content tree belongs to the login user and only has to be
  *readable* by uid 10001, while the token store must be *owned* by it. Worth keeping
  because of the silent half: a file in `kb/` that uid 10001 cannot read is served as a
  404 by `kb-static` while `kb index` embeds it perfectly well through its own mount, so
  the index looks right and every citation url is dead. `http_probe.py`'s "the cited url
  is actually fetchable" check is the only thing in the stack that catches it.
- 2026-10-06 [TOOL] **The probe had never been run from either place it is documented to
  run from, and was broken in both.** Two defects, one cause each, both in
  `scripts/http_probe.py`.
  (1) **From a client it judged the deployment by the local checkout.** The document to
  request came from globbing this machine's `kb/`, and a citation url was compared against
  this machine's `KB_URL_BASE`. Run from the dev Mac against a correct AWS stack it
  reported three failures, all wrong: a 404 for the first of twelve proxy documents asked
  of a server holding four fixtures, and a correct url judged against
  `http://localhost/kb` — which also silently skipped the "actually fetchable" check
  guarded behind it, reporting 16 checks instead of 17. The document now comes from the
  server's own `list_documents` and the url must point into `--base-url`.
  (2) **Discovery then read a field the server does not send.** A `DocumentEntry` carries
  `id` (`ids.doc_id(slug)` → `doc:<slug>`), not `slug`. A missing dict key is only `None`,
  and the local-corpus fallback cannot help inside the `kb-token` container, which is
  given no `KB_PATH`, so the probe exited before running a single check against a server
  holding twelve documents. Now parsed with `ids.parse_id`, with a test pinning the
  server's own declaration of the field.
- 2026-10-02 [CODE] **The server image does not carry `scripts/`**, so `scripts/http_probe.py`
  cannot be run with `docker compose run kb-mcp` as written. `compose/Dockerfile` copies
  `pyproject.toml`, `uv.lock`, `retrieval/`, `pipeline/` and `README.md` and nothing else —
  the image is the server, not the toolkit. The probe has to be bind-mounted at
  `/app/scripts` so the `retrieval` package it imports is its sibling, as the runbook now
  does. Found by checking the runbook's own commands against the Dockerfile rather than by
  running them; it would have failed on the deployment host at the first probe.
- 2026-10-02 [CODE] **`kb index` deletes the documents that are no longer on disk**
  (`retrieval/index.py`: `gone = set(existing) - seen`, then `DELETE FROM documents`). So
  swapping one corpus for another needs no `--init` and no `--reindex-all`: removing the
  files and re-running the one command both drops the old documents and takes on the new.
  This is what makes the runbook's fixtures-then-proxy-corpus sequence a single extra call.
- 2026-10-02 [CODE] **Nothing in `retrieval/` reads `corpus.yaml`** — it is Stage 1's
  manifest. The indexer reads `kb/*.md` plus each `.provenance.json` sidecar beside it
  (`retrieval/kbfiles.py`). Relevant because a corpus transferred to a deployment host
  needs the sidecars and does not need the manifest; copying only the `.md` files would
  index documents stripped of their provenance.
- 2026-10-01 [TOOL] **HTTPS broke IP-addressed clients, one layer below everything the
  README warns about.** Dialling the stack at `https://<ip>/health` failed in the TLS
  handshake — `tlsv1 alert internal error`, alert 80, `no peer certificate available` —
  while the same request to a name the site block lists completed. TLS SNI carries host
  names only, so a client dialling an IP sends none, and Caddy had no default certificate
  to answer with. It failed before any HTTP, so before the Host header, `KB_PUBLIC_HOST` or
  the bearer token were in the picture: **this is not the DNS-rebinding refusal the README
  documents**, which is an HTTP-layer rejection of a completed connection. Stages 1-4 never
  saw it because plain HTTP has no SNI to be missing, so the move to HTTPS was a silent
  break for every client that had been reaching the server by address. Fixed with one global
  `default_sni {$KB_PUBLIC_HOST:localhost}` — `default_sni` is NOT a `tls` subdirective in
  Caddy 2 (`caddy validate` rejects it as unknown), it only exists in the global options
  block. Verified: bare-IP HTTPS answers 200 afterwards.
- 2026-10-01 [TOOL] **A committed binary fixture was only reproducible on the machine that
  made it.** `tables_and_image.docx` differed from a Linux regeneration by one byte region:
  its embedded 6x6 PNG, same length, identical decoded pixels, different 20-byte IDAT. A
  PNG's pixel data is a zlib stream and which bytes a compressor emits for the same input
  depends on the zlib build behind it, so `Image.save(..., format="PNG")` is deterministic
  per machine and not across them — the generator's docstring claimed byte-stability on the
  grounds that PIL embeds no timestamp, which is true and insufficient. It matters beyond
  tidiness: `make check` is the stated verification command and the deployment target is
  Linux, so the gate could not pass there at all.
- 2026-09-25 [TOOL] **A form's field labels are NOT fixed, and the reason is that the page
  draws no evidence.** This was folded into the header-row task as "the same root cause".
  It is not: the almanac's header row sits over a ruled grid, and `Annotated_Forms_
  SmallBus_FORMS-f.pdf` p10 draws nothing at all around the shape that is failing.
  `Equipment item` [x 33.6–93.6] and `Funds Requested ($)` [x 455.6–534.7] are one `Line`
  only because words are grouped by baseline; the nearest ruled rectangle is 55 pt below
  them, and the equipment column itself is never ruled — only the two totals boxes are.
  There is no column edge, no neighbouring line, and no widget (`extract_widgets` returns
  `[]`; this PDF's AcroForm is flattened). Four candidate rules were written and measured
  over the whole proxy corpus against the 924 headings it currently emits. All four are
  REJECTED, and the numbers are the point:
  * **beside a blank ruled entry box** (`_table_cells`, the obvious `_reads_as_cell`
    extension): 22 heading lines claimed, of which 8 are plainly wrong — `Thank you`,
    `Thank You!`, `Handbook Summary`, `Required Supporting Documents`, `2.4. Inclusion of
    Women and Minorities`. It also misses both named cases: sections 31 and 32 have no box
    in their band.
  * **a wide internal gap** (cells separated by more than a fraction of the page width):
    no threshold separates them. `SF 424 (R&R) Page 2` sits at 0.63 of the page width and
    `Equipment item Funds Requested ($)` at 0.46, with legitimate headings on both sides of
    every cut.
  * **first heading of a run wins, demote the rest** (the structural reading of "the data
    should stay under the caption"): 215 of 924 headings demoted, including
    `TaskI-1. Establishbaselinemeasuresofcost` (PCA) and `8.3.1 Before IPF 3.70` (Sentinel).
    This is exactly what the task's own caveat warned about — the 435 empty-bodied sections
    are not one cause — and measuring it confirmed the caveat rather than the rule.
  * **a heading is one run of text: ≥2 cells after dropping a leading number**: 129 of 924,
    and it is right about `ISSUE DATE PAGE(S) DESCRIPTION` (the 10× Sentinel path) and the
    slide rows, but wrong about 35 PCA `TaskN-n.` headings — that document has lost its
    word spacing, so `Establishbaselinemeasuresofcost` is a second "cell" — and about
    Sentinel's `A1`/`B2.1` appendix numbering. Roughly a third false positives, on a rule
    that would change 14% of every heading in the corpus.
  **The conclusion is a measurement, not a shrug:** on this page geometry has nothing to
  corroborate with, so the remaining evidence is typographic and linguistic — a column
  label repeated at the same x on three lines, or the run-on words that reveal broken
  spacing. `Annotated_Forms...` is also the corpus's outlier on bold: 37% of its
  body-size text is bold, against 14% for the Sentinel spec and under 9% everywhere else,
  which is why 71 of its 113 headings rest on `_heading_run`'s bold-at-body-size fallback
  alone. A threshold between 14% and 37% would be a constant fitted to one document.
  UNRESOLVED, and it should be scoped as its own task with that evidence named.
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
- 2026-10-07 [USER+TOOL] **Proofreading pass on `docs/deployment-dgx.md`, applied.** Corrections, each checked: step 0 said three commands need `sudo` and listed two (now two plus step 5's conditional `chown`); the intro's "no Python, no step asks for one" now says step 13's probe runs from a client machine with `uv` and a checkout; "23 of 23" now says it is the host run and that a client-side run is 17 (both figures from the 2026-10-06 AWS run); step 4 notes `.env.example` has these lines commented out; step 11's "commit the result" now names `corpus.yaml`, the only tracked file in the content repository besides the README and `.gitignore` (`kb/` is gitignored); the model-from-file claim is marked not exercised. Wording: supplied phrases and a duplicated bare-address paragraph removed. No em dashes before or after. `make check`: 660 passed, 2 skipped.
- 2026-10-06 [TOOL] **`docs/deployment-dgx.md`: the DGX deployment runbook, fourteen
  steps**, with a pointer to it from the README's "Standing the stack up on a fresh host".
  It is the AWS procedure with the instance, the rsync and the teardown removed and the
  document-conversion step added, since on the DGX the documents are on the box and the
  AWS run sidestepped Stage 1 entirely by transferring an already-converted corpus. It
  lives in the code repo rather than beside the AWS runbook so that it travels with the
  `git clone` that is step 2 of itself. Carries every measured trap: the two directories
  with opposite ownership requirements, `KB_URL_BASE` with no `/kb`, the directory-not-file
  token mount, `default_sni`, `--no-sync`, and the silent 404 a content file uid 10001
  cannot read. No retrieval number appears in it, the eval question being open.
  `make check` after: **653 passed, 2 skipped** (both gates passed; higher than the 603/52
  in the handoff only because the dev `db` container was up, so the Stage 2 tests ran).
- 2026-10-06 [USER+TOOL] **The deployment ran end to end on a fresh AWS GPU instance, and
  all three things the 2026-10-01 remote test could not reach are now answered.**
  `g4dn.xlarge` (one T4, 16 GB), Ubuntu 22.04 Deep Learning Base OSS Nvidia Driver AMI,
  Elastic IP, code by `git clone` of `origin/main`, corpus by rsync. Runbook:
  `../aws-gpu-deployment-runbook.md`.
  * **GPU passthrough: `ollama ps` reported `100% GPU`.** `bootstrap_host.sh` proved
    passthrough inside a throwaway CUDA container first, `make gpu-check` reported
    reserving, and the model was resident on the card after an index.
  * **The real embedding model, end to end over HTTPS.** `nomic-embed-text` pulled from
    `registry.ollama.ai` and indexed with, replacing the deterministic double the
    2026-10-01 run was confined to. Fixtures first (`4 reindexed`), then the twelve-document
    proxy corpus (`12 reindexed, 4 deleted, 0 errors`) — one call, since `kb index` deletes
    what is no longer on disk.
  * **A supplied certificate (`TLS_CERT`/`TLS_KEY`) instead of `tls internal`, verified by
    a real client.** Self-signed from the new `scripts/make_tls_cert.sh`, standing in for
    the client's internal CA. **17/17 from the dev Mac over the internet** — which is the
    run that means something, because a probe inside the compose network cannot say
    whether a remote client trusts the certificate.
  * **23/23 from the host, including revocation on both routes.** Revoking one user closed
    `/mcp` and `/kb/*` in **4.2 s against a 5 s bound**, with no restart, and another
    user's token was untouched. That is the claim the token store exists to make and it had
    never been tested on a deployment.
  * **Retrieval, first measurement off the dev Mac:** lexical 33% / vector 86% / fused 89%.
    See [PLANS] — the vector jump is unexplained and the floor table is not being changed
    until it is.
  **Five deployment-only defects, all invisible to `make check`, and the pattern has
  shifted.** Four were in the deployment (`uv` required by the container-first make
  targets; the pre-flight crashing with `PermissionError` on the 0700 store directory it
  recommends; Docker creating `${KB_PATH}/kb` as root; the runbook handing out an `rsync`
  with no `-i`), and **two were in the probe itself** — the tool that exists to verify
  deployments, broken in both modes it documents. Every one was invisible on a developer
  machine for a specific reason: `uv` is always on PATH, tests run as root or the owning
  user, `KB_PATH` already exists because you cloned it, and the local `.env` happens to
  describe the server you are probing.
  **Still untested anywhere:** a certificate from a real internal CA rather than a
  self-signed stand-in, more than one GPU, and the §10 user record question, which is
  unchanged and still deliberately open.
- 2026-10-06 [USER+TOOL] **The GPU path is verified on real hardware, and the real
  embedding model has been indexed with for the first time.** Fresh AWS `g4dn.xlarge`
  (one T4, 16 GB), Ubuntu 22.04 Deep Learning Base OSS Nvidia Driver AMI, Elastic IP
  attached so the address survives stop/start. Code reached the host by `git clone` of
  `origin/main`.
  * **`ollama ps` reported `nomic-embed-text` with `PROCESSOR` = `100% GPU`** after an
    index. That single line is what the GPU claim rests on, and it is the one thing
    neither `make check` nor the 2026-10-01 remote run could produce — that host had no
    GPU. `make gpu-check` reported `reserving all NVIDIA GPUs for ollama` beforehand, and
    `scripts/bootstrap_host.sh` had already proven passthrough from inside a throwaway
    CUDA container.
  * **`nomic-embed-text` was pulled from `registry.ollama.ai` and indexed with.** The
    2026-10-01 run was indexed against the repo's deterministic embedding double, so no
    ranking claim from it meant anything. `kb index` over the four synthetic retrieval
    fixtures: `0 unchanged, 4 reindexed, 0 deleted, 0 errors`.
  * All five prod containers up (`db`, `ollama`, `kb-mcp`, `kb-static`, `caddy`) with
    `TLS_CERT`/`TLS_KEY` set to a self-signed pair from the new
    `scripts/make_tls_cert.sh`, standing in for the client's internal CA.
  **Still UNCONFIRMED on this host, in the order it will be answered:** the TLS handshake
  from a client against the supplied certificate (the stack starting proves Caddy accepted
  the pair, not that a client verifies it); `scripts/http_probe.py` end to end, including
  revocation on both routes; and the proxy-corpus eval against the recorded floor of
  lexical 33% / vector 58% / fused 72%, which has never been reproduced off the dev Mac.
- 2026-10-06 [TOOL] **Two more deployment-only defects, both in the same pre-flight, both
  invisible to `make check`** — found at step 6 of the runbook, on the host.
  1. **`make token` needed `uv` on a host that has none.** `compose-env-check` ran
     `scripts/check_token_paths.py` through `$(PYTHON)`, which is `uv run python`, so
     `token`, `compose-up` and `compose-index` — the three targets that exist *because* the
     deployment host has `docker compose` and nothing else — all required a host `uv`.
     `make token` died with `uv: No such file or directory`. The dependency was accidental:
     the script imports only the standard library plus `retrieval/config.py`, itself
     stdlib-only. Fixed with a separate `PREFLIGHT_PYTHON ?= python3`; `PYTHON` stays `uv
     run python` for the developer targets. Installing `uv` on the host was rejected: it
     would have made the symptom go away and left the container-first claim false.
     `tests/test_make_host_requirements.py`.
  2. **The pre-flight crashed on the setup it recommends.** `install -d -o 10001 -g 10001
     -m 700 /srv/kb` deliberately lets nobody but the server's uid enter the directory, and
     `Path.exists()` on the store inside then raises `PermissionError` rather than
     answering — `_ignore_error` swallows ENOENT and ENOTDIR and never EACCES. A traceback,
     from the script whose job is to replace a confusing failure with one line naming the
     fix. An unreadable store is now a PASS with a note. What makes that safe rather than
     permissive: `stat` on a directory needs only `+x` on its PARENT, so the directory
     check still runs when the directory itself shuts us out, and it is the check that
     pins what matters — that uid 10001 can write there. A test pins that the root-owned
     store of 2026-10-01 is still refused, as a non-root user. An unreadable *directory*
     still exits 2, naming the `sudo` command.
  **The pattern, now three deployments running:** every defect was in something `make
  check` passes, and each was invisible on a developer machine for a specific reason — `uv`
  is always on PATH there, and tests run as the owning user or as root. Both of today's
  were in the pre-flight added *because* of the last round's defects.
- 2026-10-02 [TOOL] **The deployment is packaged for a fresh GPU host.** `make check` green
  at **630 passed, 2 skipped**, both gates PASSED (the 627 baseline on `main` plus three new
  tests). Nothing about the running stack changed; what was missing was the path from a bare
  host to it.
  * `scripts/bootstrap_host.sh` — Docker Engine, the compose plugin and the NVIDIA Container
    Toolkit, each installed only if missing, idempotent, `--check` to verify only. It ends by
    running a throwaway CUDA container and reading `nvidia-smi` from inside it, because a
    host whose own `nvidia-smi` works while a container's does not is exactly the
    half-configured state `KB_GPU=on` exists to refuse and nothing on the host reveals it.
    It deliberately does NOT install the kernel driver: not idempotent, usually wants a
    reboot, and on a cloud host it is the image's job — so it refuses with one line naming
    the AMI choice instead.
  * `scripts/make_tls_cert.sh` — a self-signed pair for the `TLS_CERT`/`TLS_KEY` path, with
    every name the Caddyfile's site block answers for on it (`KB_PUBLIC_HOST`, `localhost`,
    `127.0.0.1`) plus any extra name or IP given. `tls/` is gitignored; it holds a private
    key. Exercised locally: the SANs come out as intended, DNS and IP in their separate
    fields.
  * `tests/test_compose_tls_cert_names.py` — three tests pinning the failure that only
    exists on the supplied-certificate path: with `tls internal` Caddy issues a certificate
    per name on demand so the name set is open, while one supplied certificate closes it at
    the moment it is made, and a client dialling a name that is not on it fails
    VERIFICATION — a different failure, at a different layer, from the SNI handshake one
    `tests/test_compose_tls.py` already pins. Also pins that an IP argument becomes an IP
    SAN, since a DNS SAN does not match a client that dialled an address, and the AWS run
    reaches the instance by IP before any name exists for it.
  * `../aws-gpu-deployment-runbook.md` — the twelve-step run, each step saying what it
    proves. Its commands were checked against the code rather than trusted: three were wrong
    as first drafted, see [DISCOVERIES].
  * `README.md` gained "Standing the stack up on a fresh host" and, in the TLS section, the
    naming rule for a supplied certificate.
- 2026-10-02 [TOOL] **`main` is NOT 6 commits ahead of `origin/main`; it equals it.** The
  2026-10-01 handoff recorded the HTTPS deployment work as unpushed. It is on GitHub:
  `d9ceb44` (PR #3, `fix/https-stack-deployment`) and `08bb4d1` (PR #4,
  `docs/repo-conventions`) are merge commits on `origin/main`, and `git rev-list --count
  origin/main..HEAD` is 0. Since the DGX deploys from GitHub, that work is already in effect
  there. Recorded because the briefing said otherwise and the next person would plan around
  a push that has happened.
- 2026-10-02 [TOOL] Baseline on `main` before any of today's changes: both gates PASSED,
  **627 passed, 2 skipped**, exit 0 (629 collected — the same count as the remote host's
  629, where nothing skipped). The 2026-10-01 handoff's figure of 597 is stale.
- 2026-10-01 [TOOL] **The HTTPS compose stack ran end to end for the first time, on a
  remote Linux host, and five deployment-only defects had to be fixed to get there**
  (branch `fix/https-stack-deployment`). `make check` green at **629 tests**,
  both gates PASSED, nothing skipped (Postgres and the embedding service were up, so the
  50 Stage 2 tests that skip on the dev Mac ran). `scripts/http_probe.py`: **23/23** against
  the real stack over TLS at its public address.
  Each defect was invisible to `make check` and to the earlier verification, and each one
  alone broke the deployment:
  1. **The server could not read its own token store.** `kb token issue` on the host writes
     mode 0600 owned by whoever ran it; `kb-mcp` runs as uid 10001; `kb serve` exits 2 on an
     unreadable store. The container crash-looped (9 restarts) and the only symptom at the
     front door was Caddy answering **502** — nothing named the permission. Fixed three
     ways: `tokens.write` now carries the previous file's owner and mode across the atomic
     replace (without that, a one-time `chown` is undone by the next `issue` or `revoke` —
     the same shape as the inode bug); a new `kb-token` compose service writes the store as
     uid 10001 so the normal path never creates an unreadable one; and
     `scripts/check_token_paths.py` refuses before Compose starts, naming the uid and the
     exact command.
  2. **There was no way to manage a token on the deployment target at all.** Every
     documented path was `uv run kb token ...` and the DGX has Docker and nothing else —
     while the quickstart asked for a token *before* `make compose-up`. Now `make token
     ARGS="issue you@example.com"`, a one-off container with the store mounted read-write
     (the server's mount stays read-only) and no `depends_on`, since tokens live in a file
     precisely so authentication survives Postgres being down.
  3. **The image was not self-contained: it reached pypi at every container start.** The
     build is `uv sync --extra serve --frozen --no-dev`, but the ENTRYPOINT was a bare `uv
     run`, which syncs again with none of those flags — adding the `dev` group (`pytest`,
     `reportlab`) and fetching it every start, because `UV_CACHE_DIR` is under /tmp.
     `docker run --network none` did not start at all, dying on a `packaging` wheel only
     `pytest` wanted. `ENTRYPOINT ["uv", "run", "--no-sync"]`; verified offline afterwards.
  4. **Every citation url was a 404.** `fetch` returned
     `https://<host>/kb/kb/budget-form.md` — the segment doubled, because the indexer stores
     `rel_path` as `kb/<file>.md` and `.env.example` documented `KB_URL_BASE=.../kb`. The
     `/kb/*` route exists to let a reader check a citation against the original, and nobody
     had ever followed one end to end. See [DECISIONS] for which half was changed.
  5. **The documented quickstart failed on a fresh database.** `make compose-up` then `make
     compose-index` ended in a raw asyncpg traceback (`relation "index_meta" does not
     exist`): `--init` was only ever documented for the host venv, and `compose-index` took
     no `ARGS`. Now it does, and `_run_db` turns that error into one line naming both ways
     to apply the schema.
  Also fixed, because it blocked the stated verification command on the target platform:
  `make check` could not be green on Linux at all. `test_fixtures_regenerate_byte_
  identically` failed on `tables_and_image.docx`, whose embedded 6x6 PNG decoded to
  identical pixels but differed in its 20 IDAT bytes — a zlib-build difference between
  Pillow on macOS and on Linux. The fixture generator now writes that PNG byte by byte with
  a *stored* (uncompressed) deflate block, so no compressor is involved; `tables_and_image.
  docx` and `dangling_rels.docx` were regenerated and differ only in those bytes, and
  `tables_and_image.md`'s golden moved by one line (`content_sha256`).
  **Lesson, the same one as last time and earned again: the revocation work was verified
  against real containers and still shipped four deployment-only defects, because what was
  never exercised was a FRESH deployment on a machine that was not the dev Mac** — a store
  written by the documented command, an image started without egress, a citation followed
  to its document, a database with no schema.
- 2026-10-01 [TOOL] **The compose mount would have broken revocation, and is fixed**
  (`fix/token-file-bind-mount`, `6770d7f`, merged `afe39b5`; 597 tests, `make check` green).
  Found by testing the deployment path rather than the code: **a single-file bind mount binds
  the host file's inode**, and `tokens.write` is atomic (temp file + `os.replace`), which
  installs a new one. Measured in a container: the mount does not go stale, the path
  **disappears** (`No such file or directory`), `tokens.read` treats that as no records, and
  every file-backed token stops working **on the first issue or revoke**. The shipped
  `${KB_TOKENS_FILE}:/etc/kb/tokens.json:ro` would therefore have turned the revocation fix
  into the outage it removes. Now `${KB_TOKENS_DIR:-./tokens}:/etc/kb:ro` — the directory,
  still read-only; verified the container tracks `os.replace` through it. Costs a second
  setting that must equal `KB_TOKENS_FILE`'s parent, so `make compose-env-check` now runs
  `scripts/check_token_paths.py` first (the failure it prevents is the quiet one: `kb token
  revoke` writing where the server is not reading, reporting success, changing nothing).
  `compose/tokens/.gitkeep` is a committed empty default so the mount always succeeds — a
  missing directory source fails the container at start, which is worse than `kb serve`'s own
  refusal naming `kb token issue`. **Lesson worth keeping: the revocation work was verified
  end to end against a real server process and real Caddy containers, and still shipped a
  deployment-only defect, because the one thing not exercised was a write to the store while
  it was mounted.**
- 2026-09-30 [TOOL] **Server-side token revocation shipped** (branch `fix/token-revocation`,
  two commits `fdf38d4` and `2bce5af`, **merged to `main` 2026-10-01 as `b45c4d7`**,
  not pushed). `make check` green on `main` at **585** tests, both gates PASSED. New
  `retrieval/tokens.py`: `KB_TOKENS_FILE`, a JSON file of one sha256-hashed, individually
  revocable record per user (`id`, `user`, `hash`, `issued`, optional `expires`/`revoked`/
  `note`), written atomically at mode 0600. `TokenStore` re-reads it when mtime/size change,
  stat'ing at most once per `KB_TOKEN_CACHE_SECONDS` (default 5) — that number is the upper
  bound on how long a revoked token keeps working. `BearerMiddleware` takes the store instead
  of a tuple and puts the resolved `Identity` in `scope["state"]["kb_identity"]` for the §10
  ACL work (nothing reads it yet). New `kb token issue|list|revoke`. `KB_TOKENS` still
  authenticates, is reported as `(KB_TOKENS)` in the log, and now warns at startup.
  **`/kb/*` was the other half and is fixed too, which was not in the recorded plan**: Caddy
  matched a token regex built from `KB_TOKENS` at container start, so a revoked token kept
  reading the whole converted corpus over `/kb/*` until `caddy` restarted. It now does
  `forward_auth` to a new `/auth/check` on `kb-mcp` (204/401, middleware-answered, never
  reaches the inner app). `caddy-entrypoint.sh` no longer reads `KB_TOKENS` at all.
  **Verified with real containers**, not only pytest: `/kb/*` 200 with a good token and 401
  with a bad one, tracking the upstream's answer with no cached decision in Caddy; `/health`
  still open; `/auth/check` 404 from outside the compose network. **Two behaviour changes,
  both deliberate.** (1) `--allow-anonymous` now serves requests unauthenticated, which its
  warning always claimed — an empty token tuple matched nothing, so it previously rejected
  *every* request and the flag was useless. Nothing had tested it. (2) A token file that
  breaks while the server runs keeps the last good copy serving (a typo must not lock
  everyone out at once); a file already broken at startup still refuses to start, exit 2.
  **New audit trail:** one JSON line per decision on stderr, `kb.server.auth` —
  `{"event":"auth.ok|auth.denied","path":...,"token":...,"user":...,"source":"file|env"}`.
- 2026-09-24 [TOOL] **Banded-table cell recovery shipped** (branch `fix/banded-table-cells`,
  4 commits; see [DECISIONS]). `make check` green at 514 tests (510 before this work: 500
  plus the 10 the eval-case and fetch-ceiling work added). `make fixtures` is a no-op.
  Corpus re-converted and re-indexed: **2 of 12 documents reindexed**, `ch-05-b` and
  `Annotated_Forms_SmallBus_FORMS-f`, which is the whole blast radius.
- 2026-09-24 [TOOL] **Fetch size ceiling applied to every path** (branch
  `fix/fetch-size-ceiling-all-paths`, tests a8e5cec, fix e9a5b80; see [DECISIONS]).
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
- 2026-09-28 [USER→TOOL] **New floor: lexical 33% / vector 58% / fused 72%** over the same
  36 cases, up from 31/53/67. Branch `fix/empty-heading-sections` (tests d7faafd, fix
  9dbbe9b, fixtures 8fb99cc, probe 518efa0, README e495271). Two cases went from a miss on
  every leg to a hit and none went the other way: `forms-equipment-threshold` (miss →
  **rank 1**, the case the converter could not reach) and `cfap-egg-form-part`. Two shifted
  rank and stayed hits (`presentation-cis-net-irr` fused 2 → 1; `hsa-2026-limits` vector and
  fused 1 → 5, worth watching — it is now at the edge of hit@5). Nothing tuned: no chunk
  size, no `k`, no fusion constant. Needed `kb index --reindex-all`, ~50 min on the dev Mac.
  The four almanac cases still miss; that is the short-numeric-table ranking problem and is
  untouched by this. `mcp_probe.py` back to 1 standing failure (`intact-table-captains`,
  same cause).
  Cost to be honest about: `sec:ch-05-b:3` — the accessions page, now properly named and
  holding the whole block — dropped out of the top 8 for its own question, where it had been
  present before. Net across the 36 cases was +2/−0, but the ranking did move under this and
  not only upward.
- 2026-09-28 [TOOL] **A pinned section index is not a durable way to address an eval case,
  proved twice in one session.** `mcp-cases.yaml` case 3 pinned `sec:ch-05-b:7`; the header-
  row fix renumbered it, and the section merge renumbered it again — silently each time,
  because `expect_damage` only prints for a reader and nothing compared what came back with
  what was meant. Now pinned `page:ch-05-b:p003`, which no renumbering moves, and renamed
  `damaged-table-accessions` (the age table it used to point at has converted cleanly since
  2026-09-24 and no longer demonstrates damage). `expected_slug` dropped from that one case
  and the reason recorded in the file: it exists to put damaged output in front of a reader,
  not to assert retrieval, and the almanac's poor retrieval is already measured twice in
  `proxy-cases.yaml`. Pinning by page needed one probe fix — `get_section(neighbours=1)` is
  only a superset of `fetch` for a `sec:` id — otherwise a `page:` pin reports a failure
  that says nothing about the server. `proxy-cases.yaml` pins no ids and was never exposed.
- 2026-09-25 [TOOL] **The heading/data split is fixed in conversion and the floor did not
  move.** Branch `fix/table-header-row-headings` (tests + fixture `captioned_table.pdf`
  0eae6d8, fix 1d450fb, guard rails 5d06634, README 3c5bed5). `kb eval` over the 36 proxy
  cases: lexical 31% / vector 53% / fused 67%, identical case for case to the recorded
  floor. `scripts/check_eval_cases.py`: 36 cases, 0 unsatisfiable, 4 warnings, unchanged.
  `scripts/mcp_probe.py`: 69 mechanical checks, still 1 failure.
  The four almanac cases and `forms-equipment-threshold` all still miss, and the reasons
  are now different from what was recorded:
  * `almanac-captains-count`: `sec:ch-05-b:7` is now `Active Duty Officer Grade
    Distribution` and holds `| Captain | 5,913 | 28.6% |` in one 427-char section. It is
    still not in the top 5. The vector leg puts that document's four occupational-field
    tables (1,890–2,267 chars) above it for every question about a rank; even the query
    `Active Duty Officer Grade Distribution captain` does not surface it. **This is a
    retrieval finding, not a conversion one, and it is the same finding as the CSV's.**
  * `almanac-officer-accessions`, `almanac-ocs-accessions`, `almanac-warrant-officer-route`
    are all page 3's *accessions* table, which is the separate unfixed defect recorded
    2026-09-24: each shaded row is its own single-row table, dropped by the two-row minimum
    in `_table_regions`, so alternate rows render as `> **Boxed text:**`. The header row
    `type number` sits 23 pt above the first of those grids — beyond `_HEADER_ROW_GAP` and
    over a grid that is not rendered — so this rule does not touch it.
  * `forms-equipment-threshold`: the form's field labels are NOT fixed. See [DISCOVERIES].
- 2026-09-25 [USER→TOOL] **`mcp-cases.yaml` case 3 re-pointed; Tim approved the edit.**
  `damaged-table-age-23` pinned `expected_id: "sec:ch-05-b:7"`. Section indices shifted by
  one when the caption absorbed its header row, so it silently began fetching the *grade*
  table under `expect_damage: true` — silently because `expect_damage` asserts nothing, it
  only prints for a human. Two problems at once: the address was wrong (caused by this
  fix), and the premise had been false since the banded-cell fix of 2026-09-24 made the age
  table convert cleanly, so there was no damage left to demonstrate. Renamed
  `damaged-table-accessions`, pointed at `sec:ch-05-b:5` (`type number`, page 3's accessions
  list — the separate unfixed single-row-table defect, where alternate rows still render as
  `> **Boxed text:**`), question changed to the Platoon Leader Course row. That preserves
  what the case was for: the contrast with case 4's intact table. Probe back to 69
  mechanical checks / 1 failure. Backup beside it as `mcp-cases.yaml.bak-20260925`.
  A `page:ch-05-b:p003` id would be stable against renumbering and was tried first, but it
  fails the probe's `get_section(neighbours=1) is a superset of fetch` check, so the case
  keeps a section index and carries a CAUTION comment saying to re-check the printed text
  after any conversion change. **`proxy-cases.yaml` pins no ids at all** (slug + phrase +
  page only), so it is immune to this class of breakage; `mcp-cases.yaml` had the only one.
- 2026-09-24 [TOOL] **Banded-table fix measured against the proxy corpus, before and after,
  by rendering every page with both versions of the module.** Two counts per page, against
  the words the page actually draws: tokens rendered more often than drawn (duplication) and
  tokens never rendered at all (loss).
  * Loss: `Ch 05_b` 464 → 154 unrendered tokens over 14 pages. Every other document
    unchanged — so of the nine PDFs, only the almanac was losing table text this way, and
    the `None` cells elsewhere (2,079 in the Sentinel spec, 656 in the PCA guidebook) cost
    nothing: their text was already being rendered by a nested table.
  * Duplication: zero pages increase. The first version of the fix increased it on 82 pages
    and added 5,264 token occurrences across six documents; that is what the three refusals
    in [DECISIONS] are for, and each was written after seeing the page that needed it.
  * `kb eval`, 36 cases: lexical 31% / vector 53% / fused 67% — **identical to the recorded
    floor**, case for case. Expected: the fix restores evidence, and every almanac case
    misses for the reason already recorded — `### rank number percent` is the heading of
    four sections of that document, and the right one does not rank. `almanac-captains-count`
    asks for a row that survived conversion even before this fix.
  Nothing was tuned toward the score; chunk sizes, k and the RRF constant are untouched.
- 2026-09-24 [TOOL] **The almanac's accessions table (p3) is a second, visible defect, not
  fixed here.** Its shaded rows are single-row tables, dropped by the two-row minimum in
  `_table_regions`, so `find_note_boxes` is never told they are tables and renders every
  other row as `> **Boxed text:** Platoon Leader Course 383` with the rest as paragraphs. No
  text is lost — `NROTC 218` and `Officer Candidate Course 473` are both present verbatim —
  so this is the cheap failure, not the dangerous one. Not fixed because the obvious repair
  (feed `find_note_boxes` every `find_tables` bbox rather than the filtered ones) would call
  every lone stroked rectangle a table and delete boxed-text rendering entirely.
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
