# CONTINUITY

Canonical briefing for the PDF-geometry output-quality task. Facts only.

## [PLANS]
- 2026-09-14 [USER] Fix two pre-existing defects in `pipeline/converters/pdf_geometry.py`:
  (1) over-heading — nearly every non-body line emitted as `###`, run-on paragraphs and
  bullet lists collapsed into one heading line; (2) word-splitting — `S ubmit` instead of
  `Submit`. Order of work is fixed: failing tests against committed fixtures first, then the
  fix, then golden regeneration in a SEPARATE commit that changes nothing else.
- 2026-09-14 [USER] Verification command is `make check`; the model gate fails while Marker
  weights sit in `dgx-knowledge/work/models`, so run `make clean-work` or point `--cache-dir`
  at a scratch directory first.

## [DECISIONS]
- 2026-09-14 [ASSUMPTION] Existing fixtures cannot reproduce either defect (all are ReportLab
  base-14 single-font, body 11pt / heading 16pt), so a new deterministic fixture
  `tests/fixtures/callout_notes.pdf` is added via `tests/make_fixtures.py`.

## [DISCOVERIES]
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
- 2026-09-14 Investigation complete; implementation not yet started.

## [OUTCOMES]
- UNCONFIRMED
