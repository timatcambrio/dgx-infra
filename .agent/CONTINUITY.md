# CONTINUITY — dgx-infra

Canonical briefing. Facts only, each with an ISO date and a provenance tag.

## [PLANS]

- 2026-09-11 [USER] Evaluate Marker locally on CPU to decide whether to recommend that the
  client buy a Datalab commercial licence. Branch `eval/marker-cpu`.
- 2026-09-11 [CODE] Evaluation must not weaken either policy gate: `make check` and CI still
  fail while Marker's weights are on disk.

## [DECISIONS]

- 2026-09-11 [CODE] Marker is invoked as a **subprocess in a pinned linux/amd64 container**,
  never imported. Same call-style rule as LibreOffice.
- 2026-09-11 [CODE] `models.yaml` gains an `evaluation_only` tier, distinct from
  `pending_review`: pending = the question is open; evaluation = the answer is already no
  and we are running it anyway to produce evidence. Gate FAILs on those weights unless
  `--allow-evaluation` is passed per invocation.
- 2026-09-11 [CODE] Marker output carries a NOT-FOR-DELIVERY banner in the file itself.

## [DISCOVERIES]

- 2026-09-11 [TOOL] `marker-pdf` 2.0.0 requires `torch<3,>=2.7.0`; torch's last macOS
  x86_64 wheel is **2.2.2** (PyPI). Marker is therefore **uninstallable on this Intel dev
  Mac** in any environment, and could never join `uv.lock` under
  `tool.uv.required-environments`. Platform fact, not a licence one.
- 2026-09-11 [TOOL] Surya fetches `text_detection` and `ocr_error_detection` from
  `models.datalab.to`, **not HuggingFace**, into platformdirs' `datalab` cache. A gate
  scanning only `HF_HOME` was blind to them — same shape as the rapidocr-bundled-weights
  miss. Now pinned to `$WORK_DIR/models-datalab` and scanned.
- 2026-09-11 [TOOL] `datalab-to/surya-ocr-2` is 650M params, `model_type: qwen3_5`,
  `Qwen3_5ForConditionalGeneration`, **no declared base model**, `license: openrail`.
  Provenance is therefore OPEN, not cleared — and a commercial licence would not close it.
- 2026-09-11 [TOOL] Surya's recogniser runs under vllm (GPU-only) or llama.cpp. On CPU only
  llama.cpp is viable, so a CPU run that reaches the VLM needs `llama-server` in the image
  (`--build-arg WITH_LLAMA_CPP=1`).

## [PROGRESS]

- 2026-09-11 [CODE] Implemented: `pipeline/converters/pdf_marker.py`, `PDF_ENGINE=marker`
  dispatch, `docker/marker.Dockerfile`, `models.yaml` `evaluation_only`, model-gate
  evaluation tier + datalab cache scan, `make marker-image|marker-eval|marker-gate`,
  `tests/test_marker.py` + gate tests, `docs/marker-evaluation.md`.

## [OUTCOMES]

- 2026-09-11 [TOOL] Full suite green, both gates pass with the evaluation tier in place.
- 2026-09-11 [TOOL] Empirical CPU run on the dev Mac (Docker, 4 CPU / 8 GB), full numbers in
  `docs/marker-evaluation.md`:
    * `born_digital.pdf` 1m44s, content identical to the geometry engine.
    * `image_only.pdf` **OCR'd correctly in 5m17s** where geometry emits only a stub. This is
      the entire case for a licence.
    * `mojibake.pdf` **passed the broken text layer through unchanged in 31s** -- Marker's
      OCR-error detector did not catch it, this project's `alpha_ratio` triage does. Marker
      is therefore not a replacement for the triage gate.
- 2026-09-11 [TOOL] CPU-only works end to end, via llama.cpp only (vllm is GPU-only). ~1.8 GB
  of weights fetched on first use, across TWO caches (HF + models.datalab.to).
- 2026-09-11 [ASSUMPTION] ~5 min/scanned page on 4 cores extrapolates to ~9h for a 100-page
  scanned document on this machine. Linear extrapolation, not measured at that size.
