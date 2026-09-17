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
