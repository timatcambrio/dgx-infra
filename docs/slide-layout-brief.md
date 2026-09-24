# Brief: fix list and table reconstruction in the geometry PDF converter

Self-contained implementation brief. Everything needed is here; you should not need to read
document content to do this work.

## Rule first: do not read document content

The corpus is internal and potentially sensitive. Working in this repo is fine. Reading what
the documents *say* is not — not the source files under `SOURCE_DIR`, not converted markdown
in `kb/`, not conversion output you dump while debugging.

**Permitted:** geometry and structure — coordinates, font sizes, cell boundaries, line and
segment counts, column and table counts, per-page metrics.
**Not permitted:** reading document text for meaning, summarising it, or quoting it into
commits, tests, fixtures, goldens, or chat. Describe *shape* ("a three-column bullet block
under a full-width heading"), never words.

Test fixtures and goldens stay synthetic, as they already are. If you dump conversion output
to inspect it, delete it when done. See `[DECISIONS]` 2026-09-11 in
`../.agent/CONTINUITY.md`.

## Where the work is

All of it in `pipeline/converters/pdf_geometry.py`, the model-free default engine.
Relevant existing pieces:

| Function | Line | Role |
|---|---|---|
| `Line.cells()` | ~50 | Splits a line at wide gaps into `(x0, text)` cells. Already does most of the segmentation work needed. |
| `detect_columns()` | ~128 | Page-level, 1-or-2 columns only, gutter must be wide and near page centre. |
| `_table_regions()` | ~163 | pdfplumber ruled-table detection. |
| `find_aligned_table_runs()` | ~364 | Borderless-table recovery from aligned cells. |
| `_paragraphs()` | ~421 | Groups lines into headings, paragraphs, tables. Joins lines with `" "`. |
| `_render_page()` | ~327 | Per-page assembly and ordering. |

## The two defects

### 1. No list model (affects every document, highest value)

Nothing converts bullets into markdown lists. `_paragraphs` joins lines with `" "`, so an
entire bulleted section collapses into one run-on paragraph with glyphs left inline. Nesting
levels (`●` / `○` / `■`) are present in the geometry and discarded.

The geometry is clean and sufficient. Representative measured shape from one page —
positions only, text elided:

```
top    x0    size  bold
276.8  65.6  11.0  B   | 66:<heading A> || 282:<heading B> || 573:<heading C>
304.3  38.8   9.0      | 39:● || 62:<item text> || 270:● || 293:<item> || 502:● || 525:<item>
315.3  61.5   9.0      | 62:<continuation> || 293:<continuation> || 525:<continuation>
330.3 270.5   9.0      | 270:● || 293:<item>
```

Note: the glyph occupies its **own cell** at a fixed x (39 / 270 / 502) and item text starts
at a second fixed x (62 / 293 / 525).

**Implement:** a leading glyph cell (`●○■▪◦‣·-–—*`, or ordered markers `1.` `1)` `a.` `a)`
`i.`) opens a `- ` item. A following line whose cell starts at the *item-text* x rather than
the glyph x is a **continuation** and appends to the current item. Nesting depth from glyph
identity plus x0. Emit nested markdown lists.

This is what turns a wrapped two-line bullet into one item instead of two fragments, and it
fixes non-slide documents too.

### 2. Hallucinated tables from slide furniture

`_table_regions()` trusts `page.find_tables()`. On slides, text-box borders, callout outlines
and decorative rules read as table borders. Observed results include ordinary prose emitted
as 3- and 5-column markdown tables, and one slide template emitted as a ~24-column table that
is almost entirely empty cells.

This is worse than the column bug: it silently destroys the content it consumes, and the
module's own docstring already states that a hallucinated table beats no table.

**Implement:** reject a detected ruled table when it looks like furniture rather than data.
Suggested signals, all measurable: empty-cell ratio above a threshold; only one non-empty
row; a single row or column carrying nearly all the text; column count implausibly high
(e.g. > `PDF_MAX_TABLE_COLS`). Fall back to treating the region as flowing text. Tune against
synthetic fixtures, not against the corpus.

## Precedence rule (required)

Tables and lists compete for the same line runs. Fix the order explicitly:

1. `find_aligned_table_runs()` gets first refusal on a run.
2. **If any line in the run has a bullet-glyph cell, it is a list, not a table.** A borderless
   table has a consistent cell count with aligned anchors and no glyphs.
3. Whatever neither claims stays prose.

## Phase 2 — multi-column bands (CONDITIONAL, do not start without confirming)

Only relevant if the Docling escalation path is rejected. **Ask before implementing.**

`detect_columns()` believes only a full-page gutter wider than `PDF_COLUMN_GAP_FRACTION`
(6% ≈ 43pt on a 720pt slide) near the page centre, returning 1 or 2. Real slide gutters
measured ~20pt with three columns, so such pages read as one column and every line is joined
left-to-right across the gutter.

If asked to proceed: replace with `find_column_bands(lines)` — over a run of consecutive
lines, project cell x-intervals and find gutters that hold for the whole run; a line whose
cells cross a gutter ends the band. Gutter threshold font-relative (≈2× space width), N
columns not 2. Then assign each **cell** to a column, sort by `top` within the column, and
emit column by column. Per-line splitting alone is insufficient — lines are not synchronised
across columns (see the `330.3` row above, which belongs to the middle column only).

Also worth adding either way: slide-likeness signals in `triage` (landscape aspect, low text
density, many short segments per page) so `report` stops claiming such pages are
single-column, and escalation becomes a measured decision.

## Constraints

- **Model-free.** No new dependency, no ML runtime, nothing fetched at runtime. pdfplumber
  and the standard library only.
- **Deterministic.** Byte-identical output across machines and re-runs is a project guarantee.
- **New knobs go in `pipeline/config.py`** alongside the existing `pdf_*` settings, with
  documented defaults. Do not tune existing thresholds to make a sample look better; if one
  is genuinely wrong, argue it with evidence.
- **Conservative bias.** A missed list beats a fabricated one, exactly as for tables.
- Heading levels currently derive from a document-wide body size, which degrades on decks
  (title / section / bullet sizes with no stable body size). Out of scope unless it blocks
  you — mention it, don't fix it silently.

## Definition of done

- Synthetic fixture PDFs covering: a single-column nested bullet list; a wrapped multi-line
  bullet; a ruled table that must survive; a slide-furniture pseudo-table that must be
  rejected. Generate them in code, as the existing fixtures are.
- Goldens for each, in `tests/golden/`.
- Existing goldens unchanged, or each change explained in the PR body.
- `make check` green. Baseline before this work: license gate PASSED, model gate PASSED,
  **193 tests passed**.
- Note: `make check` needs `uv` on PATH. Without it, run the equivalents directly:
  `.venv/bin/python scripts/license_gate.py`, `.venv/bin/python scripts/model_gate.py`,
  `.venv/bin/python -m pytest`.
- README updated where behaviour changes.
- `../.agent/CONTINUITY.md` updated under `[PROGRESS]` — facts only, ISO date, provenance
  tag, no document content.

## Background not to re-derive

The default PDF engine is model-free geometry rather than Docling by deliberate decision:
no ML runtime, nothing fetched at runtime, byte-identical output, auditable by the client.
Docling remains a per-document escalation (`PDF_ENGINE=docling`) that is **not wired up** and
is blocked on a model-provenance ruling. Do not add a model to solve a layout problem. See
`docs/parser-landscape.md` and README "Open questions".
