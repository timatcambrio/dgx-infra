# Document conversion

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

This is the `pipeline` command. On the DGX it runs in a container, as in
[step 11 of the deployment guide](deployment-dgx-guide.md#11-convert-the-test-documents).
The commands here run it directly on a machine with `uv`.

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
only)](development.md#libreoffice-subprocess-only). Every other format works with `uv sync`
alone.

### Where things go

| | |
| --- | --- |
| Your documents | `SOURCE_DIR`, read-only. Nothing is written, moved, renamed, or deleted here. |
| Converted markdown | `kb/` in the companion `dgx-knowledge` repo. Clone it beside this one. |
| The record of what came from where | `corpus.yaml`, also in `dgx-knowledge` |
| Scratch files | `work/`. Disposable: deleting it costs time, never information. |

Source documents are kept outside both repos on purpose. If `SOURCE_DIR` is unset, commands
stop with an error rather than guess.

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
counts, never document text, so you can run it on documents that cannot leave their machine
and paste the result into a ticket.

## Reading the report

### The class on each document

`triage` measures three things per PDF and assigns a class from them:

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

These measurements diagnose the document set and inform decisions taken later in the pipeline.
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
  because an invented table garbles the paragraph it absorbs.

### EVIDENCE NOTES

This section lists content a document contains outside its text layer, compared with what
the conversion extracted. It is where a document that converted without errors but lost
content shows up:

```
EVIDENCE NOTES (what the document carries beyond its text layer)
  <a 25-page annotated form>: 205 annotation(s) carrying text no text-layer
    extraction sees, 71 stating their own target
  <a 3-page budget form>: 3 page(s) that are mostly image, up to 36.8% of the
    page -- that content is not in the text layer and no table extraction reaches it
```

The second entry needs explaining. A form supplied as a screenshot, with typed notes beside
it, passes every coverage measurement, and each measurement is accurate:

| Measurement | Says | Because |
| --- | --- | --- |
| chars/page | healthy | the typed notes are extractable text |
| alpha ratio | perfect | that text is clean |
| low-text pages | none | every page clears the threshold |
| ruled tables | none found | a picture has no vector lines to find |

The document comes out `clean`, converts without complaint, and leaves out the form it is
about. Measuring how much of each page is raster image is the only way to tell it apart from
a document that converted correctly.

## Reading the converted markdown

Every file opens with frontmatter recording where it came from, what converted it, and its
text coverage, including `needs_ocr: true` where that applies. Whatever consumes the markdown
can then tell a complete document from an incomplete one without working it out again.

Every converted document (PDF, DOCX, DOC and CSV alike) has a sidecar next to it, and
every content block has a stable HTML comment anchor immediately before it:

```markdown
<!-- dgx:block=travel-handbook:p012:b004 -->
| Expense category | Limit | Receipt required |
```

The sidecar is saved next to the markdown as `<slug>.provenance.json`. It maps each block id to
the original source page and, when geometry has it, the page bounding box:

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

Word and CSV documents have no fixed page geometry, so their blocks carry `page: null` and no
`bbox` at all. The block's position in the file is the only locator, and its id still names
the document (`travel-handbook:p000:b004`; `p000` reads as "no page"). Their `kind` and
`confidence` mean the same thing PDF's do: `confidence` is `"structural"` for a block docling
mapped with confidence, or `"docling:<label>"` naming the item type when it met something the
mapping does not have a rule for, so an unfamiliar block type is visible rather than silently
flattened into ordinary prose.

When an answer needs verification, cite both places: the markdown file and `dgx:block=...`
for the extracted text, plus the sidecar's `source_file` and, for a PDF, `page` for the
original document. The `content_sha256` in frontmatter and sidecar must match; if it does
not, the markdown no longer proves which source bytes it came from.

Five markers can appear in the body.

### `> **INCOMPLETE — ...**`

Content that is not in the file, named:

```markdown
> **INCOMPLETE — pages 1, 2, 3 are mostly image (up to 37% of the page), and that
content is not in the text layer.** No OCR was attempted.
```

Without it a `partial` document drops its scanned pages silently, and a document missing its
pages looks the same as one that never covered the topic.

A page that is one-third diagram is not broken. The marker states the measurement, names the
threshold (`IMAGE_PAGE_COVERAGE`), and leaves the decision to you.

### `> **Annotation:**`

A Markup callout or sticky note: the box someone types into when annotating a form in Preview
or Acrobat. These are attached to the page rather than printed on it, so ordinary text
extraction does not see them. On an annotated form they are often the only instructions in
the document, and without them a filled-in example reads as a blank form.

The prefix is needed later, when this text is retrieved and cited by something that no
longer has the PDF. What the form prints and what a colleague wrote onto it are different
claims, and a citation has to keep them apart.

Where a note can be tied to something, the marker says how it was found:

```markdown
> **Annotation** [points to: TypeOfSubmission]: Use Application for the first submission attempt.

> **Annotation** [beside: 2. DATE SUBMITTED]: Format: MM/DD/YYYY.
```

| Kind | Means |
| --- | --- |
| `[points to: X]` | The annotator's arrow lands on X. Strong evidence. |
| `[beside: X]` | X only shares a row with the note. Weaker, so treat it with more care. |
| no marker | Nothing could be tied to it. A plausible guess would be worse than none, because nothing later in the pipeline can tell a guess from a measured link. |

Neither kind claims to know which form field a note is about. A PDF's ruling is a layout
grid, not a map of the form's fields. On an annotated form, a note about a checkbox in
field 1 can have its arrow tip sitting inside a cell that names a different field. Where the
tip landed is a fact. Which field the note is about is a reading of the form, and you have
the form in front of you while the converter has coordinates.

### `> **Boxed text:**`

Text the page draws inside a box: a note flattened into the document, or authored that way.
It is ordinary page text and has none of an annotation's provenance, so it gets its own
label. All the marker says is that the page sets this text apart in a box.

Marking them also keeps them readable. Boxes standing side by side share a baseline, and
without this their words interleave into one unreadable line.

### `> **Image:**`

```markdown
> **Image:** embedded picture, not extracted. No OCR was attempted.
```

A DOCX's own way of naming what a PDF's [INCOMPLETE](#-incomplete--) note names for a
scanned page: a picture embedded in the document, marked in place rather than silently
dropped. When a DOCX embeds one or more of these, the body also opens with an INCOMPLETE
note giving the count, and `corpus.yaml` records it under `conversion.images` so `pipeline
report` can list it.

### Tables

Ruled tables come from their ruling lines. Borderless ones are recovered from column
alignment and flagged in [LAYOUT NOTES](#layout-notes) for a spot-check.

A cell counts as ruled only where a line bounds it on every side, which is less often than
it looks. A table banded with shading typically draws a box around its shaded rows and
nothing around the rest, so on every other row the first and last cells have no outer rule
and their text is dropped. The result is a table of numbers with no labels, which looks
plausible. Those cells are recovered by measuring the column from the rows that are ruled
and taking the words the page draws inside it. The recovery refuses three cases rather than
guess: a column the ruled rows do not agree on (a merged cell), a grid where any column
cannot be measured that way at all (which is what a bar chart's axis labels look like), and
a cell overlapping another table or a taller row that already renders the same words. A cell
that is ruled and empty is never filled, because a blank box on a form is part of the form.

A table's header row is often drawn *outside* its grid: the page rules the data and sets the
column names a line above it. Those names reach the converter as ordinary page text, and
being short and set larger than body text they become a heading. The caption above them ends
up with an empty body, and the figures end up filed under `age number percent`. The words
someone would search for are then in one section and the answer in the next, and neither
answers on its own. Several tables headed this way would all produce sections with the same
heading, such as `rank number percent`, which also makes the citation meaningless. A line
directly above a ruled table is taken as that table's header row when each of its cells
falls wholly inside exactly one of the columns the rows were measured in, no two cells share
a column, and they arrive in the grid's order. It is then absorbed into the table rather
than dropped, so the table is headed by the column names the page draws instead of by its
own first data row, and the caption above keeps the table it introduces. The rule rejects
four cases: a grid with any unmeasurable column, a line more than one line's leading above
the grid, a line that fits two grids at once, and any line the document repeats in its
margins. `2.1  Travel Rates` also sits over a grid and also splits into two cells, but its
second cell straddles a column rule rather than sitting in a column, so it stays a heading.
Numbered headings are the most common heading style in the documents this converter is built
for.

A table that meets neither test (for example a slide's table, drawn with type, whitespace
and a rule above and below) is left as text. The risk is that it becomes headings instead:
its cells are set large and bold, so each one passes every test a heading has, and a run of
them cuts the page into a section per cell: the figure ends up in one section and the year
it belongs to in the next, and no section states the answer. So a line is not a heading if
it splits into columns, or if another line stands beside it in the same band of the page. A
heading meets neither condition, since it is one run of text with its content below it. If
the rule does catch a genuine heading, only the `#` is lost; the text stays.

## When something looks wrong

Put the converted markdown next to the original and look. Then:

**Reading order scrambled on a two-column page.** Check [LAYOUT NOTES](#layout-notes) to see
whether columns were detected at all. `PDF_COLUMN_GAP_FRACTION` sets how wide a gutter has
to be to count. Where a gutter is found, words are grouped into lines within each column and
the columns are emitted left first: a line never crosses a gutter, so two columns whose
lines happen to share a baseline cannot be welded into one.

**A table came out as prose, or prose came out as a table.** `PDF_MIN_TABLE_ROWS` is how many
aligned lines make a table; `PDF_COLUMN_ALIGN_TOLERANCE` is how much horizontal drift is
allowed between rows.

**Too many headings, or too few.** `PDF_HEADING_SIZE_RATIO`: how much larger than body text a
line has to be set to count as a heading. Size is not the only test: a line that splits into
columns, or that has another line standing beside it, is a table cell and is never a
heading, whatever size it is set in.

**Running headers and footers left in.** `PDF_REPEAT_PAGE_FRACTION` and `PDF_MARGIN_FRACTION`
control how repeated margin text is found.

Every setting is listed with its default and a one-line explanation in `.env.example`. They
describe page geometry, not what the document means.

To check that converted text can still answer the questions people will ask, see [Checking
the output can still answer
questions](evaluation.md#checking-the-output-can-still-answer-questions).

### Escalating a stubborn document

`PDF_ENGINE=docling` switches to a layout model. It gives better borderless-table structure
and costs a large ML runtime and a model download. Decide it per document, by looking at
converted output, rather than turning it on across the board.

It is not wired up yet, and says so when you try, naming what it needs.

## Things that stop and ask

Conversion stops on these rather than write something silently wrong. Each one names the file
and the reason.

- A CSV past `CSV_MAX_ROWS` (300) or `CSV_MAX_COLS` (15). How a large table should be split
  for retrieval is a decision to make, not a default to pick.
- A source file that changed since it was recorded. Each entry stores a checksum, and a
  mismatch is a loud error rather than a silent re-convert.
- An encrypted or password-protected PDF.
- A file listed in `corpus.yaml` but no longer in `SOURCE_DIR` is reported `MISSING` and the
  run carries on. The entry is never deleted and the run never fails because of it. To
  remove such entries and the `kb/` files they produced, run `make inventory` so the marks
  are current, then `make prune` to see what would go and `make prune ARGS=--yes` to do
  it. `kb index` drops the matching database rows on its next run
  ([Removing documents](administration.md#removing-documents)).

## What this does not do

No OCR, no LLM calls. Chunking, embeddings, a vector store, retrieval and serving are
[`kb`](search-and-mcp.md). They are not part of conversion, and this command does not run them.
