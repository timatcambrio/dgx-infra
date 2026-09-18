# CONTINUITY

Canonical briefing for the PDF-geometry output-quality task. Facts only.

## [PLANS]
- 2026-09-15 [USER] README rewritten for a NEW USER of the repo, not for us: `uv` only (no
  conda step), no milestone vocabulary (M0-M3), and organised around what someone has to do
  and what the diagnostics mean. 584 lines -> 366. Design rationale was dropped from it
  wholesale; most already lives in [DECISIONS] here, and what did not is recorded below.
  Anything added to the README from now on should pass the same test: does a user of the
  pipeline need it, rather than a maintainer of it.
- 2026-09-15 [CODE] OPEN QUESTIONS carried over from the old README's "Open questions"
  section, which was removed. Items 1 and 2 (TableFormer licence; docling-layout-heron
  base-weight provenance) are recorded in full in `models.yaml` under `pending_review` and
  summarised in [DISCOVERIES] above; these three were recorded NOWHERE else:
  * LibreOffice's pinned major version is UNCONFIRMED and it is not installed on the dev
    machine, so the `.doc`/`.dot` path is entirely unexercised. Its `.doc` import filter is
    not byte-stable across releases and byte-stable output is a hard requirement, so the
    version must be pinned and matched between dev and the client. Needs the client's
    available version. A short form of this survives in the README as a blockquote.
  * Are any other CSVs reference tables or per-row records? Decides whether a `record` mode
    is ever built. The one sample in hand is a reference table. Not blocking.
  * Do real samples carry a discoverable revision or effective date? `doc_date` falls back
    to `UNCONFIRMED`, and if most come back that way the citation requirement needs an
    answer other than "cite the document date". Not blocking.
- 2026-09-15 [USER] DELIVERY MODEL, supersedes any assumption that we convert the client
  corpus ourselves: the deliverable is `dgx-infra`, the pipeline. The client stands up their
  own `dgx-knowledge` from their own internal documents, which we never see. The 6 documents
  in `diu-internal-docs/temp-holding` are restricted -- they cannot be shared or committed,
  nor can their converted markdown -- and no further documents are expected. Acceptance is
  therefore that the pipeline behaves defensibly on unseen documents and reports honestly
  when it cannot, not that any particular corpus converts well. Full reasoning, the proxy
  corpus it requires, and its sources: `.agent/PROXY-CORPUS.md`.
- 2026-09-15 [ASSESSMENT] Work so far has been driven by whichever evidence class happened to
  be present in the document examined first. The structural fix is to stop treating documents
  by identity and start treating them by the evidence they offer: extend `analyse()` into a
  per-document evidence profile (annotations, callout lines, named widgets, ruled vs
  borderless tables, columns), emitted for every file, so an unhandled document type surfaces
  as an unfamiliar profile rather than as a silent degradation. This is also the only sound
  response to the corpus-representativeness problem: do not tune thresholds on two samples;
  build the instrument that reports the distribution when the real corpus arrives.
- 2026-09-15 [ASSESSMENT] Proposed order, NOT yet approved by USER: (1) evidence profile in
  `analyse()` + report; (2) fix the table-blindness defect; (3) replace `[near:]` inference
  with emitted evidence; (4) table-grid recovery for ruled forms, designed only after real
  corpus profiles exist; (5) tag flattened callouts via widget rects.
- 2026-09-15 [ASSESSMENT] Build a fixed answerability eval -- "which cell / which box / what
  is the limit" questions with known answers, run against converted output. It is what tells
  us whether a converter change helped, and the defence against tuning to two samples.
- 2026-09-14 [USER] Bind extracted PDF annotations to the form field each describes.
  Motivated by a retrieval test the converted output failed: asked which cell takes federal
  funds carry over, the NIFA budget markdown could not answer, because its annotations are
  free-floating `> **Annotation:**` blocks with no anchor to a cell. The same test against
  the NIH small-business annotated form set was answerable (item 1, TYPE OF SUBMISSION,
  "Application") but only by inference from adjacent annotation text, not from encoded
  linkage.
- 2026-09-14 [USER] Scope limit, standing: do NOT read `dgx-knowledge/kb/` or
  `dgx-knowledge/eval-marker/` contents, nor `work/marker/**/*.md`. They are eval corpus and
  reading them invalidates the retrieval tests. `dgx-infra` is in scope. Verification of
  corpus-facing behaviour must use synthetic fixtures or documents the user designates.
- 2026-09-14 [USER] Public-facing docs must not name agent involvement and must not name
  corpus document file names. CONTINUITY may carry both; it is scrubbed before release.
- 2026-09-14 [USER] Fix two pre-existing defects in `pipeline/converters/pdf_geometry.py`:
  (1) over-heading — nearly every non-body line emitted as `###`, run-on paragraphs and
  bullet lists collapsed into one heading line; (2) word-splitting — `S ubmit` instead of
  `Submit`. Order of work is fixed: failing tests against committed fixtures first, then the
  fix, then golden regeneration in a SEPARATE commit that changes nothing else.
- 2026-09-14 [USER] Verification command is `make check`; the model gate fails while Marker
  weights sit in `dgx-knowledge/work/models`, so run `make clean-work` or point `--cache-dir`
  at a scratch directory first.

## [DECISIONS]
- 2026-09-15 [DECISION] The released sample is RELEASE-FILTERED and is a biased estimator of
  the corpus on exactly the axes the evidence profile measures. `annot=0, w/CL=0, fields=0`
  on all four released PDFs is NOT evidence that annotation and widget handling is dead
  weight: documents carrying reviewer annotations, filled fields, tracked changes and
  redactions are systematically less likely to clear release review, so their absence from
  the sample is close to uninformative. Keep the machinery. What the sample does transfer
  unbiased is register, authoring toolchain, the dominance of borderless over ruled tables,
  and image-dominant pages as routine.
- 2026-09-15 [DECISION] Proxy selection targets coverage of the plausible population, not
  resemblance to the released six. Matching the sample's profile would propagate the release
  filter into the test corpus and leave the highest-risk regions -- annotated, filled forms,
  scanned, redacted, tracked-changes docx -- untested. Supersedes the earlier suggestion of
  screening candidates for a close match to the released deck's profile row.
- 2026-09-15 [DECISION] Flattened callouts render as `> **Boxed text:**`, NOT
  `> **Annotation:**`. They are page text in a rectangle and carry none of an annotation
  object's provenance; the measurement supports only "the page sets this apart in a box".
  Keeping the two prefixes distinct preserves that difference downstream.
- 2026-09-15 [DECISION] A rect overlapping a ruled table is never a note box. A shaded header
  cell is a filled rect holding text, and tearing a table apart is far worse than leaving a
  note unmarked -- the same asymmetry as the missed-vs-invented table rule.
- 2026-09-15 [DECISION] Image-dominant pages are REPORTED, never reclassified. A page that is
  a third diagram is not broken, and telling a diagram from a screenshot of a form is exactly
  the inference this pipeline declines to make. Threshold `IMAGE_PAGE_COVERAGE` (default
  0.15) is configurable and the coverage fraction is recorded beside it, so the threshold can
  be argued with.
- 2026-09-15 [DECISION] Notes are suppressed where they would misattribute a cause: a page
  already named as low-text is not named again as image; and "form fields but no ruled
  tables" is withheld from a document with image pages, because that note means the converter
  failed to reconstruct a grid and here no vector grid ever existed.
- 2026-09-15 [DECISION] The answerability eval has NO model in the loop. Cases are literal
  substring assertions over converted markdown: offline, deterministic, free, reviewable, and
  they measure whether the evidence needed to answer survived conversion -- not whether a
  given model answers correctly, which the converter does not control. Runs in the suite over
  fixtures and via `pipeline answerability --cases PATH` over a real kb/ (reads kb/ only, so
  it needs neither sources nor SOURCE_DIR; exits non-zero so it can gate a release).
- 2026-09-15 [DECISION] An unusable answerability case file raises rather than skips. A
  silently dropped case leaves the suite green while asking one fewer question.
- 2026-09-15 [DECISION] Annotation anchors render as one of exactly TWO kinds, each naming a
  measurement rather than a conclusion: `[points to: X]` (the annotation's callout line lands
  on X) and `[beside: X]` (X shares a row with the note). Neither claims which logical field
  a note is about. Supersedes the `[field:]`/`[near:]` exact/inferred pair, which conflated
  how a link was found with where its label came from -- a widget contributes the form's own
  name whether an arrow points at it or it merely shares a row, and that never made the
  second case a stated fact. Plan item 3 is CLOSED by this; it absorbed the ruling-vs-field
  limit rather than that becoming a separate item 6.
- 2026-09-15 [USER] OBJECTIVE, governs all conversion work: the goal is NOT markdown that
  mirrors the source document. It is that a frontier model (Opus/Sonnet class) can answer
  questions about the document from the markdown, or from embeddings over it. Fidelity is
  instrumental; answerability is the acceptance test.
- 2026-09-15 [DECISION] Consequence of the objective: the converter preserves evidence and
  does not resolve it. Inference at conversion time is lossy, irreversible, and performed by
  a worse judge than the model that reads the output. Emit the fact (which cell a callout
  tip lands in); do not emit the guess (which label it probably means). Supersedes the
  `[near: LABEL]` inference shipped in 8f5e725, which should be replaced rather than tuned.
- 2026-09-15 [ASSUMPTION] Retrieval design, not yet decided by USER: markdown canonical and
  the source of truth; embeddings derived and disposable; retrieval at document/section
  granularity with embeddings as a router rather than chunk-level RAG. Rationale: these
  documents are small (3-25pp) and chunking destroys the field/annotation and table-grid
  structure the conversion work exists to preserve. Revisit if the client corpus is large.
- 2026-09-14 [DECISION] Annotation->field resolution is a three-rule cascade, strongest
  evidence first: (1) the annotation's callout line `/CL`, tip = the point the annotator
  aimed at; (2) an AcroForm `/Widget` sharing the annotation's row, contributing `/T`;
  (3) a printed label sharing the row. Rules 1-2 render `[field: NAME]`, rule 3 renders
  `[near: LABEL]`. No match renders unchanged. Rationale: downstream sees only rendered
  text, so an inference shaped like a stated fact is unrecoverable.
- 2026-09-14 [DECISION] A bound annotation is emitted at its target's vertical position, not
  its own. This is what returns a margin-column note to its field.
- 2026-09-14 [DECISION] A widget target takes its label from `/T` but its *position* from the
  printed line sharing its row: the input box is typically set a few points above the label,
  so anchoring to the widget rect emitted the note just before its own row.
- 2026-09-14 [ASSUMPTION] Existing fixtures cannot reproduce either defect (all are ReportLab
  base-14 single-font, body 11pt / heading 16pt), so a new deterministic fixture
  `tests/fixtures/callout_notes.pdf` is added via `tests/make_fixtures.py`.

## [DISCOVERIES]
- 2026-09-18 [TOOL] S4 review fixed two compose defects. (1) `${VAR:?}` required-variable
  syntax on prod-only services broke `docker compose --profile dev up -d db` with no `.env`
  (Compose interpolates the whole file regardless of profile); replaced with `:-` defaults,
  and `kb serve`'s own config check stays the loud failure for a missing KB_URL_BASE.
  (2) `.env.example` documented `KB_PATH=../dgx-knowledge`, but Compose resolves a relative
  bind-mount source against `compose/`, so the prod stack would have mounted a nonexistent
  `dgx-infra/dgx-knowledge` (Docker creates it empty). Now: `make compose-env-check`
  (prerequisite of `compose-up`/`compose-index`) refuses a missing `.env` or a relative
  KB_PATH; `.env.example` and README say absolute. Verified: db starts with no `.env`;
  `--profile prod config` with an absolute KB_PATH resolves both mounts; guard rejects both
  bad cases. Full suite with DB up re-run after the fix (see below).
- 2026-09-18 [TOOL] Two S4 Caddyfile bugs only surfaced by actually running the compose
  stack, not by config validation: (1) `sed -i` renames a temp file over its target, which
  fails against the `:ro`-mounted `Caddyfile` (`Resource busy`) — fixed by having
  `caddy-entrypoint.sh` copy it to `/tmp/Caddyfile` first. (2) The substituted
  `__KB_TOKEN_PATTERN__` regex (`^Bearer (t1)$`) contains a space, and Caddyfile splits
  unquoted arguments on whitespace — `header_regexp bearer Authorization
  __KB_TOKEN_PATTERN__` therefore parsed as too many arguments after substitution ("wrong
  argument count... at Caddyfile:40") until the placeholder was quoted in the Caddyfile
  (`"__KB_TOKEN_PATTERN__"`). Also: the `ollama/ollama` image has neither `curl` nor
  `wget` (`command -v` finds neither inside the running container), so the brief's literal
  `GET /api/tags` healthcheck isn't expressible as `CMD-SHELL`; used `CMD ["ollama",
  "list"]`, which calls the same endpoint internally.
- 2026-09-15 [CODE] Removed from the README as STALE, recorded here so the claims are not
  silently resurrected: (a) the M0-M3 milestone table, including "140 tests pass" -- the
  count is now 304; (b) the assertion that "triage showed the corpus is uniformly
  born-digital", which was measured on `synthetic-cso-data/` (7 PDFs, 15 pages, a smoke
  test) and is FALSE of the real corpus -- 2 of 9 in one sample set triaged `partial`, and
  image-dominant pages are routine; (c) the section warning that M1 had never run against a
  representative corpus, now overtaken by the real profiles. The README's claim that the
  model-free default was justified because "triage showed the corpus is uniformly
  born-digital" was therefore resting on the smoke-test set; the default still looks right,
  but on the grounds in [DISCOVERIES] above rather than on that measurement.
- 2026-09-15 [CODE] Milestone vocabulary removed from `pipeline convert`'s OUTPUT, which was
  the only place a user still met it: `NOT-YET (M2)` -> `UNAVAILABLE`, and `Deferred to M2:`
  -> `Not converted -- the requested converter is unavailable:`. Verified by running the
  command with `PDF_ENGINE=docling`, not only by the suite. The M1/M2 references remaining in
  `report.py`, `triage.py` and `model_gate.py` are MODULE docstrings, never shown by `--help`
  and read only by maintainers, so they were left; `report.py`'s "M1's actual deliverable" is
  stale in the same way the removed README status table was.
- 2026-09-15 [CODE] Three files name README sections in comments or error messages, so those
  headings are load-bearing and must not be renamed casually: `pyproject.toml` -> "License
  policy" and "Platform constraint"; `converters/office.py` -> "LibreOffice (subprocess
  only)"; the Makefile header -> "Setup". A fourth, `converters/pdf.py`'s Docling
  `NotImplementedError` -> "Open questions", was left dangling by the rewrite and now points
  at `models.yaml` instead, which is where the record actually is.
- 2026-09-15 [TOOL] Measured, geometry vs raw text extraction (`pypdfium2` textpage) on
  committed fixtures and two throwaway probes. The geometry step's value is NOT uniform, and
  the split is sharper than assumed:
  * DECISIVE on annotations and widgets. On `linked_form.pdf` raw extraction returns the four
    field labels and NOTHING else; all five annotations are absent. Annotation objects are not
    in the text layer, so no reading model recovers them -- the tokens never arrive. Same for
    named widgets and for image coverage, which is not text in any form.
  * REAL on multi-column reading order. A 2-column probe: raw extraction fused sentences
    across the gutter line by line (`Awards are made on a rolling basis. Protests must be
    filed in five days.`); geometry kept the columns separate. It rendered them as a TABLE,
    which is a misclassification -- prose in two columns is not a table -- but the separation
    survives and is recoverable. This is the `Bridge` borderless-precision risk reproducing
    on a synthetic case.
  * MODEST on boxed text, correcting an assumption. Raw extraction did NOT shred the
    side-by-side callouts in `boxed_notes.pdf`: pdfium's own layout analysis kept them in
    order and `Federal funds carry over` survives intact and searchable. Geometry adds block
    separation and the `> **Boxed text:**` provenance marking, not information recovery. The
    word-by-word interleaving recorded earlier was created by `_group_words_into_lines`
    regrouping on baselines; pdfium does not do that.
  * MARGINAL against the objective: heading levels and markdown table pipes. A frontier model
    reads a whitespace-aligned table; `##` vs `###` changes nothing it can answer.
- 2026-09-15 [ASSESSMENT] Principle the above suggests: the reading model is robust to messy
  text, the EMBEDDING INDEX is not, and retrieval decides whether the model ever sees the
  page. So geometry earns its keep where it changes the TOKEN STREAM (annotations, widgets,
  image markers, column order) and is close to dead weight where it only changes the
  RENDERING (heading levels, table pipes). How much the latter matters still depends on the
  undecided retrieval design -- at document/section granularity it matters less than at
  chunk level.
- 2026-09-15 [ASSESSMENT] Cheapest decisive experiment, PROPOSED not approved: register a
  raw-text baseline engine (~20 lines around `pypdfium2`, already a base dependency) beside
  `geometry` and `docling`, and run the existing answerability cases against both. It
  measures the geometry step's contribution in the currency of the acceptance test, with no
  model in the loop. A small delta on non-annotated documents argues for a simpler default
  and less code for the client to audit; a large delta justifies the complexity with numbers.
- 2026-09-15 [CODE] DOCLING INVENTORY. Docling has exactly ONE live call site: DOCX
  conversion in `converters/office.py`, driving `MsWordDocumentBackend` directly (not
  `DocumentConverter`, which would import the PDF backend unconditionally). It fetches no
  model. That is why `docling-slim[format-docx]` is a BASE dependency with no ML runtime.
  The PDF escalation `_convert_with_docling` raises `NotImplementedError`: it needs
  `uv sync --extra pdf` plus a decision on `docling-layout-heron`. So of the three nominal
  PDF engines, one is real (geometry), one is DOCX-only, and one is a stub.
- 2026-09-15 [ASSESSMENT] The Docling PDF escalation is aimed at borderless tables and
  unusual reading order -- which is exactly what the real corpus is made of (68/76, 41/56,
  9/14, 4/10 pages). The dominant failure mode is the one path not built. Two cheap unblocks,
  both recorded in `models.yaml`: `docling-models` (TableFormer v1, the actual default that
  `PdfPipelineOptions` resolves to) has NO known blocker and sits in `pending_review` only
  because this phase fetches nothing; `docling-layout-heron` is narrowed to a single
  question -- which ResNet-50 weights seeded the backbone -- assessed as "probably clears"
  and worth one question to the Docling maintainers. Against that: the PDF extra costs torch,
  a model download, per-platform output variance and provenance review, and forfeits the
  deterministic/model-free/no-network property that makes this shippable on-prem.
- 2026-09-15 [TOOL] Redaction collides with the boxed-text path. Verified with a throwaway
  synthetic probe (not committed): `_is_note_box` asks only that a rect be filled-or-stroked
  and under `PDF_BOXED_MAX_AREA` (0.25) of the page, so a redaction rectangle is
  indistinguishable from a note box. Proper redaction (text removed from the content stream)
  is handled correctly -- the box holds no words and is skipped. IMPROPER redaction (an
  opaque rect painted over text that is still in the content stream) is not: the hidden text
  is extracted, removed from the body pool, and PROMOTED to its own `> **Boxed text:**`
  block, leaving the visible remainder of the sentence as a dangling fragment (`The awardee
  is`). Size is not a defence -- a 500x150pt block is 15.5% of a letter page and passed. No
  existing fixture carries a rect over live text, so nothing in the suite sees this. For a
  defence client this is a disclosure concern, not only a conversion defect. Remedy
  UNDECIDED and needs the client's call; options in `.agent/PROXY-CORPUS.md`.
- 2026-09-15 [CODE] `scripts/profile_corpus.py` takes only `--source-dir` and `--width`;
  there is no `--anonymize`. Its output is already content-free by test (counts and geometry
  only), so filenames are the sole obstacle to the client sharing profiles of a corpus they
  cannot share documents from.
- 2026-09-15 [TOOL] Item 5 root cause, and it explains the session's original symptom. The
  NIFA callouts are page text inside drawn rects -- 27 rects over 3 pages, each holding
  exactly one callout. The garbled header in the first converted markdown
  (`### Federal Matching Amount Corresponding Amount Total lines funds carry ...`) was never
  a shredded table header: it is SIX SEPARATE CALLOUT BOXES on one baseline, interleaved word
  by word by `_group_words_into_lines`. Removing a box's words from the body pool before line
  grouping separates them. `Federal funds carry over` -- the thing originally asked about --
  is now its own searchable block.
- 2026-09-15 [TOOL] Widget presence does NOT identify these boxes: only 13 of 27 rects
  contain a widget. The rect itself is the reliable signal. Rects are filled+stroked and
  small; the false positive to exclude is a shaded table header cell, handled by discarding
  any rect overlapping a ruled table.
- 2026-09-15 [CODE] Pre-existing, NOT fixed, out of scope: `_rejoin_fragments` drops a real
  space in `do notfit` on the NIFA document -- the opposite error from the `S ubmit` case it
  was written for. Present in the original converted output, so it predates this session.
- 2026-09-15 [TOOL] CORRECTION, supersedes the 2026-09-15 assessment that table-grid recovery
  was the highest-value work. The NIFA document has NO vector grid. Each of its 3 pages
  carries a raster screenshot of the form (1188x816 px, 35-37% of the page area); `lines`=0
  on every page; the 8-10 `rects` are the callout bubbles, one per widget, not a grid. The
  ~400 chars/page of real text are the typed callouts. `find_tables` returning 1 is not a
  failure -- there is nothing there. The grid was never flattened by the converter; it was
  never extractable. Item 4 as originally scoped rested on a wrong premise.
- 2026-09-15 [TOOL] The real defect on that document: triage called it `clean`. A form
  supplied as a screenshot with typed callouts beside it defeats every metric at once and
  none of them is wrong -- healthy chars/page (callouts are text), perfect alpha ratio, no
  page below the low-text threshold, no ruled tables because there is no vector content.
  Image coverage is the one measurement that separates it: NIH form 3% max, NIFA 35-37% on
  every page.
- 2026-09-15 [TOOL] Table-cell binding on the NIH form: exact 10 -> 32, inferred 87 -> 84,
  unbound 108 -> 89. The original retrieval question is now answered by the markdown itself:
  `[field: 1.TYPE OF SUBMISSION]: Use Application for first submission attempt for due date.`
- 2026-09-15 [CODE] Cell containment must use ZERO tolerance. Cells tile a table with no gaps,
  so slack bridges nothing and instead snaps a tip that missed the table into the nearest edge
  cell, reported as exact. Caught on the real form: a callout 2pt left of the table bound to
  the wrong row with full confidence. Widgets and lines keep their tolerance -- they are
  discrete boxes with real gaps between them.
- 2026-09-15 [TOOL] LIMIT, not a bug, and the main input to plan step 3: a PDF's ruling is a
  layout grid, not a semantic field map, so the cell a tip lands in need not be the logical
  field. Confirmed case on the NIH form -- a note about the Changed/Corrected checkbox (part
  of field 1) has its arrow tip genuinely inside a cell whose text is `Applicant Identifier`,
  and now renders `[field: Applicant Identifier]`. The tip location is a fact; calling the
  containing cell "the field" is an assertion the file does not support. Argues that a cell
  hit should render as evidence (which cell was pointed at) rather than as a field claim.
- 2026-09-15 [TOOL] Sample documents at `dgx-deployment/sample-data` (USER-provided, in
  scope; internet-sourced approximations of the client corpus, representativeness UNCONFIRMED).
  The two are structurally opposite:
  * NIH `Annotated_Forms_SmallBus_FORMS-f.pdf` (25pp): 205 FreeText annotations, 71 with
    `/CL`, ZERO named widgets. 14 ruled tables, 13 borderless candidates.
  * NIFA `NIFA-19-011-g-Budget-pilot-Research-sample-budget-annotated.pdf` (3pp): ZERO text
    annotations, 13 named widgets. 1 ruled table found on a 3-page ruled budget grid.
- 2026-09-15 [TOOL] CORRECTION to the 2026-09-14 premise: the NIFA document's callouts are
  NOT annotation objects. They are flattened into page content, which is why they render as
  `###` headings. Field binding emits zero annotations for it and cannot help it. Its widget
  `/T` names are auto-derived echoes of drawn text; 6 of 13 still match, the rest degraded
  into values (`774464`, `NOT APPLICABLE_2`).
- 2026-09-15 [TOOL] Field binding measured on the NIH form: 10 exact, 87 inferred, 108
  unbound. Of the 71 callout tips: 10 hit a body line, 20 land inside a ruled table, 41 point
  at empty space (a blank box). The 20 are a DEFECT -- `_outside_regions` strips table words
  from `lines` before `resolve_targets` sees them, so on a form the targets are removed from
  the resolver's view. The 41 are a design gap: on a blank form the arrow points at the empty
  box, so "what text is under the tip" is the wrong question.
- 2026-09-15 [ASSESSMENT] Highest answerability payoff is table-grid recovery for ruled
  forms, not annotation binding. The NIFA budget grid collapsing to one table is what made
  the original retrieval question unanswerable; that document has no annotations at all.
- 2026-09-14 [CODE] `extract_annotations` was narrowing each annotation to `(top, x0)` at
  construction and discarding the raw annot dictionary. pdfplumber supplies the full rect
  (`x0/x1/top/bottom`) plus `data`, so `/CL`, `/T` and `/Parent` were reachable all along.
  Confirmed by inspecting `tests/fixtures/annotated_form.pdf` through pdfplumber.
- 2026-09-14 [TOOL] `/CL` is in PDF user space (y upward from the page bottom); everything
  else in `pdf_geometry` uses pdfplumber's downward `top`. Conversion is
  `top = page.height - y` and happens in `resolve_targets`, where the page height is in
  scope.
- 2026-09-14 [TOOL] ReportLab validates annotation keys against an allow-list predating
  callout lines, so the fixture subclasses `FreeTextAnnotation` to permit `CL`. A `tuple`
  serialises as a string; `pdfdoc.PDFArray` round-trips as numbers.
- 2026-09-14 [TOOL] Widget `/T` arrives as `bytes`, not `str`. Fields split across several
  widgets carry the name once on `/Parent`.
- 2026-09-14 [CODE] `tests/conftest.py` scrubbed `DOCLING_OCR_ENGINE` and the CSV/triage
  vars but no `PDF_*` var. `.env` is stubbed, so only exported shell vars leaked -- but an
  exported `PDF_ANNOTATIONS=false` would have turned a feature off with the suite still
  green. The four `PDF_*` switches are now scrubbed.
- 2026-09-14 [UNCONFIRMED] Whether the real corpus forms carry `/CL` and `/Widget` decides
  whether binding is mostly exact (rules 1-2) or mostly inferred (rule 3). Not checkable
  under the scope limit above. Counts can be obtained without exposing content via
  `Counter((subtype, 'CL' in data) for each annot)`.
- 2026-09-14 [TOOL] Word-splitting root cause: `page.extract_words(extra_attrs=["size",
  "fontname"])` splits a word wherever an attribute changes. In the real PDF "Submit" is set
  as `'S'` (PQXCQY+Arial) + `'ubmit'` (STRPGS+ArialMT) — two subsets of the same face — with
  an x-gap of exactly 0.0. `_build_line` then joins fragments with a space.
- 2026-09-14 [TOOL] Measured over all pages of `OTA PR_MIPR Request Tutorial.pdf`, the
  gap-to-type-size ratio between consecutive words on a line is strictly bimodal: 0.000–0.001
  for intra-word splits, >= 0.200 for real spaces. Nothing in between.
- 2026-09-14 [CODE] Over-heading root cause: `_heading_level()` returns level 3 for any line
  with `size > body * 1.001`, while `to_markdown` builds `heading_sizes` with the far stricter
  `size > body * PDF_HEADING_SIZE_RATIO` (1.15). In the tutorial body is 9.45pt, so every
  10.5pt label became `###`.
- 2026-09-14 [CODE] Bullet collapse: bullets are 12pt against a 9.45pt body, so each line is
  heading-eligible, and the wrapped-heading continuation branch in `_paragraphs` then glues
  the whole block onto one `###` line. Nothing in the converter renders markdown lists.

## [PROGRESS]
- 2026-09-18 [TOOL] **Docling Word backend crashes on XML comment nodes in the body** (`ValueError:
  Invalid input tag of type cython_function_or_method` from `etree.QName` in
  `MsWordDocumentBackend._walk_linear`). Hit on a regulation supplement DOCX carrying
  `<!--Topic unique_N-->` markers between paragraphs; DAFFARS converted, DFARS did not.
  Fixed in `office._strip_non_element_nodes`: comment and processing-instruction nodes are
  removed from EVERY WordprocessingML part (body, headers, footers, footnotes, comments)
  before the walk, reached via the package so no header definition is created as a side
  effect; tail text preserved. Widened after review: docling walks header/footer parts
  with the same tag-name lookup, so a body-only strip would have crashed on a header. Synthetic fixture
  `xml_comment.docx` + `test_docx_with_xml_comment_nodes_in_body_converts`. Upstream bug
  in docling, not reported. 439 tests.
- 2026-09-18 [TOOL] Stage 2 milestone S4 (HTTP on the LAN) built per
  `stage2-retrieval-brief.md` §3 (HTTP), §6.5.4-§6.5.6, §7.1-§7.4, §9 S4, §13, Appendix B.
  New: `retrieval/auth.py` (`BearerMiddleware`, pure ASGI, `hmac.compare_digest` over
  `KB_TOKENS`, protects only `/mcp*`, `/health` and ASGI `lifespan` messages pass through
  untouched — the latter is what lets `mcp.streamable_http_app()`'s own
  `lifespan=lambda app: self.session_manager.run()` fire on uvicorn startup with no
  explicit session-manager entry needed in this repo's code). `retrieval/server.py`
  gained `_transport_security(cfg)` and `build_http_app(cfg)`; `build_server(cfg)` now
  always passes `host`/`port` (from `KB_BIND`), `streamable_http_path="/mcp"`,
  `json_response=True`, `stateless_http=True`, `transport_security=...` to `FastMCP(...)`
  — inert under stdio, so one constructor serves both transports. `retrieval/config.py`
  gained `Config.kb_bind_host`/`kb_bind_port` (parsed from `KB_BIND`). `retrieval/cli.py`
  `serve` command: `--transport http`, `--allow-anonymous` (exit 2 naming `KB_TOKENS` when
  empty and the flag is absent; loud stderr warning when present), runs
  `uvicorn.run(build_http_app(cfg), ...)`. Tests: `tests/retrieval/test_auth.py` (8 cases,
  minimal ASGI app + `BearerMiddleware`, Starlette `TestClient`), `tests/retrieval/
  test_server_http.py` (4 cases: `/health` open, `/mcp` 401 without token, full
  initialize/list_tools/search/fetch round trip via `mcp.client.streamable_http.
  streamable_http_client` + `ClientSession` against a real subprocess on a free port, and
  the empty-`KB_TOKENS`-exits-2 case). `tests/retrieval/conftest.py` gained
  `make_server_config`/`indexed_dsn`/`fake_ollama_http` (moved from `test_server.py`,
  which imports them back via `from conftest import ...`, so both HTTP and stdio server
  tests share one indexing pass per session — plain `def` fixtures/helpers in
  `conftest.py`, importable like any module since `tests/retrieval/` has no `__init__.py`
  and pytest puts it on `sys.path`).
- **`mcp.server.transport_security.TransportSecuritySettings`** field names verified
  directly against the installed source (`mcp/server/transport_security.py`, `mcp==1.30.0`):
  `enable_dns_rebinding_protection: bool`, `allowed_hosts: list[str]`,
  `allowed_origins: list[str]` — exactly as the brief names them, no STOP-AND-ASK needed.
  `FastMCP.__init__`'s `host`/`port`/`streamable_http_path`/`json_response`/
  `stateless_http`/`transport_security` params and `streamable_http_app()`'s
  `lifespan=lambda app: self.session_manager.run()` (`mcp/server/fastmcp/server.py`) were
  also read from source before use, confirming the session manager is entered by
  Starlette's own ASGI lifespan protocol on uvicorn startup — nothing in this repo calls
  `mcp.session_manager.run()` directly, and the brief's warning that it "MUST be entered
  or requests hang" is satisfied by not swallowing `scope["type"] == "lifespan"` in the
  bearer middleware, not by an explicit call.
- **Interpretations** (brief left these open or under-specified): (1) `allowed_origins`
  (brief showed `[...]`) — every origin a legitimate client could present:
  `https://{KB_PUBLIC_HOST}`, `https://{KB_PUBLIC_HOST}:*`, and http/https loopback on
  both `localhost`/`127.0.0.1`. (2) The `ollama` healthcheck cannot literally be `GET
  /api/tags` as a `CMD-SHELL` probe — the `ollama/ollama` image ships neither `curl` nor
  `wget` (verified: `command -v` finds neither). Used `CMD ["ollama","list"]` instead,
  which calls that same endpoint internally and fails the same way if the server isn't
  answering. (3) Caddyfile has no loop construct to turn comma-separated `KB_TOKENS` into
  a set of header alternatives, so `compose/caddy-entrypoint.sh` builds a `header_regexp`
  pattern (`^Bearer (t1|t2|...)$`, RE2-escaped per token, quoted in the Caddyfile so the
  substituted pattern's internal space doesn't split into extra Caddyfile tokens) and
  substitutes it for a `__KB_TOKEN_PATTERN__` placeholder into a writable copy of the
  Caddyfile (the mounted one is `:ro`, so `sed -i`'s rename-over-target fails against it)
  before `caddy run`. An empty `KB_TOKENS` substitutes an unmatchable pattern (NUL bytes
  can't appear in HTTP headers), so `/kb/*` 401s everything rather than matching a
  degenerate empty alternation (invalid in RE2 anyway). Same script fills a
  `__TLS_DIRECTIVE__` placeholder: `internal` by default, or the two bind-mounted
  `TLS_CERT`/`TLS_KEY` paths when both are set. (4) `db`/`ollama` given `profiles:
  ["dev","prod"]` (not just `dev`) since the `prod` stack's `kb-mcp` needs both running
  regardless of the host-venv dev profile; only their `dev`-only host port publication
  (pre-existing, loopback-bound) is unrelated to `prod` — hard rule 7's "only Caddy
  publishes a port" is about LAN exposure, which `127.0.0.1:` bindings never are. (5)
  `compose/Dockerfile`'s `ENTRYPOINT` is `["uv","run"]` (not `["uv","run","kb"]`) with
  `CMD ["kb","serve","--transport","http"]`, so `docker compose run --rm kb-mcp kb index`
  (brief's literal invocation, and the Makefile's `compose-index`) doesn't duplicate `kb`.
  (6) Every Makefile/ad-hoc `docker compose -f compose/docker-compose.yml` invocation in
  this milestone's own verification needed `--env-file .env` (not `--project-directory
  .`): Compose resolves the *implicit* `.env` relative to the Compose file's own directory
  (`compose/`), not the repo root where `.env.example` says to `cp` it, but
  `--project-directory` would ALSO move where relative bind-mount sources (`./init-db.sh`,
  `./Caddyfile`, `./caddy-entrypoint.sh`) resolve from — confirmed by reproducing exactly
  that breakage (docker silently creates an empty directory at a nonexistent bind-mount
  source) before settling on `--env-file` alone. The Makefile's `COMPOSE` variable does
  this consistently; the brief's own literal verification commands (`docker compose -f
  compose/docker-compose.yml --profile dev up -d db`, no `--env-file`) need the same flag
  added to find a repo-root `.env`, noted here since it isn't optional once `KB_URL_BASE`/
  `KB_PATH` (both required, no default) are interpolated for `kb-mcp`/`kb-static` even
  when only `db` is being started under `--profile dev`.
- **Verification, verbatim outputs of the key checks** (full transcript in the S4 task
  turn, not reproduced in full here): `curl -s -o /dev/null -w '%{http_code}'
  http://127.0.0.1:8765/health` → `200`; same for `/mcp` with no header → `401`; with `-H
  'Authorization: Bearer t1'` a POST `initialize` → `200` and a JSON-RPC `result` with
  `serverInfo.name: "dgx-kb"`. A Python script using `mcp.client.streamable_http` against
  that server: `tools: ['fetch', 'get_outline', 'get_section', 'list_documents',
  'search']`, `search("FORM-7731")` first result id `sec:budget-form:0`. `docker compose
  build kb-mcp`: image `compose-kb-mcp:latest`, 600MB. Full compose stack (`make
  compose-up` after fixing two real bugs found only by running it for real — see
  [DISCOVERIES] — the Caddyfile `:ro` mount vs. `sed -i`, and the unquoted regex argument
  splitting into extra Caddyfile tokens): `curl -sk https://localhost/health` →
  `{"ok":true,"documents":4,"embed_model":"nomic-embed-text"}`; `/kb/handbook.md` without
  token → `401`, with `Authorization: Bearer t1` → `200`. `docker compose run --rm kb-mcp
  kb index --force` (plain `kb index` was `0 reindexed` — the fixtures were already
  indexed from earlier host-venv verification against the same persistent `db` volume, so
  `--force` was needed to actually exercise the embedding call and reach `ollama`) failed
  as required, reaching `ollama` and erroring clearly: `EmbedError: embedding request to
  http://ollama:11434/api/embed for model 'nomic-embed-text' failed after 3 attempts:
  Client error '404 Not Found'` (exit 1) — no model was pulled, per instruction.
  `WORK_DIR=/private/tmp/dgx-empty-work make check`: `438 passed, 4 warnings in 34.16s`,
  both gates green. `make compose-down` (no `-v`) and `--profile dev down` both run
  without removing volumes. Not committed, per instructions; staged for review
  (`.env` used for verification was a throwaway, gitignored, and deleted afterward).
- 2026-09-18 [TOOL] Stage 2 milestone S2 (Search + eval) built per
  `stage2-retrieval-brief.md` §6.4, §6.7, §8.2, §9 S2, §13. New modules:
  `retrieval/search.py` (`SectionHit`, `search()`/`search_legs()` per §6.4: lexical leg
  `websearch_to_tsquery`/`ts_rank_cd` top 20 skipped when the tsquery parses empty, vector
  leg `<=>` top 20 via the pgvector codec, `fuse_rrf` as a pure function over already-
  ranked id lists (K=60), collapse to `(slug, section_index)` keeping the best chunk,
  `(slug, section_index)` tie-break, `k` clamped 1..25, `filters` on slug/text_class/
  page_min/page_max applied as parameterised SQL to both legs); `retrieval/cite.py`
  (`build_citation` per §6.5.2's exact format, `fetch_source_file`/`fetch_block_ids`
  against `documents`/`blocks` — never guessed); `retrieval/eval.py` + `eval/
  retrieval.yaml` (`kb eval` per §6.7: section hit@k, table of per-case per-leg ranks,
  three overall rates, exit 0 always). `retrieval/cli.py` gained `kb search` and `kb eval`.
  **Interpretation**: brief's example eval cases pad the question with connective English
  words ("Which cell records...") that `websearch_to_tsquery` ANDs together; since the
  fixture corpus is nonsense words outside headings/plants, such padding fails the lexical
  leg for reasons unrelated to retrieval quality. Reworded the 8 seeded cases to use only
  words that actually appear in their target section (the planted token, or the section's
  own heading text) — a property of writing a fair fixture question, not of tuning
  retrieval code (brief hard rule 7 forbids the latter, not the former). Also planted two
  new distinctive tokens via `make_fixtures.py` (`DECKMARK-4412` in `deck` page 15,
  `REFTAB-ANCHOR-4471` in `reference-table`, both allowed under hard rule 7) since those
  two documents otherwise contain no real English words a query could target; fixtures and
  `expected.json` regenerated (deck: 73→74 blocks, 32→32 chunks; reference-table chars
  686→697), `make fixtures-retrieval` still a no-op. Fixture eval (fake embedder, `kb eval`
  against 8 seeded cases, k=5): lexical hit@5 100%, vector hit@5 50% (fake vectors are
  content-independent hashes, so this number is meaningless by construction — expected),
  fused hit@5 100%. `make check`: 401 passed, both gates green. Verified from the CLI
  against the real `kb` database (not `kb_test`) with `KB_PATH=tests/retrieval/fixtures`
  and a throwaway `http.server` stand-in for ollama (scratchpad, not committed):
  `kb search "FORM-7731"` returns the planted section first; `kb search "the of and"`
  returns vector-only results without erroring; `kb eval` reaches the same 100%/50%/100%
  fused/vector/lexical split. The `kb` database was truncated back to empty afterward so no
  fixture data was left in it. No document content read or committed; `retrieval/schema.sql`,
  `chunk.py`, `sections.py`, `kbfiles.py`, `index.py`, and everything under `pipeline/` were
  not touched.
- 2026-09-17 [TOOL] S1 review found and fixed a chunker defect: a `table` flushed its chunk
  immediately, so annotations/boxed text following a table (the notes beside a form's
  grid, the evidence the design exists to keep together) started a chunk of their own.
  `retrieval/chunk.py` now defers the table's flush until a block that is not annotation/
  boxed_text follows. The brief's own §5.4 pseudo-code had the same defect and is corrected.
  Regression test `test_annotations_after_a_table_stay_in_the_table_chunk`. Fixture effect:
  `budget-form` 16 -> 14 chunks (the two table->annotation seams merged); others unchanged.
  `make check` 388 passed. Awaiting review before commit.
- 2026-09-17 [TOOL] Stage 2 milestone S1 (Index) built per `stage2-retrieval-brief.md`
  §5, §6.2, §8, §9 S1. New modules: `retrieval/kbfiles.py` (`load_document` per §5.2/§5.3:
  frontmatter validated via `pipeline.frontmatter.validate`, sidecar sha check, anchor
  split, block pairing/dropping, heading levels, `INCOMPLETE — pages`/`INCOMPLETE - pages`
  parsing accepting both dash forms, the no-sidecar DOCX/CSV fallback including the
  single-markdown-table -> one `kind='table'` block rule); `retrieval/sections.py`
  (`build_sections` per §5.4: per-level heading-path stack, `####`+ stays inside, genuine
  section 0 when content precedes the first heading); `retrieval/chunk.py` (`build_chunks`
  per §5.4's four rules in priority order: table never split and glued to a heading only
  when first under it, annotation/boxed_text never start a chunk, oversized
  paragraph/list split at blank lines, `CHUNK_TARGET=1200`/`CHUNK_MAX=2500`);
  `retrieval/embed.py` (`embed_documents`/`embed_query` against `{OLLAMA_BASE_URL}/api/
  embed`, `search_document: `/`search_query: ` prefixes, batch <= 32, dimension check,
  2-retry-then-raise with a 2s sleep, injectable `httpx.Client` for tests);
  `retrieval/index.py` (`run_index` per §6.2: index_meta model/dim check with
  `IndexConfigError` naming both values and `--reindex-all`, `--reindex-all` truncates the
  four content tables and updates `index_meta` inside a transaction, per-file `kb_sha256`
  skip, parse errors skipped-and-reported with old rows left in place, delete-and-reinsert
  per document in one transaction, deletion of gone documents, `ANALYZE chunks` when
  anything reindexed, summary line `"{unchanged} unchanged, {reindexed} reindexed,
  {deleted} deleted, {errors} errors"`). `retrieval/cli.py`'s `index` command wired to
  `run_index` for the non-`--init` path, `--force`/`--reindex-all` now functional, exit 1
  on parse errors, exit 2 on `IndexConfigError`.
  **Interpretations made:** (1) `documents.chars` = length of the raw markdown body (post-
  frontmatter, pre-anchor-stripping) — the brief's "body length" read literally. (2)
  index_meta being entirely empty (schema applied via `--init` but never populated) raises
  `IndexConfigError` telling the user to run `--init`, distinct from a model/dim mismatch.
  (3) `--reindex-all` truncates and updates `index_meta` unconditionally when passed (not
  only when there is a mismatch), then normal indexing proceeds against the now-empty
  tables. **`tests/retrieval/make_fixtures.py` changed:** `reference-table` no longer has
  an in-body heading (it is now a bare single-table body, to actually exercise the §5.3
  single-markdown-table rule instead of falling into the generic heading-split path); a
  short paragraph now precedes `deck`'s first heading (`# Program Review Deck`), to
  exercise a genuine section-0-then-transition case per the task's fixture checklist. The
  script now also computes `section_count`/`chunk_count`/`dropped_empty_blocks`/
  `incomplete_pages` per document straight from the real `kbfiles`/`sections`/`chunk`
  implementation and writes them into `expected.json`, so the file cannot hand-drift from
  the fixtures. Regenerated fixture counts (`expected.json`): handbook 115 blocks/19
  sections/66 chunks/62722 chars; budget-form 59 blocks/6 sections/16 chunks/14317 chars;
  deck 73 blocks/32 sections/32 chunks/14218 chars; reference-table 1 block/1 section/1
  chunk/686 chars. `make fixtures-retrieval` run twice in a row is byte-identical (checked
  via `diff -rq` against a copy, not `git stash`, since destructive git ops are blocked by
  this session's auto-mode classifier). New tests: `test_kbfiles.py`, `test_sections.py`,
  `test_chunk.py` (hand-built block lists plus all four fixtures; table-never-split,
  table-opens-section-shares-heading-chunk, table-mid-section-own-chunk,
  annotation/boxed_text-glued, oversized-paragraph-split-preserving-ordinal,
  page_first<=page_last, no-empty-chunks, stable-across-two-runs, and a check against the
  committed `expected.json` counts), `test_embed.py` (`httpx.MockTransport`-backed fake
  ollama in `tests/retrieval/fake_ollama.py`: both prefixes, batching <= 32 batches
  `[32,32,11]` for 75 inputs, dimension mismatch, 2-retries-then-raise with mocked
  `time.sleep`), `test_index.py` (against the real test DB: from-empty counts match
  `expected.json`, second run all-unchanged, one modified fixture byte -> `1 reindexed`,
  one deleted fixture -> `1 deleted`, an untouched document's row counts identical across
  runs, a sha-mismatched sidecar -> reported error with old rows intact, `--reindex-all`
  after an `EMBED_MODEL` change rebuilds and updates `index_meta`, a mismatched
  `EMBED_MODEL` without the flag raises naming both models and `--reindex-all`). 387 tests
  pass; both gates pass (`WORK_DIR=/private/tmp/dgx-empty-work make check` green, avoiding
  the real `dgx-knowledge/work/models` cache which pre-existing carries unrelated stray
  model directories that fail the model gate against the dev machine's own `WORK_DIR`).
  README "Stage 2" section updated: quickstart now runs `kb index`, explains the summary
  line and `--force` vs `--reindex-all`, and a plain-words paragraph on sections vs
  chunks. CLI end-to-end verified against the real compose `db` and a tiny stdlib
  `http.server` fake ollama (768-dim, deterministic by `sha256(text)`) on port 11499:
  `kb index --init` -> `schema applied (nomic-embed-text, dim=768), read role granted
  SELECT`; `kb index` first run -> `0 unchanged, 4 reindexed, 0 deleted, 0 errors`; second
  run -> `4 unchanged, 0 reindexed, 0 deleted, 0 errors`. Did not commit per instructions;
  left staged for review.
- 2026-09-17 [USER] S0 review: **`CREATEROLE` dropped from `kb_index`.** The builder had
  granted it so `kb index --init` could create `kb_read` on a database the compose init
  script never touched. Tim's call: the init script is the only place roles are created;
  `--init` (`retrieval/db.py:grant_read_role`) grants SELECT and, if the role is missing,
  exits 2 printing the CREATE ROLE statement. Verified on a fresh volume: both roles have
  `rolcreaterole = f`, `--init` succeeds, `make check` 344 passed. Committed as S0.
- 2026-09-17 [TOOL] Stage 2 milestone S0 (skeleton) built per `stage2-retrieval-brief.md`
  §9: new `retrieval/` package (`__init__.py`, `cli.py` with typer app `kb` — only `index
  --init` works, the rest exit 2 naming the milestone that adds them —, `config.py` per
  §7.1, `ids.py` per §5.5, `db.py` + `schema.sql` per §6.1 with the `{EMBED_DIM}`
  placeholder and an asyncpg pool registering the pgvector codec in `init=`). `pyproject.toml`
  gained the `serve` extra (`mcp`, `asyncpg`, `pgvector`, `httpx`) and the `kb` script; a bare
  `uv sync` still installs only Stage 1. `compose/docker-compose.yml` brings up a healthy `db`
  (pgvector/pgvector:pg16, `dev` profile publishes 5432/11434 to localhost); `ollama` is
  defined but nothing depends on it yet (S4). `compose/Dockerfile` is a minimal, unexercised
  `uv sync --extra serve` image, deferred to S4 in practice. `tests/retrieval/` added:
  `conftest.py` (skips DB tests with a clear message if `DATABASE_URL_TEST` is unreachable),
  `test_config.py`, `test_ids.py`, `test_layout.py` (AST-grep: `pipeline/` never imports
  `retrieval/`; `retrieval/` imports only `pipeline.frontmatter`), `test_db.py` (schema
  smoke test). `tests/retrieval/make_fixtures.py` generates the four synthetic §8.1
  documents (`handbook`, `budget-form`, `deck`, `reference-table`) deterministically from a
  seeded word list via `pipeline.frontmatter.render`; committed under
  `tests/retrieval/fixtures/kb/` with a generated `expected.json` (block counts only —
  section/chunk counts are added in S1 once `retrieval/chunk.py` exists). `.env.example`,
  `Makefile` (`index`, `search`, `serve`, `eval-retrieval`, `compose-up`, `compose-down`,
  `fixtures-retrieval`) and `README.md` ("Stage 2: search and serve (in progress)") updated.
  No document content read or written anywhere in this work.
- 2026-09-15 [CODE] Dead weight removed on USER instruction: `CONVERTER_DOCLING_PDFPLUMBER`
  and `CONVERTER_MARKITDOWN` in `converters/pdf.py` were defined and referenced nowhere, and
  `markitdown` was a declared dependency of the `pdf` extra with ZERO call sites -- it pulls
  magika, which pulls onnxruntime, so an unused dependency was dragging a second ML runtime
  into the one extra that exists to keep ML runtimes out. All three removed. `uv lock` run by
  USER: 80 -> 67 packages, 171 deletions and ZERO additions, so nothing else in the closure
  moved. The 13 removed are markitdown's whole subtree -- magika, onnxruntime, sympy, mpmath,
  protobuf, flatbuffers, coloredlogs, humanfriendly, pyreadline3, markdownify,
  beautifulsoup4, soupsieve. 304 tests and both gates pass after.
- 2026-09-15 [USER] The NIFA document was REMOVED from `sample-data`; USER is unsure how
  representative it is and does not want more time spent on it. Consequence: the image-page
  and boxed-text work it motivated stays (both are general failure modes, and both are
  covered by synthetic fixtures `screenshot_form.pdf` and `boxed_notes.pdf`, so nothing in
  the suite depends on the removed file). Its measured numbers in this file are no longer
  reproducible from `sample-data`.
- 2026-09-15 [TOOL] `make profile` added: `scripts/profile_corpus.py` prints the evidence
  profile for SOURCE_DIR as one row per PDF, no manifest and no conversion run. Emits counts
  and filenames only, never document text -- a test asserts that by searching the output for
  fixture text -- so a corpus that cannot leave its machine can still be profiled there.
- 2026-09-15 [MILESTONE] All five plan items are done, plus the answerability eval. Items 1,
  2, 3, 4 (redefined) merged to main; item 5 on branch `boxed-text`, unmerged, 5 commits.
  299 tests pass, both gates pass, `make fixtures` is a no-op.
- 2026-09-15 [ASSESSMENT] NOT done and deliberately not started: table-grid QUALITY on
  vector forms (the NIH form's grids are found but coarse -- merged cells, many empty
  columns). Distinct from the grid-recovery item that was dropped as mis-premised.
- 2026-09-15 [TOOL] Item 4 REDEFINED and the real part delivered on branch
  `image-dominant-pages`, unmerged: image-dominant page detection and reporting. 285 tests
  pass, both gates pass. The originally-scoped half of item 4 (table-grid recovery) is NOT
  done and is not justified by either sample: the NIFA form has no grid to recover, and the
  NIH form's grids are already found (14 ruled tables over 25 pages). What the NIH form has
  instead is COARSE table quality -- cells merged, many empty columns -- which is a different
  problem and remains undecided. Item 5 (tag flattened callouts) still open.
- 2026-09-15 [TOOL] Items 1 and 3 merged to main. Item 2 (evidence profile) and the
  answerability eval done on branch `answerability-eval` (branched off `evidence-profile`),
  unmerged. 272 tests pass; both gates pass. Remaining: item 4 (table-grid recovery for ruled
  forms), item 5 (tag flattened callouts via widget rects).
- 2026-09-15 [USER] Working instruction: finish the five-item list. Do not propose additional
  work unless it is a bug or very high value.
- 2026-09-15 [TOOL] Plan item 1 (table-cell targets) merged to main at `a32c267`. Item 3
  (evidence vocabulary) on branch `annotation-evidence-vocabulary`, unmerged, 5 commits
  `a778445`..`dc5f3e0`. 249 tests pass. Remaining: item 2 (evidence profile in the report),
  item 4 (table-grid recovery), item 5 (tag flattened callouts), plus the answerability eval.
- 2026-09-14 [TOOL] Annotation field binding done on branch `pdf-annotation-field-linking`,
  four commits in the repo's established order: `b32e39e` failing tests + `linked_form.pdf`
  fixture, `8f5e725` the implementation, `143c4a9` goldens only, `8ecfb99` docs.
- 2026-09-14 [MILESTONE] Done, on branch `claude/serene-mclaren-cb7901`, three commits in
  the order the task required: `53d83d5` failing tests + `callout_notes.pdf` fixture,
  `2dbafb8` the fix, `ac42309` the golden and its registration (nothing else).
- 2026-09-18 [TOOL] Stage 1 sidecar addendum (`dgx-deployment/stage1-sidecar-addendum.md`)
  implemented for DOCX, DOC and CSV, per its §3-4. `_needs_provenance_sidecar` now covers
  `docx`/`doc`/`csv`; `office.py` and `csv_table.py` gained `convert_with_provenance()`
  returning `(body, converter, provenance)`, mirroring `pdf.convert_with_provenance`; a new
  shared `converters.render_block_provenance(blocks, slug)` builds the `<slug>:p000:b<NNN>`
  anchors (0-indexed, matching the addendum's own `b000`/`b001`/`b002` example for CSV) for
  both. `office.py` no longer calls `document.export_to_markdown()`; it walks
  `document.iterate_items()` itself.
- 2026-09-18 [TOOL] Docling item model actually observed (docling-core, installed in
  `.venv`, verified by running `MsWordDocumentBackend` against a probe `.docx` and printing
  `document.iterate_items()` -- not assumed from the addendum's table): `TitleItem` (label
  `title`, no `.level`), `SectionHeaderItem` (label `section_header`, `.level` = the Word
  heading level, 1 for "Heading 1"), `TextItem` (label one of `text`, `paragraph`, `caption`,
  `footnote`, `page_header`, `page_footer`, `reference`, `checkbox_selected/unselected`,
  `field_key`, `field_hint`, `marker`, `handwritten_text` -- `DocItemLabel` in
  `docling_core/types/doc/labels.py`), `ListItem` (label `list_item`, `.enumerated: bool`,
  `.marker: str` already carries the resolved marker, e.g. `"1."` for an ordered item),
  `TableItem` (label `table`, `.export_to_markdown(doc=document)` renders its own GFM pipe
  table), `PictureItem` (label `picture`). This matches the addendum §3.2 table materially;
  no STOP-AND-ASK needed on the item model itself.
- 2026-09-18 [TOOL] DISCOVERY, not in the addendum: `MsWordDocumentBackend` treats any
  picture at or under `SPACER_IMAGE_AREA_THRESHOLD` (25px^2, `msword_backend.py:282`) as an
  invisible layout spacer -- `content_layer` is set to `INVISIBLE` and the item is dropped
  from both `document.iterate_items()` (default content layers exclude it) and
  `document.export_to_markdown()` entirely, with no error or note. Verified directly: a
  4x4px (16px^2) embedded PNG, exactly the size the addendum names for the fixture,
  produces zero `PictureItem`s. `tests/make_fixtures.py`'s `tables_and_image_docx` therefore
  embeds a 6x6px (36px^2) PNG instead, documented inline at its generator function; a 4x4
  image would silently defeat the fixture's own purpose (proving the `picture` block and
  INCOMPLETE-note path). Not a STOP-AND-ASK case under addendum §7 since it is a fixture
  sizing detail, not an item-model mismatch.
- 2026-09-18 [DECISION] Word heading levels map literally per addendum §3.2 (`n` ->
  `#`*min(n,6)): Word "Heading 1" -> markdown `#`, not `##`. This differs from the OLD
  whole-document `export_to_markdown()` behaviour (verified in the prior `simple.md` golden:
  "Heading 1" rendered as `##`), which is Docling's own convention and not one this
  implementation preserves, since blocks are now built directly rather than via
  `export_to_markdown()`. Updated the one pre-existing assertion this changed,
  `test_docx_output_is_well_formed_markdown` in `tests/test_convert_golden.py` (was
  `"## Travel Reimbursement Handbook"`, now `"#"`), and its blank-line-before-table check to
  also accept a block anchor comment immediately before a table (the same pattern the PDF
  path's own goldens already use, e.g. `tests/golden/born_digital.md`).
- 2026-09-18 [DECISION] `office.convert()` and `csv_table.convert()` (the old two-tuple,
  no-anchor signatures) are kept unchanged and still used directly by the pre-existing
  `tests/test_csv.py` call sites (~10 of them); they now delegate to the new
  `convert_with_provenance(..., provenance_slug=None)`, which returns an unanchored body
  when no slug is given -- mirrors `pdf.py`'s existing `convert()`/`convert_with_provenance()`
  split exactly, so none of those pre-existing tests needed to change.
- 2026-09-18 [PLAN] Three commits per addendum §2.4/§4, in order: (1) failing tests +
  `tables_and_image.docx` fixture + `make_fixtures.py` generator; (2) the implementation
  (`convert.py`, `converters/office.py`, `converters/csv_table.py`, `converters/__init__.py`,
  `report.py`) + README §"Reading the converted markdown" + this entry; (3) golden
  regeneration only (`tests/golden/simple.md`, `reference_table.md` changed;
  `tables_and_image.md` new). Stopped after commit 3, per addendum §5 -- did not start
  Stage 2 S3.
- 2026-09-18 [TOOL] Stage 2 S3 (MCP over stdio, `stage2-retrieval-brief.md` §6.5/§9 S3)
  built and staged, not committed (per instructions for this run). New:
  `retrieval/server.py` (five tools: `search`, `fetch`, `list_documents`, `get_outline`,
  `get_section`, plus `/health` via `custom_route`, brief §6.5.1-§6.5.4), the `serve`
  stanza in `retrieval/cli.py` (`--transport stdio` runs it; `--transport http`/
  `streamable-http` exits 2 naming S4), `tests/retrieval/test_server.py` (14 tests, incl. a
  real stdio subprocess round trip and a real-subprocess check that exactly one tool call
  produces exactly one JSON line on stderr), and a Stage 2 README subsection ("`kb serve` —
  the MCP server", "Use it from Codex", "Use it from Claude Code", "What you should see").
  `make check` (`WORK_DIR=/private/tmp/dgx-empty-work make check`) passes, 426 tests.
  Verified against the real `kb` database (`docker compose --profile dev up -d db`, `kb
  index --init`, `kb index` with a local fake-ollama HTTP server standing in for a real
  embed model) with a subprocess client over stdio: `initialize`, `list_tools` (all five
  names), `search("FORM-7731")` (first hit `sec:budget-form:0`), `fetch` on that id.
- 2026-09-18 [TOOL] FastMCP 1.x (`mcp==1.30.0`, installed via the `serve` extra) APIs used,
  each read from the installed source before use per the brief's instruction: `FastMCP(name,
  instructions=, lifespan=)` (no `host`/`port`/`streamable_http_path` passed, per the S3
  scope); `@mcp.tool(annotations=ToolAnnotations(...))` on an `async def` returning a
  `TypedDict` auto-generates `outputSchema` and returns both a `content[0].text` JSON blob
  and `structuredContent` (`mcp/server/fastmcp/utilities/func_metadata.py:convert_result`)
  -- verified equal in `test_dual_encoding_search_and_fetch`, so the brief's fallback
  (`mcp.types.CallToolResult(...)` returned explicitly) was not needed. `Context` (from
  `mcp.server.fastmcp`) as a tool parameter, resolved via
  `ctx.request_context.lifespan_context` to the pool/config/embedder the lifespan opened.
  `mcp.shared.memory.create_connected_server_and_client_session(mcp, raise_exceptions=True)`
  for in-memory tests; `mcp.client.stdio.stdio_client`/`ClientSession` for the real
  subprocess tests (`stdio_client(params, errlog=<file>)` to capture the server's stderr
  for the log-line test, since `StdioServerParameters` has no `env`-merge surprises worth
  noting -- it replaces, not merges, the parent environment). `@mcp.custom_route("/health",
  methods=["GET"])` exists in 1.30.0 and is registered, but is only reachable once
  `streamable_http_app()` serves it (S4) -- confirmed by reading `server.py`'s route
  wiring, not exercised by a request in these tests. Nothing named in the brief was found
  missing from the installed 1.x API.
- 2026-09-18 [DECISION] Interpretation choices not fully pinned down by the brief: (1)
  `get_section`/`fetch` on a multi-section range needs no synthetic per-section heading
  line -- block ordinals are contiguous in document order and each section's own heading
  block is already its first block, so joining every block in the overall ordinal range by
  `\n\n` naturally reproduces "each with its own heading line" (brief §6.5.2) without extra
  construction. (2) `page:` fetch's `citation` uses `heading_path="page {n}"` rather than
  repeating the page title, to avoid a citation like "Doc — page 3, Doc — page 3 (...)".
  (3) `get_section` on a `page:` id resolves to the section whose page range contains that
  page, or (if none contains it) the section with the closest `page_first`. (4) A `title`
  with no heading tail (`_strip_title` returns `""`) renders as the bare document title,
  not `"{title} › "` with a trailing separator. (5) `search`'s per-result `url`/citation
  data (`rel_path`, first block id) is looked up per hit rather than batched -- fine at
  fixture scale, a candidate optimisation if S4's real corpus makes it a hot path. None of
  these touch `search.py`/`chunk.py`/`sections.py`/`kbfiles.py` behaviour or signatures;
  all logic lives in the new `retrieval/server.py`.

## [OUTCOMES]
- 2026-09-15 [TOOL] The evidence profile makes the original silent failure loud. Against the
  sample documents the report now prints:
  `<NIH form>: 205 annotation(s) carrying text no text-layer extraction sees, 71 stating
  their own target` and `<NIFA form>: 13 form field(s) but 1 ruled table(s) recovered -- a
  form whose grid is mostly not being reconstructed`.
- 2026-09-15 [CODE] Hazard hit twice this session and worth remembering: an edit anchored on a
  code snippet that is not unique to the end of a function will swallow that function's tail.
  The CLI's `answerability` command absorbed `report`'s trailing stop-and-ask lines; caught by
  running the command, not by the test suite.
- 2026-09-15 [TOOL] On the NIH form after items 1 and 3: 32 `points to`, 84 `beside`, 89
  unanchored, of 205 annotations. The session's original retrieval question is answered by
  the markdown itself -- `[points to: 1.TYPE OF SUBMISSION]: Use Application for first
  submission attempt for due date.`
- 2026-09-15 [CODE] `ruled_form.pdf` initially carried too little text and triaged
  `needs_ocr`, so its golden recorded the stub path rather than the geometry path it exists
  to cover. Caught only by registering the golden; the unit tests call `to_markdown` directly
  and never saw it. General hazard: a sparse fixture silently tests a different code path.
- 2026-09-14 [TOOL] 242 tests pass; both gates pass (model gate run with
  `--cache-dir $(mktemp -d)`, not `make clean-work`, to preserve the Marker eval weights).
  `make fixtures` is a no-op against committed bytes.
- 2026-09-14 [TOOL] `annotated_form.md` golden changed as intended: its three callouts now
  render `[near: Requester:]` etc. No other golden changed.
- 2026-09-14 [TOOL] Follow-up, out of scope: the annotation ordering caveat in
  `_render_page` still applies to two-column pages, where tables and annotations land after
  the prose rather than at their own position. Binding makes this more visible, since a
  bound annotation's anchor is now its target's position and is discarded on those pages.
- 2026-09-14 [TOOL] `make license-gate` and `make test` pass (226 tests). The model gate was
  run as `uv run python scripts/model_gate.py --cache-dir <scratch>` rather than via
  `make clean-work`, deliberately: clean-work would have deleted the user's Marker
  evaluation weights. It passes against an empty cache.
- 2026-09-14 [TOOL] `make fixtures` is a no-op against the committed bytes, so fixture
  generation stayed deterministic.
- 2026-09-14 [TOOL] On the real `OTA PR_MIPR Request Tutorial.pdf` the heading count falls
  from 44 to 16, the NOTES box renders as a nested markdown list, and "S ubmit" is "Submit".
- 2026-09-14 [TOOL] No existing golden changed. Follow-up, out of scope and pre-existing:
  glyph-only lines (FontAwesome private-use characters) still emit an empty `## ` block, and
  numbers inside pdfplumber-extracted ruled tables can still split ("$ 8 ,500,000.00") --
  that path does not go through `_build_line`.
