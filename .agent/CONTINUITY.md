# CONTINUITY

Canonical briefing for the PDF-geometry output-quality task. Facts only.

## [PLANS]
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
- 2026-09-14 [TOOL] Annotation field binding done on branch `pdf-annotation-field-linking`,
  four commits in the repo's established order: `b32e39e` failing tests + `linked_form.pdf`
  fixture, `8f5e725` the implementation, `143c4a9` goldens only, `8ecfb99` docs.
- 2026-09-14 [MILESTONE] Done, on branch `claude/serene-mclaren-cb7901`, three commits in
  the order the task required: `53d83d5` failing tests + `callout_notes.pdf` fixture,
  `2dbafb8` the fix, `ac42309` the golden and its registration (nothing else).

## [OUTCOMES]
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
