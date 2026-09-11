# Marker, pinned, CPU-only, linux/amd64 — for EVALUATION (see pipeline/converters/pdf_marker.py).
#
# Why a container at all: marker-pdf 2.0.0 requires `torch<3,>=2.7.0`, and torch's last
# macOS x86_64 wheel is 2.2.2 (verified on PyPI 2026-09-11). Marker is therefore not
# installable on the dev machine in any environment, venv or otherwise. It is also not
# installable *alongside* this project: `tool.uv.required-environments` requires every
# locked dependency to have a wheel on darwin-x86_64, and `uv lock` would refuse.
#
# A container answers both, and keeps the call-style licence rule satisfied by construction:
# marker runs as a separate program, never imported into our process.
#
#   docker build --platform linux/amd64 -f docker/marker.Dockerfile -t dgx-infra/marker:2.0.0 .
#
# Nothing in this image is delivered to the client. The Surya weights it downloads are under
# a revenue-capped RAIL-M licence; `models.yaml` `evaluation_only` records that, and
# `scripts/model_gate.py` fails on them unless run with --allow-evaluation.

FROM --platform=linux/amd64 python:3.12-slim-bookworm

# Pinned so a rebuild months from now compares like with like. An evaluation whose subject
# silently changed version is not evidence of anything.
ARG MARKER_VERSION=2.0.0
ARG TORCH_VERSION=2.7.1
ARG TORCHVISION_VERSION=0.22.1

# Surya's VLM recogniser needs llama.cpp on CPU (vllm is GPU-only), and marker reaches it
# only when a page actually has to be OCR'd. Both halves are confirmed by running it:
# born_digital.pdf converted fine without llama-server, and image_only.pdf died on
# `SpawnError: llama-server binary not found`.
#
# On by default because the no-text-layer documents are the ones the evaluation exists to
# measure. Build with --build-arg WITH_LLAMA_CPP=0 for a smaller image that can convert
# born-digital PDFs only.
#
# llama.cpp is MIT, and it is a separate binary invoked as a server process -- not linked
# into anything here.
ARG WITH_LLAMA_CPP=1
ARG LLAMA_CPP_RELEASE=b10909

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl libgl1 libglib2.0-0 libgomp1 libcurl4 \
    && rm -rf /var/lib/apt/lists/*

# torch AND torchvision from the CPU index first, then marker under a constraints file that
# forbids upgrading either.
#
# Both halves are load-bearing and the second one was learned the hard way. Installing torch
# alone is not enough: marker requires `torchvision>=0.20`, and the newest torchvision on
# PyPI pins a newer torch, so pip cheerfully replaced the CPU build with torch 2.14.0 and
# started pulling half a gigabyte of nvidia CUDA wheels -- on a CPU-only evaluation, on a
# machine with no GPU. Pinning the pair together and constraining the marker install is what
# actually keeps CUDA out of the image.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}"
RUN printf 'torch==%s\ntorchvision==%s\n' "${TORCH_VERSION}" "${TORCHVISION_VERSION}" \
        > /tmp/constraints.txt \
    && pip install --no-cache-dir -c /tmp/constraints.txt "marker-pdf==${MARKER_VERSION}" \
    && python -c "import torch; assert '+cpu' in torch.__version__, torch.__version__; print(torch.__version__)"

# Marker downloads a rendering font into site-packages on first use. The container runs as
# the calling user's uid, which cannot write there, so the run dies on a PermissionError
# before it reaches a single page. Fetching it at build time fixes that and removes a runtime
# network dependency at the same time -- which matters beyond this image, since the client
# deployment is air-gapped and "it worked on the dev machine" would have hidden it.
RUN python -c "from marker.util import download_font; download_font()" \
    && chmod -R a+rwX /usr/local/lib/python3.12/site-packages/static

RUN if [ "${WITH_LLAMA_CPP}" = "1" ]; then \
        curl -fsSL -o /tmp/llama.tar.gz \
          "https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_CPP_RELEASE}/llama-${LLAMA_CPP_RELEASE}-bin-ubuntu-x64.tar.gz" \
        && mkdir -p /opt/llama && tar -xzf /tmp/llama.tar.gz -C /opt/llama \
        && find /opt/llama -type f -name 'llama-server' -exec install -m 0755 {} /usr/local/bin/ \; \
        && find /opt/llama -type f -name '*.so*' -exec install -m 0755 {} /usr/local/lib/ \; \
        && ldconfig \
        && rm -f /tmp/llama.tar.gz \
        && llama-server --version; \
    fi

# Caches are bind-mounted from WORK_DIR at run time so the model gate can see every weight
# that lands on disk. These defaults matter only if someone runs the image by hand.
ENV HF_HOME=/cache/huggingface \
    MODEL_CACHE_DIR=/cache/datalab \
    TORCH_DEVICE=cpu \
    FAST_DETECTOR_DEVICE=cpu \
    TOKENIZERS_PARALLELISM=false \
    HOME=/tmp

RUN mkdir -p /in /out /cache/huggingface /cache/datalab && chmod 0777 /cache /cache/* /out

# The container runs as the calling user's uid (see pdf_marker.build_command) so that files
# written into WORK_DIR stay deletable on the host.
WORKDIR /out
ENTRYPOINT []
CMD ["marker_single", "--help"]
