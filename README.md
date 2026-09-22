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

Every converted document -- PDF, DOCX, DOC and CSV alike -- has a sidecar next to it, and
every content block has a stable HTML comment anchor immediately before it:

```markdown
<!-- dgx:block=travel-handbook:p012:b004 -->
| Expense category | Limit | Receipt required |
```

The sidecar lives next to the markdown as `<slug>.provenance.json`. It maps each block id to
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
`bbox` at all -- the block's position in the file is the only locator, and its id still names
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

### `> **Image:**`

```markdown
> **Image:** embedded picture, not extracted. No OCR was attempted.
```

A DOCX's own way of naming what a PDF's INCOMPLETE note names for a scanned page: a picture
embedded in the document, marked in place rather than silently dropped. When a DOCX embeds
one or more of these, the body also opens with an INCOMPLETE note giving the count, and
`corpus.yaml` records it under `conversion.images` so `pipeline report` can list it.

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
  run carries on. The entry is never deleted and the run never fails because of it. To
  remove such entries and the `kb/` files they produced, run `make inventory` so the marks
  are current, then `make prune` to see what would go and `make prune ARGS=--yes` to do
  it. Stage 2's `kb index` drops the matching database rows on its next run.

---

## What this does not do

No OCR, no LLM calls. Chunking, embeddings, a vector store, retrieval and serving are Stage
2 (below) — not part of conversion, and not something this command runs.

---

## Stage 2: search and serve

Once `kb/` exists, a second, separate command line — `kb` — makes it searchable and hands it
to an AI assistant (Codex, ChatGPT desktop, Claude Code, Claude desktop) over MCP. It lives
in the same repo, behind its own install step, and does not change anything above.

**Status:** indexing, search, the eval harness, and the MCP server over both **stdio** (the
developer path) and **streamable HTTP** (the shared server on the LAN, behind Caddy with a
bearer token) work. Document summaries (`kb catalog --summarize`) are the one piece not yet
built (S5, optional).

**Quickstart — clone to a working `search` in Codex, over the shared HTTPS server:**

```bash
cp .env.example .env               # then set KB_PATH (absolute), KB_TOKENS, KB_PUBLIC_HOST, KB_URL_BASE
make compose-up                    # builds and starts db, ollama, kb-mcp, kb-static, caddy
make compose-index                 # walks kb/, embeds it, loads it into Postgres
```

`KB_PATH` must be an absolute path here: the compose stack bind-mounts it, and a relative
path would be resolved against the `compose/` directory rather than this one. `make
compose-up` checks and refuses otherwise.

**GPUs.** Docker never hands a GPU to a container unless asked, so `make compose-up` asks
on your behalf. Before starting anything it checks whether this host has NVIDIA GPUs and
whether Docker can pass them through, and if both hold it reserves all of them for
`ollama`; otherwise it starts on the CPU. Either way it prints which it chose. Run the
check on its own with:

```bash
make gpu-check
```

Set `KB_GPU` in `.env` to override the choice. `off` keeps embedding on the CPU. `on`
makes the GPUs a requirement, so a deployment that is meant to have them refuses to start
instead of quietly running many times slower. The default, `auto`, is the detection just
described. Passing GPUs through needs the NVIDIA Container Toolkit installed on the host;
without it the check finds nothing to use and says so.

Indexing is the part this speeds up, and only the first run over a corpus is slow. An
embedding model is small enough to sit on a single GPU, so a second and third card do not
divide that work further; they matter for a larger embedding model and for serving several
requests at once.

Then add the server to your assistant (Codex/Claude Code snippets below) using
`https://<KB_PUBLIC_HOST>/mcp` and one of the tokens from `KB_TOKENS`, and ask it a
question. `make compose-index` needs the embedding model pulled into `ollama` first —
`docker compose -f compose/docker-compose.yml exec ollama ollama pull nomic-embed-text`
— which is a separate, manual, one-time step (nothing in `make compose-up` does it, since
pulling a model is exactly the kind of thing that should not happen silently).

For local development against a host venv instead of the container stack:

```bash
uv sync --extra serve                      # installs kb's dependencies; plain `uv sync` does not
cp .env.example .env                       # then fill in the Stage 2 keys (see below)
docker compose -f compose/docker-compose.yml --profile dev up -d db ollama
uv run kb index --init                     # applies the database schema
uv run kb index                            # walks kb/, embeds it, loads it into Postgres
uv run kb serve --transport stdio          # runs the MCP server over stdio
```

`kb --help` lists every subcommand (`index`, `search`, `serve`, `eval`, `catalog`); `catalog`
currently exits with "not implemented until S5".

### `kb index`

Walks every `kb/*.md` file, embeds it with the local `ollama` model named by `EMBED_MODEL`
(needs `ollama pull nomic-embed-text` and `ollama serve` reachable at `OLLAMA_BASE_URL`),
and loads it into Postgres. It is idempotent and safe to re-run: a document whose markdown
file has not changed since the last run is left alone, so running it again after adding one
new document only embeds that one document.

It prints one summary line:

```
3 unchanged, 1 reindexed, 0 deleted, 0 errors
```

`unchanged` — files whose bytes match the last indexed copy, skipped. `reindexed` — files
that were new or had changed, parsed and reloaded. `deleted` — documents that were indexed
before but whose file is now gone, removed from the database. `errors` — files that failed
to parse (bad frontmatter, a sidecar that does not match, a missing block); the file is
reported and skipped, and whatever was indexed for it before is left in place rather than
being silently dropped. A run with any errors exits non-zero.

Two flags change what counts as "changed":

- `--force` reindexes every document regardless of whether its file changed — useful after
  editing `retrieval/chunk.py`'s constants or anything else that changes how a document is
  cut, without touching the source files themselves.
- `--reindex-all` additionally **empties** the index first (documents, blocks, sections,
  chunks — not the roles or the schema) and updates the recorded embedding model. Use it
  after changing `EMBED_MODEL` or `EMBED_DIM` in `.env`: mixing vectors from two different
  models in the same table would make search meaningless, so `kb index` refuses to run
  and names both the old and new model until you pass this flag.

### How a document gets cut up

A document is never searched as one blob, and never chunked without regard for its
structure. Two cuts happen, in order:

1. **Sections.** Every heading (`#`, `##`, or `###`) starts a new section that runs until
   the next heading of the same or a shallower level; a `####` heading or deeper does not
   start a new section, it just stays inside the one it's in. A section is what an
   assistant reads: it is never split across a search result.
2. **Chunks.** Inside a section, blocks (paragraphs, lists, tables, annotations) are
   grouped into runs of about 1,200 characters — a chunk is what search actually matches
   against, never shown as an answer on its own. A table shares a chunk with its section's
   heading when it is the first thing under it, otherwise it gets a chunk to itself, and
   is never split unless it is over 2,500 characters. A larger table is cut only between
   rows: each piece repeats the table's header row and the last row of the piece before
   it, so every piece reads as a table on its own, and a single row is never cut however
   long it is. A very long paragraph or list (over 2,500 characters) is split at its
   blank lines rather than mid-sentence. Notes such
   as `> **Annotation**` or `> **Boxed text:**` always stay attached to whatever came right
   before them.

### What the Stage 2 keys in `.env` mean

`.env.example` documents every key `kb` reads, each with a one-line comment. The two that
matter to get right: `DATABASE_URL_INDEX` (the writer role `kb index` uses) and
`DATABASE_URL` (the read-only role `kb serve`/`kb search` use). `docker compose --profile
dev up -d db` creates a local Postgres with both roles already set up, matching the defaults
in `.env.example`.

### `kb search`

```bash
uv run kb search "per diem rates" --k 8 [--slug handbook] [--text-class clean] [--leg fused]
```

Runs the same retrieval the (future) MCP server uses and prints one line per hit, followed
by an indented citation line:

```
0.0164  sec:handbook:5  pages 12–13  Employee Handbook › Handbook › Per Diem Rates  |  Uxbane...
    Employee Handbook, Employee Handbook › Handbook › Per Diem Rates (source: handbook.pdf, pages 12–13, dated UNCONFIRMED; blocks handbook:p012:b001…handbook:p013:b003)
```

Every search runs two independent legs over the indexed chunks and merges them:

- **Lexical** (Postgres full-text search) catches exact tokens -- form numbers, codes,
  exact phrases -- that an embedding model tends to blur together with similar-looking
  text.
- **Vector** (cosine similarity over `nomic-embed-text` embeddings) catches paraphrase --
  the right passage even when the question uses none of the document's own words.

Neither leg alone is reliable enough on its own, so results are merged with reciprocal
rank fusion (RRF): each leg contributes independently, and a hit that both legs agree on
outranks a hit either leg alone thought was best. `--leg lexical` or `--leg vector` runs
one leg in isolation, for debugging. A query made only of stop words (`"the of and"`) has
no lexical leg to run (`websearch_to_tsquery` parses it to nothing) and falls back to
vector-only results rather than erroring.

Every hit is a **section**, never a chunk: chunks are what search matches against
internally, but what comes back is always a whole readable section with its page range,
heading path, and a citation you can quote and go check against the original markdown.

### `kb eval`

```bash
uv run kb eval [--cases eval/retrieval.yaml] [--k 5]
```

Runs a fixed set of questions with known answers (`eval/retrieval.yaml`) against the index
and prints a table:

```
case id                         lexical   vector    fused
form-token-exact                      1        5        1
...
lexical hit@5: 100%
vector hit@5: 50%
fused hit@5: 100%
```

Each row is one case; the number is the rank (1 = top result) of the first section that
leg returned matching the case's expected document (and, if the case specifies them, an
expected phrase or page) within the top `--k`. A `-` means that leg never found it. The
three percentages at the bottom are hit@k across all cases, one per leg.

The committed cases run only against the synthetic fixtures in
`tests/retrieval/fixtures/kb/` and are checked in the test suite (fused hit@5 must be
100% there). They say nothing about retrieval quality on a real corpus.

**Recorded floor, proxy corpus (2026-09-18).** Twelve public documents of the shapes the
client corpus is expected to contain (reports, slide decks, an annotated form set, two
acquisition regulations as Word files, one CSV table), indexed with `nomic-embed-text`,
21 questions worded the way a user would ask them, each answerable from one document and
checked by an expected phrase and, where the source has pages, an expected page. The
case file lives outside the repository because its questions describe the documents.

| leg | hit@5 |
|---|---|
| lexical | 33% |
| vector | 67% |
| fused | 76% |

How to read it: the lexical leg ANDs every non-stop word of the question, so a natural
question containing one word the right chunk lacks scores zero there; it exists for exact
tokens (a section number, a form number, a phone number), and it placed every such case
first. Of the five fused misses, two are slide pages whose large-type fragments each
convert to a one-line heading and therefore a one-line section; one is a PDF whose word
spacing was lost in conversion; one is a page holding only bare labels and numbers; one is
a table the vector leg does not surface. Each is a conversion shape, not a ranking
parameter. These numbers are a regression floor: a change to chunking, embedding model,
or fusion is measured against them and must not lower them. They are not tuned toward.

No number for the client's own corpus appears here; the client runs the same command
against their index and records their own.

### `kb serve` — the MCP server

```bash
uv run kb serve --transport stdio    # the default; developer path, nothing on the network
uv run kb serve --transport http     # the shared server; needs KB_TOKENS (or --allow-anonymous, dev only)
```

Runs a read-only [MCP](https://modelcontextprotocol.io) server against the index, over
either:

- **stdio** — the assistant starts `kb serve` itself as a subprocess and talks to it over
  stdin/stdout, so there is nothing to bind or expose on the network. This is what
  `uv run kb serve --transport stdio` and the compose `dev` profile are for.
- **streamable HTTP** — one server, run once (by `kb-mcp` in the compose stack), that every
  user's assistant talks to over `https://<KB_PUBLIC_HOST>/mcp`. `kb-mcp` itself binds
  `0.0.0.0:8765` inside the compose network only; `caddy` is what actually terminates TLS
  and is reachable from the LAN, on 443. Every request under `/mcp*` needs `Authorization:
  Bearer <token>` where `<token>` is one of the comma-separated values in `KB_TOKENS`; a
  missing or wrong token gets a 401 with no body. `/health` (`GET /health`, returning
  `{"ok": true, "documents": <count>, "embed_model": ...}`) needs no token, for monitoring.
  Starting `--transport http` with `KB_TOKENS` empty refuses to run (exit 2) unless you pass
  `--allow-anonymous`, which is for local experimentation only and logs a loud warning —
  never pass it on a network anyone else can reach.

It exposes five tools, each read-only (`readOnlyHint: true`) and documented to the
assistant in its own instructions:

- **`search(query, k=8, slug=None, text_class=None)`** — find candidate sections for a
  question; returns ids, titles, snippets and citation urls, never full text.
- **`fetch(id)`** — read the whole section, page, or document named by an id from
  `search`, `list_documents`, or `get_outline`.
- **`list_documents(text_class=None, title_contains=None)`** — every indexed document,
  sorted by title, with its size and section count.
- **`get_outline(id)`** — the section-by-section table of contents for one document.
- **`get_section(id, neighbours=0)`** — like `fetch` on a section or page, but also pulls
  in `neighbours` sections before and after it, concatenated in reading order.

#### Use it from Codex

```toml
# ~/.codex/config.toml — developer, local stdio
[mcp_servers.kb]
command = "uv"
args = ["run", "--directory", "/path/to/dgx-infra", "kb", "serve", "--transport", "stdio"]
startup_timeout_sec = 30
tool_timeout_sec = 60

# end user, the shared server on the LAN
[mcp_servers.kb]
url = "https://kb.internal.example/mcp"
bearer_token_env_var = "KB_TOKEN"
tool_timeout_sec = 60
```

Equivalent CLI: `codex mcp add kb -- uv run --directory /path/to/dgx-infra kb serve --transport stdio`.
For the HTTP form, `export KB_TOKEN=<your token>` first and use `kb.internal.example`
replaced with your real `KB_PUBLIC_HOST`.

#### Use it from Claude Code

```bash
claude mcp add --transport stdio kb -- uv run --directory /path/to/dgx-infra kb serve --transport stdio
claude mcp add --transport http kb https://kb.internal.example/mcp --header "Authorization: Bearer $KB_TOKEN"
```

Both forms work today. Use `--transport stdio` for local development against a host venv;
use `--transport http` (with `KB_TOKEN` exported and `kb.internal.example` replaced with
your `KB_PUBLIC_HOST`) once `make compose-up` is running.

#### What you should see

Once added, ask the assistant something the fixtures or your real corpus can answer. It
should call `search`, get back a short list of section ids with snippets, call `fetch` (or
`get_section`) on the most promising one or two, and answer using that section's text —
citing the `citation` string it got back, not the question itself. If you watch `kb serve`'s
own stderr (redirected by your assistant's MCP client, not printed to your terminal
directly) you will see one JSON line per tool call: the tool name, its arguments, the
result ids, how long it took, and any error.

**A snippet is not evidence.** `search` returns a 300-character preview of the single best
match — enough to judge relevance, not enough to answer from, and never enough to tell
whether a table came out flattened or a note got separated from what it is about. Treat it
as a pointer, not an answer: the assistant should always `fetch` (or `get_section` with
`neighbours=1` when a section looks cut off) before quoting anything back to you.

### Checking a citation against the original — the static `kb/` server

Every result's `url` (and the `citation` string in `fetch`'s metadata) points at
`https://<KB_PUBLIC_HOST>/kb/<file>.md#dgx:block=<id>` — the same converted markdown file
`kb search`/`kb serve` indexed, served read-only by `kb-static` behind Caddy. Opening it in
a browser is the way to check what the assistant told you against the actual converted
text (the `#dgx:block=...` fragment does nothing in a browser today — it exists so a future
viewer can jump straight to the block, and so the URL is unique per citation).

**`/kb/*` needs the same bearer token `/mcp*` does**, and a bare browser has no way to add
an `Authorization` header to a request. Three practical options: a browser extension that
adds a fixed header to requests for your `KB_PUBLIC_HOST` origin (e.g. "ModHeader" or
similar); `curl -H "Authorization: Bearer $KB_TOKEN" https://<host>/kb/<file>.md -o
file.md` and open the saved file locally; or, if your organisation's Caddy is set up with a
client TLS certificate instead of `tls internal` (see "TLS" below), some browsers can be
configured to present it automatically and you drop the header requirement for that one
origin — that is a Caddy/client-cert configuration choice, not something this repo sets up
for you.

### Rotating a token

`KB_TOKENS` is a comma-separated list — add a new token, keep the old one for as long as
you want both to work, then remove the old one. Whether it's one token per user, one per
team, or one shared token for everyone is a decision `stage2-retrieval-brief.md` §11.2
leaves to Tim; `KB_TOKENS` supports all three shapes equally. After editing `.env`:

```bash
docker compose -f compose/docker-compose.yml --profile prod up -d --build caddy kb-mcp
```

restarts both `caddy` (which regenerates its token matcher from the new `KB_TOKENS` on
start — see `compose/caddy-entrypoint.sh`) and `kb-mcp` (whose bearer middleware reads
`KB_TOKENS` at process start too) without touching the database or `ollama`.

### The "flattened table" caveat

Some source tables (merged cells, unusual borders, tables inside scanned images) don't
survive conversion as a clean grid — Stage 1's converter says so explicitly, either with a
`Not converted` note or by falling back to a plainer rendering (see "Reading the converted
markdown" above, and the per-document `LAYOUT NOTES`/`EVIDENCE NOTES` in `pipeline
report`). If an assistant's answer depends on a specific cell, check the citation against
the original before trusting it — this is exactly what the static `kb/` server above is
for. Retrieval does not repair a bad conversion; it only finds and returns what Stage 1
already wrote down.

### TLS

`compose/Caddyfile` defaults to `tls internal`: Caddy generates its own local CA and a leaf
certificate for `KB_PUBLIC_HOST` the first time it starts, and terminates HTTPS with it.
Nothing else has to be configured for the stack to serve valid-looking HTTPS — but every
user's machine has to be told to trust that CA once, or their assistant/browser will reject
the connection as self-signed. Fetch the CA certificate from the running container and
install it as a trusted root using your OS's normal process for that:

```bash
docker compose -f compose/docker-compose.yml cp caddy:/data/caddy/pki/authorities/local/root.crt ./dgx-kb-ca.crt
```

Alternatively, set `TLS_CERT` and `TLS_KEY` in `.env` to the paths of a certificate/key
pair issued by a CA your users' machines already trust (an internal corporate CA, or a
client-issued cert) — `compose/caddy-entrypoint.sh` uses that pair instead of `tls
internal` whenever both are set, and no user-side trust step is needed. Which of the two
is right for a given deployment is `stage2-retrieval-brief.md` §11.1, open for Tim to
decide; both are supported without any code change.

### What is deliberately not built

A web UI (users bring their own assistant), email intake, answer generation or reranking,
OAuth or any public/internet-facing endpoint, multi-tenancy or per-user document
permissions, and document summaries (`kb catalog --summarize`, S5, optional and gated on
`models.yaml`) — see `stage2-retrieval-brief.md` §10 for the full list and why.

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
