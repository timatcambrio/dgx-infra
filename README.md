# dgx-infra — document conversion

Point this at a folder of institutional documents (PDF, DOCX, legacy DOC, CSV) and it
produces clean markdown with metadata, plus a report telling you which documents converted
completely and which did not.

**Nothing here uses a model.** No OCR, no LLM, no ML runtime, and nothing is downloaded at
run time. PDFs are reconstructed from character geometry — word positions, type sizes,
ruling lines. That means conversion works offline, produces identical output every time, and
you can read the extraction logic rather than trusting weights.

It also means some things cannot be recovered — a page that is a scan has no text to
extract. **The pipeline's job is to be honest about that**, so the sections below on reading
the report and the markers in the output are the important ones.

---

## Setup

You need [uv](https://docs.astral.sh/uv/). It installs the right Python (3.12) and every
dependency itself — there is nothing else to install and no virtualenv to create by hand.

```bash
uv sync
cp .env.example .env
```

Then open `.env` and set one line:

```bash
SOURCE_DIR=/absolute/path/to/your/documents
```

That is the only required setting. Everything else in `.env.example` is commented out with
its default shown.

**Legacy `.doc` / `.dot` files also need LibreOffice** — see [LibreOffice (subprocess
only)](#libreoffice-subprocess-only). Every other format works with `uv sync` alone.

### Where things go

| | |
| --- | --- |
| **Your documents** | `SOURCE_DIR` — **read-only**. Nothing is ever written, moved, renamed or deleted here. |
| **Converted markdown** | `kb/` in the companion `dgx-knowledge` repo (clone it beside this one) |
| **The record of what came from where** | `corpus.yaml`, also in `dgx-knowledge` |
| **Scratch files** | `work/` — disposable; deleting it costs time, never information |

Source documents deliberately live outside both repos. If `SOURCE_DIR` is unset, commands
stop with an error rather than guessing.

---

## Running it

Four commands, in order:

```bash
make inventory    # find the documents and record them in corpus.yaml
make triage       # measure how much readable text each PDF actually has
make convert      # write the markdown into kb/
make report       # print what happened
```

Each is re-runnable. `convert` skips documents that have not changed since last time; add
`--force` to redo them anyway. To work on a single document, every command takes `--only
SUBSTRING`, and `--source-dir PATH` overrides `.env` for one run.

`make profile` is a useful extra: it prints a one-row-per-document summary straight from the
PDFs, with no manifest and no conversion run, so you can see what a folder contains before
committing to anything. It emits counts only — never document text — so it is safe to run on
a corpus that cannot leave the machine it lives on, and to paste the result into a ticket.

---

## Reading the report

### The class on each document

`triage` measures three things per PDF and combines them into a class:

| Class | Means |
| --- | --- |
| `clean` | Every page carries usable text. Convert and move on. |
| `partial` | Usable overall, but a meaningful minority of pages yielded almost nothing — typically a handbook with scanned appendices. **Those pages will be missing from the markdown.** |
| `needs_ocr` | Not enough readable text to convert. Needs OCR, which this pipeline does not do. |
| `error` | The file could not be opened. Encrypted files report themselves specifically. |

DOCX and CSV skip triage — their text is structural, not drawn on a page — and are recorded
as `clean`.

The three measurements behind the class:

| Setting | Default | What it catches |
| --- | --- | --- |
| `MIN_CHARS_PER_PAGE` | 100 | A page with less than this counts as "low". Median is used, not mean, so a few dense pages cannot mask a scanned majority. |
| `MIN_ALPHA_RATIO` | 0.60 | A PDF with a broken font map extracts plenty of characters and every one is mojibake. Such a file looks text-rich and is unusable. Character count alone never catches it. |
| `MAX_LOW_PAGE_FRACTION` | 0.20 | Above this share of low pages, a document is no longer `clean`. |

**Leave these alone.** They are set where they are on purpose; moving them to make a
particular folder look better changes the labels without changing the documents.

### LOW-TEXT PAGES

Page numbers, inside otherwise-usable documents, that yielded almost no text. Worth a
glance: a cover page or a section divider is fine, a scanned figure is content that will be
missing from `kb/`.

### LAYOUT NOTES

Where the geometry is most likely to have struggled. Two kinds:

- **Multi-column pages** — handled, but the likeliest place for reading order to go wrong.
  Worth checking the converted output.
- **Borderless tables** — tables with no ruling lines, recovered by noticing that several
  consecutive lines split into aligned columns. Worth a spot-check. The pipeline is
  deliberately reluctant here, because inventing a table destroys the paragraph it consumes,
  so it would rather miss one than invent one.

### EVIDENCE NOTES

**This is the section that catches silent failure**, and it is the reason to read the report
at all rather than glancing at the classes.

It reports what a document carried *beyond* its text layer, set against what was recovered:

```
EVIDENCE NOTES (what the document carries beyond its text layer)
  <a 25-page annotated form>: 205 annotation(s) carrying text no text-layer
    extraction sees, 71 stating their own target
  <a 3-page budget form>: 3 page(s) that are mostly image, up to 36.8% of the
    page -- that content is not in the text layer and no table extraction reaches it
```

The second line is the case worth understanding. A form supplied as a **screenshot** with
typed notes beside it defeats every coverage measurement at once, and none of them is wrong:

| Measurement | Says | Because |
| --- | --- | --- |
| chars/page | healthy | the typed notes are real text |
| alpha ratio | perfect | that text is clean |
| low-text pages | none | every page clears the threshold |
| ruled tables | none found | a picture has no vector lines to find |

Such a document classifies `clean`, converts without complaint, and **omits the entire form
it is about**. Measuring how much of each page is raster image is the only thing that
separates it from a document that is genuinely fine.

---

## Reading the converted markdown

Every file opens with frontmatter recording where it came from, what converted it, and its
text coverage — including `needs_ocr: true` where applicable, so a downstream consumer can
tell a complete document from an incomplete one without re-deriving it.

Then there are four markers you will see in the body.

### `> **INCOMPLETE — ...**`

Content that is not in the file, named explicitly:

```markdown
> **INCOMPLETE — pages 1, 2, 3 are mostly image (up to 37% of the page), and that
content is not in the text layer.** No OCR was attempted.
```

A `partial` document would otherwise drop its scanned pages silently, which downstream is
indistinguishable from a document that never covered the topic.

This is reporting, not a verdict. A page that is one-third diagram is not broken — the
pipeline states the measurement, names the threshold (`IMAGE_PAGE_COVERAGE`), and leaves the
judgement to you.

### `> **Annotation:**`

A "Markup" callout or sticky note — the box someone types into when annotating a form in
Preview or Acrobat. These are **attached to the page rather than printed on it**, so no
ordinary text extraction sees them. On an annotated form they are frequently the only
instructions the document carries; dropping them turns a tutorial back into a blank form.

The prefix matters downstream: this text gets retrieved and cited with no access to the
original PDF, and "what the form prints" versus "what a colleague annotated onto it" is
exactly the distinction a citation has to keep.

Where a note can be tied to something, the marker says **how it was found**:

```markdown
> **Annotation** [points to: TypeOfSubmission]: Use Application for the first submission attempt.

> **Annotation** [beside: 2. DATE SUBMITTED]: Format: MM/DD/YYYY.
```

| Kind | Means |
| --- | --- |
| `[points to: X]` | The annotator's own arrow lands on X. Strong evidence. |
| `[beside: X]` | X merely shares a row with the note. Weaker — treat with more caution. |
| no marker | Nothing could be tied to it. Deliberate: a plausible guess is worse than none, because nothing downstream can tell a plausible guess from a real one. |

**Neither kind claims to know which form field a note is about.** A PDF's ruling is a layout
grid, not a map of the form's fields — on a real annotated form, a note about a checkbox in
field 1 can have its arrow tip genuinely inside a cell naming a different field. Where the
tip landed is a fact; which field the note concerns is a reading of the form, and you have
the form in front of you while the converter has coordinates.

### `> **Boxed text:**`

Text the page draws inside a box — a note that was flattened into the document, or authored
that way. It is ordinary page text, so it carries none of an annotation's provenance, which
is why it is labelled differently. All the marker claims is that the page sets this text
apart in a box.

Marking these also keeps them readable: boxes standing side by side share a baseline, and
without special handling their words interleave into a single unreadable line.

### Tables

Ruled tables come from their ruling lines. Borderless ones are recovered from column
alignment and flagged in LAYOUT NOTES as worth a spot-check.

---

## When something looks wrong

Start by looking at the converted markdown next to the original. Then:

**Reading order scrambled on a two-column page.** Check LAYOUT NOTES to confirm columns were
detected. `PDF_COLUMN_GAP_FRACTION` controls how wide a gutter must be to count.

**A table came out as prose, or prose came out as a table.** `PDF_MIN_TABLE_ROWS` (how many
aligned lines make a table) and `PDF_COLUMN_ALIGN_TOLERANCE` (how much horizontal drift is
allowed) are the relevant knobs.

**Too many headings, or too few.** `PDF_HEADING_SIZE_RATIO` — how much larger than body text
a line must be set to count as a heading.

**Running headers and footers left in.** `PDF_REPEAT_PAGE_FRACTION` and
`PDF_MARGIN_FRACTION` control how repeated margin text is detected.

Every knob is listed with its default and a one-line explanation in `.env.example`. They
describe page geometry, not document meaning.

### Checking the output can still answer questions

Comparing output byte-for-byte catches *change*. It cannot catch output that is stable and
useless — a budget grid flattened into prose, a note stranded from the field it describes.
Both would pass a file comparison indefinitely.

So you can write questions with known answers and assert that the text needed to answer them
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

This reads `kb/` only — it needs neither the source documents nor `SOURCE_DIR` — and exits
non-zero on failure, so it can gate a release. There is **no model involved**: cases are
literal substring checks, which makes them offline, deterministic, free, and reviewable by
someone who wants to disagree with a case.

Write `expect` as the smallest string that makes the answer findable.

### Escalating a stubborn document

`PDF_ENGINE=docling` switches to a layout model, which buys better borderless-table
structure at the cost of a large ML runtime and a model download. It is meant to be a
per-document decision made by looking at converted output — not a blanket setting.

It is **not currently wired up** and will tell you so, naming what it needs.

---

## Things that stop and ask

Conversion halts on these rather than emitting something quietly wrong. Each names the file
and the reason.

- **A CSV beyond `CSV_MAX_ROWS` (300) or `CSV_MAX_COLS` (12).** How a large table should be
  shaped for retrieval is a design decision, not a default.
- **A source file that changed since it was recorded.** Each entry stores a checksum; a
  mismatch is a loud error, never a silent re-convert.
- **An encrypted or password-protected PDF.**
- **A file listed in `corpus.yaml` but absent from `SOURCE_DIR`** is reported `MISSING` and
  the run continues. The entry is never deleted and the run never fails because of it.

---

## What this does not do

No OCR, no LLM calls, no chunking, no embeddings, no vector store, no retrieval, no serving.
A document that needs OCR is *identified* here and handled elsewhere.

---

## Development

```bash
make check    # the test suite plus both policy gates — what CI runs
make help     # every available target
```

### License policy

The rule is about **how a dependency is called**, not what its licence string says.
Copyleft invoked as a separate program (LibreOffice) is fine; copyleft *imported* as a
library is not. PyMuPDF and pymupdf4llm (AGPL) are import-only and therefore permanently
banned, including for a quick check.

`make license-gate` enforces this by walking installed package metadata and failing if a
GPL/AGPL package is imported anywhere under `pipeline/`. Genuine false positives go in
`scripts/license_allowlist.yaml` with written justification.

### Model policy

Models must be permissively licensed **and** have non-Chinese base-weight provenance, judged
on the base weights rather than on the releasing organisation.

`make model-gate` checks two places, because they fail differently: model caches, and
weights shipped *inside* an installed wheel. The second is not hypothetical — the `docling`
meta-package installs `rapidocr`, whose wheel bundles roughly 30MB of Baidu PaddleOCR
weights as ordinary files that never touch a cache. This project therefore depends on
`docling-slim` with named extras, never on `docling`.

`models.yaml` is the allowlist, and it is deliberately empty: nothing here should ever
download a model, so an empty allowlist plus a cache scan asserts exactly that, and fails
the moment it stops being true. Models that a future conversion path *would* fetch are
recorded under `pending_review` with what is known about each.

### LibreOffice (subprocess only)

Legacy `.doc` / `.dot` conversion shells out to the `soffice` binary:

```bash
soffice --headless --convert-to docx --outdir "$WORK_DIR/doc2docx/" <file>
```

If `soffice` is absent the pipeline fails with an error naming it. There is no fallback to a
copyleft Python library.

> The major version should be pinned and matched between machines: LibreOffice's `.doc`
> import filter is not byte-stable across releases, and byte-stable output is a hard
> requirement here. The pin is not yet decided, so this path is currently unexercised.

### Platform constraint

`pyproject.toml` requires every locked dependency to have an installable wheel on both
**linux-x86_64** and **darwin-x86_64**.

This is not decorative. PyTorch ships no macOS x86_64 wheel after 2.2.2, and without the
constraint `uv lock` produces a lockfile that resolves cleanly and then cannot be installed.
If `uv lock` starts failing, a dependency has dropped one of those platforms — decide
deliberately rather than silently dropping support.

Because the two platforms can resolve different versions, byte-identical output *across*
platforms is not guaranteed. The determinism requirement is per-machine: the same input
converted twice on the same machine must be byte-identical, which `tests/test_determinism.py`
enforces.
