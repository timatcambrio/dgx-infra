# CONTINUITY

Canonical briefing for the PDF-geometry output-quality task. Facts only.

## [PLANS]
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
