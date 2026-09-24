# PDF parser and OCR landscape: licence and model provenance

Compiled 2026-09-11 against this project's two hard filters:

1. **Licence.** Copyleft imported as a library is banned; copyleft invoked as a subprocess is
   allowed. Field-of-use restrictions (non-commercial, revenue caps) are disqualifying for a
   paid government deployment regardless of what the licence is called.
2. **Model provenance.** Judged on base weights, not the releasing organisation. A US lab
   fine-tuning a Chinese base does not clear it.

A third, softer filter runs underneath: anything requiring a runtime model download conflicts
with the client-runs-the-pipeline distribution shape.

**The `Checked` column is load-bearing.** ✔ means the licence was read from the repository or
model card on 2026-09-11 and the finding is quoted below. `—` means it is not verified and
must be before anyone relies on it. Do not promote a `—` row to a decision without checking.

## Text-layer extraction (no models)

| Tool | Code licence | Weights | Provenance | Verdict | Checked |
|---|---|---|---|---|---|
| **pdfplumber** | MIT | none | n/a | **Clear** — in use as the default engine | ✔ |
| **PyMuPDF** / pymupdf4llm | AGPL-3.0 | none | n/a | **Fails** — import-only copyleft, permanently out | ✔ (prior) |
| pdfminer.six | MIT | none | n/a | **Clear** | ✔ |
| pypdf | BSD-3-Clause | none | n/a | **Clear** — GitHub reports NOASSERTION; the LICENSE file is plainly BSD-3-Clause | ✔ |
| pypdfium2 (PDFium) | REUSE multi-licence — bindings Apache-2.0 / BSD-3, but the `LICENSES/` directory also carries **LGPL-3.0-or-later** and **MPL-2.0** | none | PDFium is Google's, C++ | **Needs a per-file REUSE read before use** — the top-level licence field is null and the tree is mixed | ✔ (flagged) |
| Camelot | MIT | none | n/a | **Clear**; tables only | ✔ |
| Apache Tika | Apache-2.0 | none | n/a | **Clear**; JVM dependency | ✔ |
| **MarkItDown** | MIT | none by default | n/a | **Clear** — in use for Office formats | ✔ |

## OCR engines

| Tool | Code licence | Weights | Provenance | Verdict | Checked |
|---|---|---|---|---|---|
| **Tesseract** | Apache-2.0 | Apache-2.0 traineddata | Google / UNLV lineage | **Clear** | ✔ code |
| **docTR** | Apache-2.0 | Apache-2.0 | Trained by Mindee (France) | **Clear** — best permissive OCR fit | ✔ code |
| EasyOCR | Apache-2.0 | — | Detector is CRAFT (`clovaai/CRAFT-pytorch`, **MIT**, Naver, Korea); recogniser trained by JaidedAI | **Clear on provenance**; weights licence not separately declared | ✔ code + CRAFT |
| **PaddleOCR** | Apache-2.0 | Baidu | **Baidu (China)** | **Fails** provenance | ✔ code |
| **RapidOCR** | Apache-2.0 | wraps PaddleOCR weights | **Baidu (China)** | **Fails** — and its wheel bundles ~30MB of weights into site-packages, invisible to a cache-scanning gate | ✔ (prior) |

## Layout models and end-to-end document pipelines

| Tool | Code licence | Weights licence | Base-weight provenance | Verdict | Checked |
|---|---|---|---|---|---|
| **granite-docling-258M** | Apache-2.0 | Apache-2.0 | Idefics3 architecture, **SigLIP2 vision encoder (Google) + Granite 165M LLM (IBM)**, trained by IBM Research on SynthCodeNet / SynthFormulaNet / SynthChartNet / DoclingMatix | **Clear on both filters** — see note below | ✔ |
| **Docling** (`docling-slim`) | MIT | mixed — see rows below | mixed | **Mixed** — pending ruling | ✔ (prior) |
| ├ TableFormer | — | Apache-2.0 + CDLA-permissive-2.0 | IBM's own weights, not a fine-tune | **Clear** | ✔ (prior) |
| └ docling-layout-heron | — | Apache-2.0 | RT-DETRv2-r50vd; detector trained by IBM on 150k docs, but backbone initialised from `ResNet50_vd_ssld_v2_pretrained_from_paddle.pth` (**Baidu PaddleClas**) | **Fails a strict reading** — awaiting ruling | ✔ |
| **Marker** | **Apache-2.0** | **modified AI Pubs Open RAIL-M** — free only for research, personal use, and orgs under $5M funding/revenue | Uses Surya | **Fails** — field-of-use restriction on the weights. Note the code licence changed from GPL; the licence string alone would mislead here | ✔ |
| **Surya** | **Apache-2.0** | datalab modified RAIL-M (same family as Marker) | Qwen-*style* architecture without Qwen weights — architecture alone does not fail | **Weights licence is the blocker**, not provenance | ✔ code |
| **MinerU** | Apache-2.0 **plus additional commercial terms** (100M MAU / $20M monthly revenue thresholds, attribution required) | — | OpenDataLab / Shanghai AI Lab (China) | **Fails** both | ✔ |
| **olmOCR-2** | Apache-2.0 | Apache-2.0 | `base_model: Qwen/Qwen2.5-VL-7B-Instruct` | **Fails** provenance — permissive licence, Chinese base | ✔ |
| **Nanonets-OCR-s** | — | — | `base_model: Qwen/Qwen2.5-VL-3B-Instruct` | **Fails** provenance | ✔ |
| **dots.ocr** | MIT | MIT | RedNote HiLab (Xiaohongshu), built on Qwen2.5-VL | **Fails** provenance | ✔ |
| **DeepSeek-OCR** | MIT | MIT | DeepSeek AI (China) | **Fails** provenance | ✔ |
| **LayoutLMv3** | — | **CC-BY-NC-SA-4.0** | Microsoft | **Fails** — non-commercial licence, disqualifying for a paid deployment | ✔ |
| Table Transformer | MIT | MIT | DETR-based, trained on PubTables-1M; backbone lineage not stated on the card | Probably clear; backbone unverified | ✔ licence |
| Nougat | **MIT** (code) | not verified — believed CC-BY-NC | Meta | Code clear; **weights licence must be read before use** | ✔ code only |
| GOT-OCR2 | Apache-2.0 | Apache-2.0 | Wei et al., **UCAS (China)** / StepFun; purpose-built, not a fine-tune of a named base | **Fails** provenance — permissive licence, Chinese-origin weights | ✔ |
| GROBID | Apache-2.0 | — | Scientific-paper focus, poor fit for handbooks | **Clear** on licence; wrong tool for handbooks | ✔ code |

## Cloud APIs

Azure Document Intelligence, AWS Textract and Google Document AI are all proprietary and
send document content to a third party. Excluded by the deployment shape, not by licence.

## What this changes

**`granite-docling-258M` is the significant finding.** It is Docling's VLM pipeline rather
than its layout-model pipeline, it is Apache-2.0 end to end, and its two inherited components
are Google's SigLIP2 and IBM's own Granite 165M — no Chinese base weights anywhere. If it
holds up, it clears both filters where `docling-layout-heron` does not, which means the
Docling escalation path may be viable via a different pipeline regardless of how the heron
ruling goes. Not yet verified: inference cost on the target hardware, whether a 258M VLM is
actually better than geometry on this corpus, and whether it introduces a runtime download
the distribution shape cannot absorb.

**Marker and Surya are no longer GPL.** Both are Apache-2.0 as of this check. The project's
2026-09-04 `[DECISIONS]` entry recording "Marker (GPL)" is stale on the code licence — but the
conclusion still holds for a different reason: the *weights* carry a revenue-capped RAIL-M
licence, which fails independently.

**The pattern worth noting.** Of the permissively licensed, genuinely capable document VLMs,
almost all are Chinese-based (olmOCR-2, Nanonets, dots.ocr, DeepSeek-OCR all trace to
Qwen or a Chinese lab), and almost all of the non-Chinese ones carry either a non-commercial
licence (LayoutLMv3) or a revenue cap (Marker, Surya). `granite-docling` and the
classical-OCR stack (Tesseract, docTR) are the exceptions. The provenance rule is not a
marginal constraint in this space — it removes most of the field.
