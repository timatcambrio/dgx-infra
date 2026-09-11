# Evaluating Marker on CPU

**Status: branch `eval/marker-cpu`. Nothing here is a delivery path.**

The question this branch exists to answer, in the client's words, is whether Marker is good
enough on this corpus to be worth recommending that they buy a licence. It is deliberately
*not* the question "should we switch to Marker" — that one cannot be asked until the licence
question is settled, and settling it costs money.

## What is actually blocked, and by what

Three separate constraints, which are easy to run together and shouldn't be.

### 1. The weights are not licensed for this deployment — this is the real blocker

Marker's **code** is Apache-2.0 (it changed from GPL; the project's 2026-09-04 note recording
"Marker (GPL)" is stale). The **Surya weights** it drives are not. Every model in the chain —
`datalab-to/surya-ocr-2`, `surya-ocr-2-gguf`, `surya_layout2` — carries `license: openrail`
on HuggingFace, pointing at Datalab's modified AI Pubs Open RAIL-M: free for research,
personal use, and organisations under $5M funding/revenue, and otherwise requiring a
commercial licence.

A paid federal deployment is outside that grant. **A local evaluation by us is inside it** —
that is exactly the research/personal use the licence permits — which is why this branch can
exist at all without buying anything first.

Two of the five models are worse than restricted: `text_detection` and `ocr_error_detection`
are fetched from `models.datalab.to` rather than HuggingFace and carry **no declared licence
at the distribution point**. A commercial licence would presumably cover them; that is a
question for Datalab, not an assumption to make here.

### 2. A provenance question that a licence would not resolve

`datalab-to/surya-ocr-2` is a 650M-parameter VLM whose `config.json` declares
`model_type: qwen3_5`, `architectures: ["Qwen3_5ForConditionalGeneration"]` — Alibaba's
Qwen3.5 architecture — and whose model card names **no base model**.

Architecture alone does not fail this project's rule; the rule is about weights. And no
published Qwen3.5 checkpoint is 650M, which is consistent with Datalab having trained from
scratch. But "consistent with" is not evidence, and under the rule an unresolved provenance
question is a failure, not a pass. **If a licence is ever seriously considered, ask Datalab
in the same conversation what the weights were initialised from.** They are the only source
that can close it. The same applies to `surya_layout2`'s rf-detr backbone, which is
unstated — the identical question that ran `docling-layout-heron` aground.

### 3. Marker cannot be installed on the dev machine at all

`marker-pdf` 2.0.0 requires `torch<3,>=2.7.0`. Torch's **last macOS x86_64 wheel is 2.2.2**
(verified on PyPI, 2026-09-11: the 2.7.0 release ships `macosx_11_0_arm64` but no x86_64).
This machine is an Intel Mac. So Marker is not installable here in any environment, and it
could not join this project's lock in any case — `tool.uv.required-environments` requires a
darwin-x86_64 wheel for every locked dependency, and `uv lock` would refuse.

This is a platform fact, not a licence one, and it is why the engine is containerised.

## How it is wired up

`PDF_ENGINE=marker` routes `pipeline/converters/pdf.py` to `pdf_marker.py`, which runs
`marker_single` **as a subprocess inside a pinned `linux/amd64` container** — the same
call-style rule that lets this project use LibreOffice. Marker is never imported. Nothing in
the default install changes: no second torch, no ML runtime, no effect on `uv.lock`.

```bash
make marker-image                      # build dgx-infra/marker:2.0.0 (CPU torch, no CUDA)
PDF_ENGINE=marker make convert         # or: make marker-eval
make marker-gate                       # model gate, acknowledging the evaluation weights
```

Properties worth knowing about, each enforced by a test in `tests/test_marker.py`:

- **CPU is pinned explicitly** (`TORCH_DEVICE`, `FAST_DETECTOR_DEVICE`). Surya resolves its
  device as `cuda > mps > cpu` when unset, so an unpinned run on a different machine would
  quietly stop answering the question.
- **`SOURCE_DIR` is mounted `:ro`.** The read-only rule, expressed where the kernel enforces it.
- **Hosted-LLM API keys are blanked.** Marker will call OpenAI, Anthropic or Gemini if handed
  a key. For this corpus that is a disclosure, not a convenience.
- **Every output file carries a NOT FOR DELIVERY banner** naming the licence. A converted
  file outlives the shell that produced it.
- **Extracted images are named in the output.** Marker writes images as separate files and
  links them; `kb/` is markdown only, so those links would dangle silently.

## Why the model gate had to change, and how

Surya downloads its detection and OCR-error weights from `models.datalab.to` into
platformdirs' `datalab` cache — **not** HuggingFace. A gate scanning only `HF_HOME` would
have reported a clean run with RAIL-M weights sitting on disk: the same shape of blind spot
as the rapidocr wheel that bundled Baidu weights into `site-packages`.

So `config.marker_cache_dir` pins that cache into `WORK_DIR`, `default_cache_dirs()` scans
it, and `models.yaml` gains an **`evaluation_only`** section for models that are known not to
be deliverable and are being run anyway. The gate **fails** on any of them found in a cache.
`make check` and CI keep failing; only an explicit `--allow-evaluation` (`make marker-gate`)
downgrades them to a warning, and it says so in its output.

That ordering is the point: an evaluation is a thing you opt into per invocation, not a
setting someone flips once.

## Results

<!-- RESULTS -->

## If the recommendation is to buy

Questions for Datalab, in one conversation:

1. Commercial licence terms and price for an on-premises federal deployment with no
   telemetry and no outbound network.
2. **What were the `surya-ocr-2` weights initialised from?** A Qwen3.5-architecture model
   with no declared base is an open provenance question under this project's rule, and a
   commercial licence does not close it.
3. The same question for `surya_layout2`'s rf-detr backbone.
4. Do the commercial terms cover the two undeclared `models.datalab.to` checkpoints?
5. Is there an offline/air-gapped distribution of the weights? The client-runs-the-pipeline
   shape cannot absorb a runtime download from `models.datalab.to`.
