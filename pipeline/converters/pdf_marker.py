"""PDF -> markdown via Marker, invoked as a separate program.

**This is an evaluation engine. Nothing it produces may be delivered as-is.**

Marker's *code* is Apache-2.0, but the Surya weights it drives carry a modified AI Pubs
Open RAIL-M licence: free for research, personal use, and organisations under $5M
funding/revenue, and otherwise requiring a commercial licence from Datalab. A paid federal
deployment is not inside that grant. The purpose of this module is to produce the evidence
that decides whether that licence is worth recommending -- converted output to compare
against the geometry engine on the same documents -- not to become a conversion path.

Two consequences are designed for rather than worked around.

**It is never imported.** Marker requires `torch >= 2.7`; torch's last macOS x86_64 wheel is
2.2.2, so marker cannot be installed into this project's venv on the dev machine at all, and
`uv lock` would reject it under `tool.uv.required-environments`. It is therefore invoked the
way LibreOffice is -- as a subprocess -- inside a pinned `linux/amd64` container. That keeps
the default install free of a second torch, keeps the licence gate's call-style rule
satisfied by construction, and makes the platform constraint a non-issue rather than a
blocker.

**Its model caches are pinned where the model gate can see them.** Surya fetches its
detection and OCR-error weights from `models.datalab.to` into platformdirs' `datalab` cache,
not from HuggingFace -- so a gate that scans only `HF_HOME` would report a clean run while
RAIL-M weights sat on disk. Both caches are bind-mounted from `WORK_DIR`, and
`scripts/model_gate.py` scans both. Running this engine is *meant* to fail `make check`; see
`models.yaml` `evaluation_only`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..config import MARKER_RUNNER_DOCKER, MARKER_RUNNER_LOCAL, Config

CONVERTER_MARKER = "marker (subprocess, {device}) -- EVALUATION, weights unlicensed here"

#: Surya's VLM recogniser runs under vllm (GPU-only) or llama.cpp. On CPU the only viable
#: backend is llama.cpp, and leaving the choice on `None` means "auto", which tries vllm
#: first and fails in a way that reads like a marker bug rather than a missing GPU.
MARKER_CPU_BACKEND = "llamacpp"

_CONTAINER_IN = "/in"
_CONTAINER_OUT = "/out"
_CONTAINER_HF_CACHE = "/cache/huggingface"
_CONTAINER_DATALAB_CACHE = "/cache/datalab"


class MarkerError(RuntimeError):
    """Marker could not be run, or ran and failed. Always names the fix."""


def marker_env(config: Config, *, containerised: bool) -> dict[str, str]:
    """Environment that pins Marker to CPU and to this project's caches.

    Every device knob is set explicitly. Surya resolves its device as `cuda > mps > cpu`
    when left unset, so on a machine with either it would quietly stop answering the
    question this evaluation exists to answer.
    """
    hf_cache = _CONTAINER_HF_CACHE if containerised else str(config.model_cache_dir)
    datalab_cache = _CONTAINER_DATALAB_CACHE if containerised else str(config.marker_cache_dir)
    return {
        "TORCH_DEVICE": config.marker_device,
        "FAST_DETECTOR_DEVICE": config.marker_device,
        "SURYA_INFERENCE_BACKEND": MARKER_CPU_BACKEND,
        "HF_HOME": hf_cache,
        "MODEL_CACHE_DIR": datalab_cache,
        # Marker will call a hosted LLM if it is handed a key. Blanking them here means an
        # ambient key in the environment cannot send document text to a third party, which
        # for this corpus would be a disclosure, not a convenience.
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "GEMINI_API_KEY": "",
        "GOOGLE_API_KEY": "",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONHASHSEED": "0",
    }


def build_command(source: Path, out_dir: Path, config: Config) -> list[str]:
    """The exact argv used to run Marker. Separated out so tests can assert it.

    A test on the command line is worth more than it looks: the CPU pin, the read-only
    source mount and the blanked API keys are all properties of this list, and all three are
    silent when they regress.
    """
    env = marker_env(config, containerised=config.marker_runner == MARKER_RUNNER_DOCKER)

    if config.marker_runner == MARKER_RUNNER_LOCAL:
        return [config.marker_binary, str(source), *_marker_flags(out_dir, config)]

    if config.marker_runner != MARKER_RUNNER_DOCKER:
        raise MarkerError(
            f"Unknown MARKER_RUNNER {config.marker_runner!r}. "
            f"Use {MARKER_RUNNER_DOCKER!r} or {MARKER_RUNNER_LOCAL!r}."
        )

    command = [
        "docker", "run", "--rm",
        # The image is linux/amd64. Stating it means an arm64 host emulates rather than
        # failing at the first instruction, and an x86_64 host is unaffected.
        "--platform", "linux/amd64",
        # Container-written files land in WORK_DIR on the host; without this they are
        # root-owned and the next `make clean-work` cannot delete them.
        "-u", f"{os.getuid()}:{os.getgid()}",
        # SOURCE_DIR is read-only for the whole pipeline. `:ro` is that rule expressed
        # somewhere the kernel enforces it.
        "-v", f"{source.parent}:{_CONTAINER_IN}:ro",
        "-v", f"{out_dir}:{_CONTAINER_OUT}",
        "-v", f"{config.model_cache_dir}:{_CONTAINER_HF_CACHE}",
        "-v", f"{config.marker_cache_dir}:{_CONTAINER_DATALAB_CACHE}",
    ]
    for key, value in env.items():
        command += ["-e", f"{key}={value}"]
    command += [
        config.marker_image,
        config.marker_binary,
        f"{_CONTAINER_IN}/{source.name}",
        *_marker_flags(Path(_CONTAINER_OUT), config),
    ]
    return command


def _marker_flags(out_dir: Path, config: Config) -> list[str]:
    flags = ["--output_format", "markdown", "--output_dir", str(out_dir)]
    if config.marker_page_range:
        flags += ["--page_range", config.marker_page_range]
    return flags


def convert(path: Path, config: Config) -> tuple[str, str]:
    """Convert one PDF with Marker. Returns `(markdown_body, converter_name)`."""
    _require_runner(config)

    out_dir = config.work_dir / "marker" / path.stem
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    config.model_cache_dir.mkdir(parents=True, exist_ok=True)
    config.marker_cache_dir.mkdir(parents=True, exist_ok=True)

    command = build_command(path, out_dir, config)
    environment = dict(os.environ)
    if config.marker_runner == MARKER_RUNNER_LOCAL:
        environment.update(marker_env(config, containerised=False))

    try:
        completed = subprocess.run(  # noqa: S603 - argv list, no shell
            command,
            capture_output=True,
            text=True,
            timeout=config.marker_timeout,
            env=environment,
        )
    except FileNotFoundError as exc:
        raise MarkerError(_missing_runner_message(config)) from exc
    except subprocess.TimeoutExpired as exc:
        raise MarkerError(
            f"Marker exceeded MARKER_TIMEOUT ({config.marker_timeout}s) on {path.name}. "
            "On CPU this is an expected outcome for a long document rather than a "
            "malfunction -- raise MARKER_TIMEOUT, or narrow the run with MARKER_PAGE_RANGE, "
            "and record the timing either way: how slow it is on CPU is part of what this "
            "evaluation is measuring."
        ) from exc

    if completed.returncode != 0:
        raise MarkerError(
            f"Marker exited {completed.returncode} on {path.name}.\n"
            f"  command: {' '.join(command)}\n"
            f"  stderr tail:\n{_tail(completed.stderr)}"
        )

    return _read_output(out_dir, path, config), CONVERTER_MARKER.format(
        device=config.marker_device
    )


def _require_runner(config: Config) -> None:
    executable = "docker" if config.marker_runner == MARKER_RUNNER_DOCKER else config.marker_binary
    if shutil.which(executable) is None:
        raise MarkerError(_missing_runner_message(config))


def _missing_runner_message(config: Config) -> str:
    if config.marker_runner == MARKER_RUNNER_DOCKER:
        return (
            "`docker` is not on PATH (or its daemon is not running), and the Marker "
            "evaluation runs inside a container by design: marker needs torch >= 2.7, which "
            "has no macOS x86_64 wheel, so it cannot be installed on this machine directly. "
            "Start Docker, then `make marker-image` to build "
            f"{config.marker_image}. On a Linux box with marker already installed, set "
            "MARKER_RUNNER=local instead."
        )
    return (
        f"`{config.marker_binary}` is not on PATH. MARKER_RUNNER=local expects marker "
        "installed in its own environment (never this project's venv -- it would pull a "
        "second torch and break the lock's platform constraint)."
    )


def _read_output(out_dir: Path, source: Path, config: Config) -> str:
    """Find the markdown Marker wrote, and say plainly what was left behind.

    Marker writes `<out>/<stem>/<stem>.md` alongside extracted images and a metadata JSON.
    The images are real files that the markdown links to, and `kb/` holds markdown only --
    so a converted file would carry image links that resolve to nothing. Naming them in the
    output is the same rule the geometry engine follows for pages with no text: a gap that
    is visible is answerable, one that is silent is not.
    """
    candidates = sorted(out_dir.rglob("*.md"))
    if not candidates:
        raise MarkerError(
            f"Marker produced no markdown for {source.name} under {out_dir}. "
            "Its exit status was 0, so this is a Marker behaviour change, not a failure "
            "here -- inspect that directory before trusting any other result."
        )
    body = candidates[0].read_text(encoding="utf-8").strip()

    images = sorted(
        path.name
        for path in out_dir.rglob("*")
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    if images:
        listed = ", ".join(images[:10]) + (", ..." if len(images) > 10 else "")
        note = (
            f"> **{len(images)} extracted image(s) are not represented below** and the "
            f"links to them do not resolve: {listed}. `kb/` holds markdown only.\n"
            f"> They are in {out_dir}."
        )
        body = f"{note}\n\n{body}"

    return _evaluation_banner(config) + body


def _evaluation_banner(config: Config) -> str:
    """Mark the output as unlicensed for delivery, in the file itself.

    A converted file outlives the shell that produced it. If a RAIL-M-derived conversion is
    ever going to be mistaken for a deliverable one, it will be by someone reading `kb/`
    months from now, not by whoever ran the command.
    """
    return (
        "> **EVALUATION OUTPUT — NOT FOR DELIVERY.** Produced by Marker on "
        f"{config.marker_device}. Marker's code is Apache-2.0, but the Surya weights that "
        "produced this text are under a modified AI Pubs Open RAIL-M licence, free only for "
        "research, personal use, and organisations under $5M funding/revenue. Delivering "
        "this content requires a commercial licence from Datalab.\n\n"
    )


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join((text or "").strip().splitlines()[-lines:])
