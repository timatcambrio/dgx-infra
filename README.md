# dgx-infra — Stage 1 document conversion

Takes a directory of institutional documents (PDF, DOCX, legacy DOC, CSV) and produces
clean markdown with metadata frontmatter, plus a **coverage report** measuring how much of
the corpus carries a usable text layer.

**This phase is deliberately model-free for text: no OCR, no VLM, no LLM.** The point is to
find out how far plain text-layer extraction gets on the real document mix *before* anyone
commits to an OCR model. The coverage report is a decision gate, not a warm-up.

Companion repo: `dgx-knowledge/` (clone side by side). It holds the manifest `corpus.yaml`,
the converted markdown in `kb/`, and the disposable `work/` directory.

---

## Quickstart — three commands to a coverage report

```bash
conda activate dgx-infra          # python 3.12 + uv only; uv owns everything else
uv sync                           # creates .venv from the locked dependency set
cp .env.example .env              # then edit SOURCE_DIR to point at your documents
```

Then:

```bash
make inventory                    # scan SOURCE_DIR, populate corpus.yaml
make triage                       # measure text-layer coverage per PDF
make report                       # print the coverage table + corpus totals
```

`make check` runs the test suite plus both policy gates and is what CI should run.

---

## Source documents live OUTSIDE both repos

The source documents are **never** inside either repo, not even gitignored. They sit in a
directory of your choosing elsewhere on disk, and the pipeline is pointed at it:

```bash
SOURCE_DIR=/path/to/documents     # in .env; overridable per-run with --source-dir
```

Every command accepts `--source-dir PATH`, falls back to `SOURCE_DIR` from the environment,
and **errors clearly if neither is set**. It never defaults to a path inside the repo and
never creates one.

`SOURCE_DIR` is treated as **strictly read-only**. The pipeline does not write, move,
rename, or clean up anything under it. Conversion intermediates (for example the
LibreOffice `.doc` → `.docx` step) go to `WORK_DIR`, which defaults to `$KB_PATH/work/`,
is gitignored, and is disposable — deleting it costs time, never information.

Consequences that are designed for, not worked around:

- **Manifest paths are relative to the source root**, never absolute. Two machines with the
  same documents at different absolute paths produce the same `corpus.yaml`. The root's own
  path appears nowhere in the committed manifest.
- **A clone alone cannot reproduce a conversion.** That is expected. Each entry's `sha256`
  is the record that a given `kb/` file came from a given source; a mismatch on a later run
  is a loud error, not a silent re-convert.
- **Missing source files are a first-class case.** If a manifest entry's file is absent from
  `SOURCE_DIR`, it is reported as `MISSING` and the run continues. The entry is never
  deleted and the run never fails because of it.

Because `kb/` is the same content as text, it is exactly as sensitive as the sources, so
`kb/` is gitignored in `dgx-knowledge` for now. `corpus.yaml` **is** committed — check that
the filenames themselves are not sensitive before pushing.

---

## Prerequisites

### Python

Python **3.12** via a conda env containing nothing but `python` and `uv`; `uv` manages
every package from there and writes a repo-local `.venv`. `uv.lock` is committed and
pins the entire graph.

```bash
conda create -n dgx-infra python=3.12 uv
```

### LibreOffice (subprocess only — never imported)

Legacy `.doc` / `.dot` files are converted to `.docx` by invoking the `soffice` binary as a
subprocess:

```bash
soffice --headless --convert-to docx --outdir "$WORK_DIR/doc2docx/" <file>
```

LibreOffice is MPL/copyleft. Invoking it as a **separate program** is permitted under the
call-style licence rule below; importing a copyleft library is not. If `soffice` is absent
the pipeline fails with an error naming the binary and this section — it never falls back
to a copyleft Python library.

> **Pinned major version: UNCONFIRMED.** LibreOffice's `.doc` import filter output is not
> byte-stable across releases, and determinism is a hard requirement, so the major version
> must be pinned and matched between dev and the client. It is **not yet installed on the
> dev machine**, so the `.doc`/`.dot` path is currently unexercised. Resolve against the
> client's available version (open question 4) and record the pin here.

---

## Platform constraint (read before upgrading dependencies)

`pyproject.toml` declares `tool.uv.required-environments` for **linux-x86_64** (the DGX and
client target) and **darwin-x86_64** (the current dev machine). Every locked dependency
must have an installable wheel on both.

This is not decorative. PyTorch — pulled in by Docling via `docling-ibm-models` — ships no
macOS x86_64 wheel after **2.2.2**. Without that constraint `uv lock` produces a lock that
resolves cleanly and then cannot be installed on the dev Mac at all. With it, uv resolves
torch per-platform: **2.2.2 on darwin-x86_64, current on linux-x86_64.**

If `uv lock` starts failing, a dependency has dropped one of those platforms. That failure
is the intended signal — decide deliberately, do not silently drop a platform.

**Consequence for golden files:** Docling runs against different torch versions on the two
platforms, so byte-identical Docling output *across* platforms is not guaranteed. The hard
determinism requirement is per-machine: the same input converted twice on the same machine
must be byte-identical, and that is what `test_determinism.py` enforces. A golden-file
mismatch after changing machines is a review item, not something to auto-accept.

---

## Policy gates

Both run under `make check`.

### `scripts/license_gate.py` — call style, not licence string

Obligations attach to *conveying* software, not to using it. The rule is therefore about
**how a dependency is called**, not what its metadata string says:

- Copyleft invoked as a **subprocess** is allowed (separate program, not a derivative work).
  LibreOffice qualifies.
- Copyleft **imported as a library** is banned. The conservative reading of linking makes
  our own module a derivative work, and "the client downloads it themselves" does not cure
  that.
- **PyMuPDF / pymupdf4llm (AGPL) are import-only and therefore permanently banned — for
  anything, including a "quick check".**
- Never vendor a copyleft binary into the repo or into a published image.

The gate walks installed dist-info metadata, classifies each distribution, and FAILS if a
GPL/AGPL package is imported anywhere under `pipeline/`. False positives (dual-licensed or
misdeclared metadata) go in `scripts/license_allowlist.yaml` with written justification.

`test_gates.py` proves the gate fails on a planted `import fitz` — an unexercised gate is
decorative.

### `scripts/model_gate.py` — licence AND provenance

Models must be permissively licensed **and** have non-Chinese base-weight provenance,
judged on the base weights rather than the releasing organisation. A US company
fine-tuning a Chinese base does not clear it.

The gate looks in two places, because they fail differently:

- **Model caches** — anything downloaded at runtime that is not on the allowlist.
- **Installed packages** — model weights shipped *inside* a wheel. This is not hypothetical.
  The `docling` meta-package is defined as `docling-slim[standard]`, which installs
  `rapidocr` whether or not any OCR is used, and the rapidocr wheel bundles roughly 30MB of
  Baidu PaddleOCR weights (`PP-OCRv6_det`, `PP-OCRv6_rec`, `ch_ppocr_mobile`) as ordinary
  files. They never touch a cache, so a cache-only gate reports a clean run while banned
  weights sit in site-packages. This project therefore depends on **`docling-slim` with
  named extras**, never on `docling`.

`models.yaml` is an explicit allowlist, and in this phase it is deliberately **empty**.
Stage 1 is model-free for text, so nothing should ever be fetched; an empty allowlist plus
the gate's cache scan therefore asserts something true and useful right now — that no model
has been downloaded — and fails the moment one appears.

Models Docling *would* fetch once M2 runs are listed under `pending_review` with what is
known about each. The gate prints them on every run and fails if one is found in a cache, so
M2 cannot quietly begin by downloading a model whose licence or provenance is still open.

**Docling's OCR engine is pinned in `config.py` even though OCR is off.** Docling's OCR
backends differ in provenance — RapidOCR wraps PaddleOCR (Baidu) models — so leaving engine
selection on defaults could silently pull Chinese-trained weights into ingestion on a
version bump. The pin is asserted in `test_gates.py`.

---

## Triage thresholds

Defaults live in `config.py` and are overridable by environment variable:

| Setting | Default | Meaning |
| --- | --- | --- |
| `MIN_CHARS_PER_PAGE` | 100 | A page yielding fewer characters counts as "low". |
| `MIN_ALPHA_RATIO` | 0.60 | Fraction of extracted characters that are alphanumeric, punctuation, or whitespace. |
| `MAX_LOW_PAGE_FRACTION` | 0.20 | Above this fraction of low pages, a document is no longer `clean`. |

`alpha_ratio` catches the failure mode a character count misses: a PDF with a broken
font-to-Unicode map extracts plenty of characters and all of them are mojibake. A file can
look text-rich and be unusable.

Classification: `clean` (passes all three), `partial` (passes overall, meaningful minority
of low pages — e.g. a handbook with scanned appendices), `needs_ocr` (fails median chars or
alpha ratio). DOCX and CSV skip triage and are recorded as `clean` with
`text_coverage: null`.

**Do not tune these against a sample set to change the class distribution.** Report the
numbers at the defaults. If a threshold looks wrong, say so here with evidence and leave
the default alone.

---

## Status

| Milestone | State |
| --- | --- |
| **M0** — skeleton, gates, fixtures | Done. `make check` green: both gates pass, 140 tests pass. |
| **M1** — inventory, triage, report | Machinery done and exercised end to end. **The decision gate itself is still open** — see below. |
| **M2** — conversion | **Done for PDF, DOCX and CSV**, with goldens for each. PDF uses a model-free geometric engine; the Docling escalation is still gated. |
| **M3** — quality pass on real samples | Not started. Needs M2 and the real corpus. |

### M1 has not actually run against a representative corpus

The pipeline was exercised against `synthetic-cso-data/`, which is a smoke test, **not the
decision gate**. Those 7 PDFs (15 pages) all classify `clean` at 0.0% `needs_ocr`, with
median 1089–2161 characters per page. That number means only that the tooling works: the
files are uniformly born-digital and are explicitly not representative of the real,
restricted corpus, whose PDFs may well have no text layer at all.

**The M1 deliverable is that same report run over the real documents.** Until then nobody
should conclude anything about how large the OCR problem is.

Test fixtures do carry the hard cases the real corpus might contain, and they classify
correctly: `image_only.pdf` → `needs_ocr`, `mixed.pdf` → `partial`, and `mojibake.pdf` →
`needs_ocr` at 2574 characters per page — text-rich by character count, unusable in fact,
caught only by the alpha-ratio check.

## Open questions and STOP-AND-ASK items

Two of these block M2. None should be resolved by guessing.

1. **The table-structure model is fine — no action needed.** Docling's default
   `PdfPipelineOptions` resolves `table_structure_options` to `TableStructureOptions`
   (kind `docling_tableformer`), which fetches `docling-project/docling-models` —
   `apache-2.0` plus `cdla-permissive-2.0`, IBM's own TableFormer weights rather than a
   fine-tune of anyone's base. The unlicensed `docling-project/TableFormerV2` is reachable
   only by explicitly opting into `TableStructureV2Options`, which nothing here does. It
   stays listed in `models.yaml` precisely so that opting in would trip the gate.

2. **`docling-project/docling-layout-heron` inherits a Baidu-lineage ImageNet backbone.
   Needs a ruling, not more research.** Resolved 2026-09-11 from primary sources; the
   remaining question is a policy call. The model is Apache-2.0 and IBM Research trained the
   *detector* on their own 150k-document corpus, but arXiv:2509.11720 states plainly that
   "the backbones (ResNet-50, ResNet-101, HGNet-V2) have been initialized with pre-trained
   weights", and that "we trained the models with their native code implementations". The
   native implementation for RT-DETRv2 is `lyuwenyu/RT-DETR` (the Baidu authors' repo), whose
   r50vd config sets `pretrained: True`, which resolves to
   `ResNet50_vd_ssld_v2_pretrained_from_paddle.pth` — a Baidu PaddleClas SSLD checkpoint.
   Corroborating: the `vd` variant is a PaddleClas lineage and torchvision ships no
   ResNet-vd, so no non-Paddle source of that backbone shape exists. There is no escape
   hatch inside Docling — the DFINE-based `egret` variants use HGNet-V2, whose D-FINE
   weights are `PPHGNetV2_B*_stage1.pth` ("PP" = PaddlePaddle), so all six layout variants
   inherit the same lineage.

   What is inherited is an ImageNet-1k *classification backbone* — convolutional features,
   no language, no text corpus — not a base model in the sense the rule was written for
   (Qwen, olmOCR-2, DeepSeek-OCR, where the inherited weights carry the behaviour). A strict
   provenance reading fails it anyway. The call is [USER]'s and has consequences past this
   repo: Paddle-lineage ImageNet backbones are pervasive in vision detectors, so a strict
   reading rules out most modern layout and table-structure models permanently and makes the
   geometry engine the only long-term option rather than the preferred one.

   Residual uncertainty: the paper never names the checkpoint. The chain above rests on
   their statement that they used the upstream repos' native code, where that checkpoint is
   the default — strong inference, not a maintainer's confirmation. A question to the
   Docling maintainers would settle it.

3. **LibreOffice's pinned major version is unknown**, and it is not installed on the dev
   machine, so the `.doc`/`.dot` path is entirely unexercised. Needs the client's available
   version.

Logged, not blocking:

4. Are the other CSVs (if any exist) reference tables or per-row records? Decides whether
   `record` mode is ever built. The one sample in hand is a reference table.
5. Is `kb/` markdown as sensitive as the source documents? Decides whether the content repo
   can hold committed content at all. Currently assumed yes, so `kb/` is gitignored.
6. Do any real samples carry a discoverable revision or effective date? If most come back
   `UNCONFIRMED`, the citation requirement needs an answer other than "cite the doc date".

For context on why the gate reports these on every run rather than filing them away: both
`models.yaml` entries sit in `pending_review`, the allowlist is empty, and the model gate
fails immediately if either model is ever actually downloaded.

## How PDFs are converted

**The default PDF engine is model-free.** `pdfplumber` reconstructs the document from
character geometry — word positions, font sizes, ruling lines — and no model is involved at
any point. Concretely it recovers reading order, heading levels from type size, ruled
tables, borderless tables from column alignment, and strips running headers and footers by
finding text that repeats in the same margin position across pages.

This is the default because triage showed the corpus is uniformly born-digital. With a clean
text layer on every page, a layout model buys borderless-table structure and unusual reading
orders, and costs a torch runtime, a model download, per-platform output variance, and an
unresolved base-weight provenance question. That is a poor default trade, and a reasonable
one for the specific documents that turn out to need it.

What it buys, beyond avoiding the provenance question: the default install carries **no ML
runtime at all**, nothing is fetched at runtime (so an air-gap is a non-issue for Stage 1),
output is byte-identical across machines, and a client can audit the extraction logic by
reading it — which is not true of model weights.

### Where geometry is weak, and how you find out

`triage` records the two things that decide whether a document needs more, and `report`
prints them under **LAYOUT NOTES**:

- **Multi-column pages.** Handled, but it is the likeliest place for reading order to go
  wrong. Detection is deliberately conservative — a gutter is only believed if it sits near
  the page centre with real text on both sides, because treating an indent as a column
  interleaves the page, which is far worse than treating a column as prose.
- **Borderless tables.** Recovered by column alignment, but only when at least
  `PDF_MIN_TABLE_ROWS` consecutive lines split into the same number of cells at aligned
  positions. The bias is deliberate: a hallucinated table destroys the paragraph it
  consumes, so a missed table beats an invented one.

Pages that yielded no usable text are named in the converted file itself, with an
`INCOMPLETE` callout listing the page numbers. A `partial` document otherwise omits its
scanned pages silently, which downstream is indistinguishable from a document that never
covered the topic.

### Escalating a document to Docling

Set `PDF_ENGINE=docling`. It is not wired up yet and will say so: it needs
`uv sync --extra pdf` for the ML runtime, and a decision on the layout model's base-weight
provenance (open question 2). Escalation is meant to be per document and deliberate, decided
by looking at converted output rather than assumed up front.

## Out of scope — do not build

Chunking, embeddings, pgvector, retrieval, eval harness, any LLM call, OCR of any kind, web
UI, serving. If `ollama` or a vector store appears in this repo, it has gone off-brief.
