# dgx-infra — document conversion

Point this at a folder of documents (PDF, DOCX, legacy DOC, CSV). It writes clean markdown
with metadata, and a report saying which documents converted completely and which did not.

Nothing here uses a model. No OCR, no LLM, no ML runtime, and nothing is downloaded while it
runs. PDFs are rebuilt from character geometry: word positions, type sizes, ruling lines. So
it works offline, gives the same output every time, and you can read the extraction code
instead of trusting weights.

Some things cannot be recovered that way: scanned pages, screen captured tables, etc. The
pipeline generates a report of potential failure points, and marks them in the converted
files as well. **Please check [Reading the report](#reading-the-report) to avoid these
silent failures, and [Reading the converted markdown](#reading-the-converted-markdown)
for the markers.**

---

## Setup

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.12 and every dependency.

```bash
uv sync
cp .env.example .env
```

Then open `.env` and set one line:

```bash
SOURCE_DIR=/absolute/path/to/your/documents
```

That is the only setting you have to fill in. Everything else in `.env.example` is commented
out with its default shown. To change any of the others, uncomment the corresponding line
and edit it.

Legacy `.doc` and `.dot` files also need LibreOffice. See [LibreOffice (subprocess
only)](#libreoffice-subprocess-only). Every other format works with `uv sync` alone.

### Where things go

| | |
| --- | --- |
| Your documents | `SOURCE_DIR`, read-only. Nothing is written, moved, renamed, or deleted here. |
| Converted markdown | `kb/` in the companion `dgx-knowledge` repo. Clone it beside this one. |
| The record of what came from where | `corpus.yaml`, also in `dgx-knowledge` |
| Scratch files | `work/`. Disposable: deleting it costs time, never information. |

Source documents live outside both repos on purpose. If `SOURCE_DIR` is unset, commands stop
with an error rather than guess.

---

## Running it

Four commands, in order:

```bash
make inventory    # find the documents and record them in corpus.yaml
make triage       # measure how much readable text each PDF has
make convert      # write the markdown into kb/
make report       # print what happened
```

Each one can be re-run. `convert` skips documents that have not changed since last time; add
`--force` to redo them anyway. Every command takes `--only SUBSTRING` to work on one
document, and `--source-dir PATH` to override `.env` for a single run.

`make profile` prints one row per document straight from the PDFs, with no manifest and no
conversion run. Use it to see what a folder holds before you commit to anything. It prints
counts, never document text, so you can run it on a corpus that cannot leave the machine it
sits on and paste the result into a ticket.

---

## Reading the report

### The class on each document

`triage` measures three things per PDF and turns them into a class:

| Class | Means |
| --- | --- |
| `clean` | Every page has usable text. Convert and move on. |
| `partial` | Usable overall, but a fair number of pages gave almost nothing. Usually a handbook with scanned appendices. Those pages will be missing from the markdown. |
| `needs_ocr` | Not enough readable text to convert. Needs OCR, which this pipeline does not do. |
| `error` | The file could not be opened. Encrypted files get their own message. |

DOCX and CSV skip triage, since their text is structural rather than drawn on a page, and are
recorded as `clean`.

The three measurements behind the class:

| Setting | Default | What it catches |
| --- | --- | --- |
| `MIN_CHARS_PER_PAGE` | 100 | A page with less than this counts as low. The median is used rather than the mean, so a few dense pages cannot hide a scanned majority. |
| `MIN_ALPHA_RATIO` | 0.60 | Measured over the whole document. A PDF with a broken font map extracts plenty of characters but they are garbled and unusable. The alpha ratio is the share of characters that are alphanumeric, whitespace, or punctuation. Everything else, such as replacement characters and private-use glyphs, counts against it, so a lower ratio means more garbled text. |
| `MAX_LOW_PAGE_FRACTION` | 0.20 | Above this share of low pages, a document is no longer `clean`. |

These measurements diagnose the corpus and inform decisions taken later in the pipeline.
They also decide how each document is handled: one classed `needs_ocr` gets a stub written
instead of a conversion. Avoid changing them.

### LOW-TEXT PAGES

Pages inside otherwise usable documents that gave almost no text. Check them by eye. A cover
page or a divider is fine. A scanned figure is content that will be missing from `kb/`.

### LAYOUT NOTES

Where the geometry most likely struggled. Two kinds:

- Multi-column pages. Handled, but the likeliest place for reading order to go wrong. Check
  the converted output.
- Borderless tables, recovered by noticing that several consecutive lines split into aligned
  columns. Spot-check these. The bias is towards missing a table rather than inventing one,
  because an invented table destroys the paragraph it swallows.

### EVIDENCE NOTES

This is the section that catches a document failing quietly.

It reports what a document carried on top of its text layer, against what came back out:

```
EVIDENCE NOTES (what the document carries beyond its text layer)
  <a 25-page annotated form>: 205 annotation(s) carrying text no text-layer
    extraction sees, 71 stating their own target
  <a 3-page budget form>: 3 page(s) that are mostly image, up to 36.8% of the
    page -- that content is not in the text layer and no table extraction reaches it
```

The second line is the one to understand. A form supplied as a screenshot, with typed notes
beside it, beats every coverage measurement at once, and none of them is wrong:

| Measurement | Says | Because |
| --- | --- | --- |
| chars/page | healthy | the typed notes are real text |
| alpha ratio | perfect | that text is clean |
| low-text pages | none | every page clears the threshold |
| ruled tables | none found | a picture has no vector lines to find |

The document comes out `clean`, converts without complaint, and leaves out the entire form it
is about. Measuring how much of each page is raster image is the only thing that separates it
from a document that is genuinely fine.

---

## Reading the converted markdown

Every file opens with frontmatter recording where it came from, what converted it, and its
text coverage, including `needs_ocr: true` where that applies. Whatever consumes the markdown
can then tell a complete document from an incomplete one without working it out again.

For PDFs converted by the geometry engine, each content block also has a stable HTML comment
anchor immediately before it:

```markdown
<!-- dgx:block=travel-handbook:p012:b004 -->
| Expense category | Limit | Receipt required |
```

The matching sidecar lives next to the markdown as `<slug>.provenance.json`. It maps each
block id to the original source page and, when geometry has it, the page bounding box:

```json
{
  "version": 1,
  "source_file": "travel_handbook.pdf",
  "source_format": "pdf",
  "content_sha256": "...",
  "converter": "pdfplumber-geometry (model-free)",
  "blocks": [
    {
      "block_id": "travel-handbook:p012:b004",
      "page": 12,
      "kind": "table",
      "confidence": "geometry",
      "bbox": [72.0, 144.0, 520.0, 310.0]
    }
  ]
}
```

When an answer needs verification, cite both places: the markdown file and `dgx:block=...`
for the extracted text, plus the sidecar's `source_file` and `page` for the original PDF.
The `content_sha256` in frontmatter and sidecar must match; if it does not, the markdown no
longer proves which source bytes it came from.

Four markers can appear in the body.

### `> **INCOMPLETE — ...**`

Content that is not in the file, named:

```markdown
> **INCOMPLETE — pages 1, 2, 3 are mostly image (up to 37% of the page), and that
content is not in the text layer.** No OCR was attempted.
```

Without it a `partial` document drops its scanned pages silently, and a document missing its
pages looks the same as one that never covered the topic.

This is a report, not a verdict. A page that is one-third diagram is not broken. The marker
states the measurement, names the threshold (`IMAGE_PAGE_COVERAGE`), and leaves the call to
you.

### `> **Annotation:**`

A Markup callout or sticky note: the box someone types into when annotating a form in Preview
or Acrobat. These are attached to the page rather than printed on it, so ordinary text
extraction does not see them. On an annotated form they are often the only instructions in
the document, and losing them turns a tutorial back into a blank form.

The prefix earns its place later on, when this text gets retrieved and cited by something
that no longer has the PDF. What the form prints and what a colleague wrote onto it are not
the same claim, and a citation has to keep them apart.

Where a note can be tied to something, the marker says how it was found:

```markdown
> **Annotation** [points to: TypeOfSubmission]: Use Application for the first submission attempt.

> **Annotation** [beside: 2. DATE SUBMITTED]: Format: MM/DD/YYYY.
```

| Kind | Means |
| --- | --- |
| `[points to: X]` | The annotator's arrow lands on X. Strong evidence. |
| `[beside: X]` | X only shares a row with the note. Weaker, so treat it with more care. |
| no marker | Nothing could be tied to it. A plausible guess would be worse than none, because nothing further down the line can tell a plausible guess from a real one. |

Neither kind claims to know which form field a note is about. A PDF's ruling is a layout
grid, not a map of the form's fields. On a real annotated form, a note about a checkbox in
field 1 can have its arrow tip sitting inside a cell that names a different field. Where the
tip landed is a fact. Which field the note is about is a reading of the form, and you have
the form in front of you while the converter has coordinates.

### `> **Boxed text:**`

Text the page draws inside a box: a note flattened into the document, or authored that way.
It is ordinary page text and carries none of an annotation's provenance, so it gets its own
label. All the marker says is that the page sets this text apart in a box.

Marking them also keeps them readable. Boxes standing side by side share a baseline, and
without this their words interleave into one unreadable line.

### Tables

Ruled tables come from their ruling lines. Borderless ones are recovered from column
alignment and flagged in LAYOUT NOTES for a spot-check.

---

## When something looks wrong

Put the converted markdown next to the original and look. Then:

**Reading order scrambled on a two-column page.** Check LAYOUT NOTES to see whether columns
were detected at all. `PDF_COLUMN_GAP_FRACTION` sets how wide a gutter has to be to count.

**A table came out as prose, or prose came out as a table.** `PDF_MIN_TABLE_ROWS` is how many
aligned lines make a table; `PDF_COLUMN_ALIGN_TOLERANCE` is how much horizontal drift is
allowed between rows.

**Too many headings, or too few.** `PDF_HEADING_SIZE_RATIO`: how much larger than body text a
line has to be set to count as a heading.

**Running headers and footers left in.** `PDF_REPEAT_PAGE_FRACTION` and `PDF_MARGIN_FRACTION`
control how repeated margin text is found.

Every setting is listed with its default and a one-line explanation in `.env.example`. They
describe page geometry, not what the document means.

### Checking the output can still answer questions

Comparing output byte for byte catches change. It cannot catch output that is stable and
useless, like a budget grid flattened into prose, or a note stranded from the field it
describes. Both would pass a file comparison forever.

So you can write questions with known answers, and assert that the text needed to answer them
survived conversion:

```yaml
cases:
  - id: submission-type-first-attempt
    question: Which box do I check for the first submission attempt?
    document: linked-form.md
    expect:
      - "[points to: TypeOfSubmission]: Use Application for the first submission attempt."
```

```bash
uv run pipeline answerability --cases /path/to/your/cases.yaml
```

This reads `kb/` and nothing else, so it needs neither the source documents nor `SOURCE_DIR`,
and it exits non-zero on failure so it can gate a release. No model is involved: the cases are
literal substring checks, which keeps them offline, repeatable, free, and open to argument by
anyone who thinks a case is wrong.

Write `expect` as the shortest string that makes the answer findable.

### Escalating a stubborn document

`PDF_ENGINE=docling` switches to a layout model. It gives better borderless-table structure
and costs a large ML runtime and a model download. Decide it per document, by looking at
converted output, rather than turning it on across the board.

It is not wired up yet, and says so when you try, naming what it needs.

---

## Things that stop and ask

Conversion stops on these rather than write something quietly wrong. Each one names the file
and the reason.

- A CSV past `CSV_MAX_ROWS` (300) or `CSV_MAX_COLS` (15). How a large table should be shaped
  for retrieval is a decision to make, not a default to pick.
- A source file that changed since it was recorded. Each entry stores a checksum, and a
  mismatch is a loud error rather than a silent re-convert.
- An encrypted or password-protected PDF.
- A file listed in `corpus.yaml` but no longer in `SOURCE_DIR` is reported `MISSING` and the
  run carries on. The entry is never deleted and the run never fails because of it.

---

## What this does not do

No OCR, no LLM calls. Chunking, embeddings, a vector store, retrieval and serving are Stage
2 (below) — not part of conversion, and not something this command runs.

---

## Stage 2: search and serve (in progress)

Once `kb/` exists, a second, separate command line — `kb` — makes it searchable and hands it
to an AI assistant (Codex, ChatGPT desktop, Claude Code, Claude desktop) over MCP. It lives
in the same repo, behind its own install step, and does not change anything above.

**Status:** skeleton only. The database schema and `kb index --init` work; indexing,
search, and serving come in later milestones and are not usable yet.

```bash
uv sync --extra serve                      # installs kb's dependencies; plain `uv sync` does not
cp .env.example .env                       # then fill in the Stage 2 keys (see below)
docker compose -f compose/docker-compose.yml --profile dev up -d db
uv run kb index --init                     # applies the database schema
```

`kb --help` lists every subcommand (`index`, `search`, `serve`, `eval`, `catalog`); all but
`index --init` currently exit with "not implemented until S<n>" naming the milestone that
adds them.

### What the Stage 2 keys in `.env` mean

`.env.example` documents every key `kb` reads, each with a one-line comment. The two that
matter to get right: `DATABASE_URL_INDEX` (the writer role `kb index` uses) and
`DATABASE_URL` (the read-only role `kb serve`/`kb search` use). `docker compose --profile
dev up -d db` creates a local Postgres with both roles already set up, matching the defaults
in `.env.example`.

### What is deliberately not built yet

Indexing kb/ into the database, search, the MCP server (stdio and HTTP), the eval harness,
and document summaries all come later, in the order in `stage2-retrieval-brief.md` §9. This
section will grow a real quickstart (adding `kb` to Codex and Claude Code, reading the eval
table, rotating a token) as those land.

---

## Development

```bash
make check    # the test suite plus both policy gates. What CI runs.
make help     # every target
```

### License policy

The rule is about how a dependency is called, not what its licence string says. Copyleft
invoked as a separate program is fine, and LibreOffice is the one case of it. Copyleft
imported as a library is not. PyMuPDF and pymupdf4llm are AGPL and import-only, so they are
banned outright, including for a quick check.

`make license-gate` walks installed package metadata and fails if a GPL or AGPL package is
imported anywhere under `pipeline/`. Real false positives go in
`scripts/license_allowlist.yaml` with a written reason.

### Model policy

Models have to be permissively licensed and have non-Chinese base-weight provenance, judged
on the base weights rather than on whoever released them.

`make model-gate` checks two places, because they fail differently: model caches, and weights
shipped inside an installed wheel. The second one is real. The `docling` meta-package
installs `rapidocr`, whose wheel carries about 30MB of Baidu PaddleOCR weights as ordinary
files that never touch a cache. So this project depends on `docling-slim` with named extras,
never on `docling`.

`models.yaml` is the allowlist and it is empty on purpose. Nothing here should ever download
a model, so an empty allowlist plus a cache scan asserts exactly that and fails the moment it
stops being true. Models a future conversion path would fetch are recorded under
`pending_review` with what is known about each.

### LibreOffice (subprocess only)

Legacy `.doc` and `.dot` conversion shells out to the `soffice` binary:

```bash
soffice --headless --convert-to docx --outdir "$WORK_DIR/doc2docx/" <file>
```

If `soffice` is missing the pipeline fails with an error naming it. There is no fallback to a
copyleft Python library.

> Pin the major version and match it across machines. LibreOffice's `.doc` import filter is
> not byte-stable between releases, and byte-stable output is a hard requirement here. The
> pin is not decided yet, so this path is currently unexercised.

### Platform constraint

`pyproject.toml` requires every locked dependency to have an installable wheel on both
linux-x86_64 and darwin-x86_64.

This is not decorative. PyTorch ships no macOS x86_64 wheel after 2.2.2, and without the
constraint `uv lock` produces a lockfile that resolves cleanly and then will not install. If
`uv lock` starts failing, a dependency has dropped one of those platforms. Decide that
deliberately instead of dropping support by accident.

Because the two platforms can resolve different versions, byte-identical output across
platforms is not guaranteed. The requirement is per machine: the same input converted twice
on the same machine must be byte-identical, which `tests/test_determinism.py` enforces.
