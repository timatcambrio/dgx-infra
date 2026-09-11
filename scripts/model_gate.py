#!/usr/bin/env python3
"""Model gate — two independent tests, both of which a model must pass.

1. **Licence.** Weights must be permissively licensed. A permissive *code* licence attached
   to restrictively-licensed *weights* does not count; the weights are what gets shipped.
2. **Base-weight provenance.** Judged on the base weights, not on the releasing organisation
   or the software distributor. A US company fine-tuning or repackaging a Chinese base does
   not clear the rule.

`models.yaml` is an explicit allowlist. Anything found in a model cache that is not listed
fails, which is what makes the gate catch an OCR model appearing by accident — precisely the
scenario this phase is built to avoid, since OCR is meant to be off entirely.

The gate is deliberately meaningful from day one: in this phase the allowlist holds only
Docling's layout and table-structure models.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODELS_FILE = REPO_ROOT / "models.yaml"

REQUIRED_FIELDS = ("model", "license", "developer", "base_model", "base_developer")

#: Permissive weight licences. Deliberately short: anything not on it needs a human read,
#: because "open" model licences frequently carry field-of-use restrictions (RAIL, Llama
#: Community, research-only) that a federal client cannot accept.
PERMISSIVE_LICENSES = frozenset(
    {
        "apache-2.0",
        "mit",
        "bsd-2-clause",
        "bsd-3-clause",
        "cc-by-4.0",
        "cc0-1.0",
        "cdla-permissive-2.0",
    }
)

#: Values meaning "nobody has checked yet". Treated as a failure, not as a pass, wherever a
#: model is actually in use: an unanswered provenance question is not evidence of compliance.
UNRESOLVED = frozenset({"unconfirmed", "unknown", "undeclared", "tbd", "", "none"})

#: Substrings identifying Chinese base-weight provenance. Matched against `base_developer`
#: and against the base model name, since a fine-tune usually carries its base in its name.
CHINESE_PROVENANCE = (
    "baidu", "paddle", "alibaba", "qwen", "deepseek", "zhipu", "glm", "01.ai", "yi-",
    "moonshot", "kimi", "sensetime", "megvii", "iflytek", "tencent", "hunyuan",
    "bytedance", "opengvlab", "internlm", "internvl", "shanghai ai", "minicpm",
    "thudm", "openbmb", "modelscope", "baichuan", "xverse", "skywork", "ernie",
    "dots.ocr", "mineru", "rapidocr",
    # Vision backbones and detector architectures of Chinese origin. These matter because a
    # layout model is usually "someone else's detector, retrained": HGNet/PP-HGNet is Baidu's
    # (PaddleClas), and D-FINE is from USTC. Docling's `egret` layout variants are built on
    # both, so switching layout model "for better accuracy" would be a provenance regression.
    "hgnet", "pp-hgnet", "d-fine", "dfine",
)

_HF_CACHE_DIR = re.compile(r"^models--(?P<org>.+?)--(?P<name>.+)$")


@dataclass
class Finding:
    level: str
    subject: str
    detail: str


def default_cache_dirs() -> list[Path]:
    """Where a model would land if something fetched one.

    Deliberately the project's own pinned cache (see `config.model_cache_dir`), not the
    machine-wide `~/.cache/huggingface`. Scanning the shared cache would fail the gate on
    models belonging to unrelated work, and a gate that cries wolf gets switched off.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from pipeline import config as pipeline_config  # noqa: PLC0415 - needs the path above

    config = pipeline_config.load()
    # The datalab cache is not optional decoration. Surya pulls its detection and OCR-error
    # weights from models.datalab.to rather than HuggingFace, so a gate that knows only about
    # HF_HOME reports a clean run while RAIL-M weights sit on disk -- the same class of blind
    # spot as weights bundled inside a wheel, which this project has already been bitten by.
    return [
        config.model_cache_dir,
        config.model_cache_dir / "hub",
        config.marker_cache_dir,
    ]


def load_models(path: Path) -> tuple[list[dict], list[dict], dict]:
    """Return `(allowed_models, pending_review, whole_document)`.

    `allowed_models` is missing-key-fatal rather than defaulted: a typo in the key name must
    not silently produce an allowlist that permits nothing and passes.
    """
    if not path.is_file():
        raise SystemExit(f"model_gate: allowlist not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models = data.get("allowed_models")
    if models is None:
        raise SystemExit(f"model_gate: {path} has no `allowed_models` key")
    return models, data.get("pending_review") or [], data


def has_chinese_provenance(*values: str | None) -> str | None:
    """Return the matched marker, or None. Case-insensitive substring match."""
    for value in values:
        if not value:
            continue
        lowered = value.lower()
        for marker in CHINESE_PROVENANCE:
            if marker in lowered:
                return marker
    return None


def check_entry(entry: dict, index: int) -> tuple[str, list[str]]:
    """Validate one allowlist entry. Returns `(name, problems)`."""
    name = entry.get("model") or f"<entry {index}>"

    missing = [field for field in REQUIRED_FIELDS if field not in entry]
    if missing:
        return name, [f"missing required field(s): {', '.join(missing)}"]

    problems: list[str] = []

    license_id = str(entry["license"]).strip().lower()
    if license_id in UNRESOLVED:
        problems.append(
            f"licence is {entry['license']!r}. An undeclared or unverified licence is not a "
            "permissive one -- absent an explicit grant, all rights are reserved."
        )
    elif license_id not in PERMISSIVE_LICENSES:
        problems.append(
            f"weights licence {entry['license']!r} is not on the permissive list "
            f"({', '.join(sorted(PERMISSIVE_LICENSES))})."
        )

    if str(entry.get("base_developer", "")).strip().lower() in UNRESOLVED:
        problems.append(
            "base-weight provenance is unresolved. The rule is judged on the base weights, "
            "so an unanswered question is not evidence of compliance."
        )

    marker = has_chinese_provenance(
        entry.get("base_developer"), entry.get("base_model"), entry.get("model")
    )
    if marker:
        problems.append(
            f"base-weight provenance matches {marker!r}. Judged on the base weights, not on "
            "the releasing organisation."
        )

    return name, problems


def check_allowlist(models: list[dict]) -> list[Finding]:
    findings: list[Finding] = []
    for index, entry in enumerate(models):
        name, problems = check_entry(entry, index)
        if problems:
            findings += [Finding("FAIL", name, problem) for problem in problems]
        else:
            findings.append(
                Finding(
                    "OK",
                    name,
                    f"{entry['license']}, base {entry.get('base_model') or 'n/a'} "
                    f"({entry.get('base_developer')})",
                )
            )
    return findings


def check_pending(pending: list[dict]) -> list[Finding]:
    """Report models that are known-needed but not cleared.

    Non-fatal on its own -- nothing is fetched in this phase -- but printed on every run so
    an unresolved licence or provenance question stays visible instead of decaying into a
    forgotten comment. `scan_caches` turns it fatal the moment one is actually downloaded.
    """
    findings: list[Finding] = []
    for index, entry in enumerate(pending):
        name, problems = check_entry(entry, index)
        detail = "; ".join(problems) if problems else "no outstanding problems recorded"
        findings.append(Finding("REVIEW", name, f"NOT approved -- {detail}"))
    return findings


def check_evaluation(evaluation: list[dict]) -> list[Finding]:
    """Report models that exist in this repo only to be measured, never delivered.

    Distinct from `pending_review` because the question is different in kind. A pending model
    might clear; an evaluation model is one we already know does not, and is being run anyway
    to find out whether it is worth buying a licence for. Reporting them identically would
    lose exactly the distinction that makes a licence recommendation defensible.
    """
    findings: list[Finding] = []
    for index, entry in enumerate(evaluation):
        name, problems = check_entry(entry, index)
        detail = "; ".join(problems) if problems else "no licence or provenance problem recorded"
        findings.append(
            Finding("EVAL", name, f"evaluation only, NOT deliverable -- {detail}")
        )
    return findings


def scan_caches(
    models: list[dict],
    pending: list[dict],
    cache_dirs: list[Path],
    evaluation: list[dict] | None = None,
    allow_evaluation: bool = False,
) -> list[Finding]:
    """Fail on anything cached that is not on the allowlist.

    A `pending_review` model found in a cache gets its own message, because "someone started
    M2 before the provenance question was answered" is a different problem from "an unknown
    model appeared" and deserves to read that way.
    """
    findings: list[Finding] = []
    evaluation = evaluation or []
    allowed_repos = {str(entry.get("model", "")).lower() for entry in models}
    pending_repos = {str(entry.get("model", "")).lower() for entry in pending}
    evaluation_repos = {str(entry.get("model", "")).lower() for entry in evaluation}

    def _cache_dirs_of(entries: list[dict]) -> set[str]:
        return {
            str(entry["cache_dir"]).lower()
            for entry in entries
            if entry.get("cache_dir")
        }

    allowed_dirs = _cache_dirs_of(models)
    evaluation_dirs = _cache_dirs_of(evaluation)

    # An evaluation run downloads weights this project is not licensed to deliver. That has
    # to be a FAILURE by default -- `make check` and CI must never go green with them on
    # disk -- and an explicit, per-invocation acknowledgement otherwise. A flag that has to
    # be typed is the difference between a deliberate evaluation and an accident.
    def _evaluation_finding(subject: str, location: Path) -> Finding:
        if allow_evaluation:
            return Finding(
                "WARN",
                subject,
                f"evaluation weights present at {location}. Acknowledged via "
                "--allow-evaluation: these are RAIL-M licensed and NOT deliverable. Delete "
                "them (`make clean-work`) before any run whose output leaves this machine.",
            )
        return Finding(
            "FAIL",
            subject,
            f"evaluation-only weights found at {location}. They are listed in models.yaml "
            "`evaluation_only`, which means their licence is known NOT to cover this "
            "deployment. If this is a deliberate local evaluation, run the gate with "
            "--allow-evaluation (or `make marker-gate`); CI must never pass with these "
            "present.",
        )

    for cache_dir in cache_dirs:
        if not cache_dir.is_dir():
            continue
        for child in sorted(cache_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue

            match = _HF_CACHE_DIR.match(child.name)
            if match:
                repo = f"{match.group('org')}/{match.group('name')}".lower()
                if repo in allowed_repos:
                    findings.append(Finding("OK", repo, f"cached at {child}"))
                elif repo in evaluation_repos:
                    findings.append(_evaluation_finding(repo, child))
                elif repo in pending_repos:
                    findings.append(
                        Finding(
                            "FAIL",
                            repo,
                            f"fetched (found at {child}) while still under review in "
                            "models.yaml `pending_review`. Its licence or base-weight "
                            "provenance is an open question -- settle that before using it.",
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            "FAIL",
                            repo,
                            f"model fetched at runtime but not in models.yaml (found at "
                            f"{child}). Nothing may download a model that has not been "
                            "cleared on licence and provenance.",
                        )
                    )
                continue

            if child.name.lower() in allowed_dirs:
                findings.append(Finding("OK", child.name, f"cached at {child}"))
            elif child.name.lower() in evaluation_dirs:
                findings.append(_evaluation_finding(child.name, child))
            else:
                findings.append(
                    Finding(
                        "FAIL",
                        child.name,
                        f"unrecognised model cache entry at {child}. Declare it in "
                        "models.yaml (with `cache_dir`) or remove it.",
                    )
                )

    return findings


#: Weight-file extensions. A file with one of these inside an installed package is a model
#: shipped as part of a wheel, which no cache scan will ever see.
WEIGHT_SUFFIXES = frozenset(
    {".onnx", ".pdmodel", ".pdiparams", ".safetensors", ".gguf", ".tflite", ".pt"}
)
#: Suffixes that are only *sometimes* weights, disambiguated by size:
#: `.bin` is used for all sorts of data, and `.pth` collides outright with Python's
#: path-configuration files, which sit in site-packages and are a few bytes long.
AMBIGUOUS_WEIGHT_SUFFIXES = frozenset({".bin", ".pth"})
BIN_WEIGHT_MIN_BYTES = 1_000_000


def site_packages_dirs() -> list[Path]:
    paths = {sysconfig.get_paths().get(key) for key in ("purelib", "platlib")}
    return [Path(path) for path in paths if path and Path(path).is_dir()]


def scan_installed_packages(
    allowed_packages: set[str], directories: list[Path] | None = None
) -> list[Finding]:
    """Fail on model weights shipped *inside* an installed package.

    This exists because of a real miss. `docling` is defined as `docling-slim[standard]`,
    which installs `rapidocr` whether or not any OCR is used -- and the rapidocr wheel bundles
    roughly 30MB of Baidu PaddleOCR weights (`PP-OCRv6_det`, `PP-OCRv6_rec`,
    `ch_ppocr_mobile`) as ordinary files on disk. They never touch a model cache, so a
    cache-scanning gate reports a clean run while banned weights sit in site-packages.

    Judging a dependency by its declared requirements is not enough; what landed on disk has
    to be looked at.
    """
    findings: list[Finding] = []
    for directory in directories or site_packages_dirs():
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            is_weight = suffix in WEIGHT_SUFFIXES or (
                suffix in AMBIGUOUS_WEIGHT_SUFFIXES
                and path.stat().st_size >= BIN_WEIGHT_MIN_BYTES
            )
            if not is_weight:
                continue

            relative = path.relative_to(directory)
            package = relative.parts[0] if relative.parts else path.name
            if package.lower() in allowed_packages:
                findings.append(
                    Finding("OK", package, f"allowlisted bundled weights: {relative}")
                )
                continue
            findings.append(
                Finding(
                    "FAIL",
                    package,
                    f"ships model weights inside the installed package "
                    f"({relative}, {path.stat().st_size // 1024}KB). Weights bundled in a "
                    "wheel bypass every cache-based check. Remove the dependency or narrow "
                    "its extras.",
                )
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-file", type=Path, default=DEFAULT_MODELS_FILE)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        action="append",
        default=None,
        help="Model cache to scan. Repeatable. Defaults to the HF and Docling caches.",
    )
    parser.add_argument("--skip-cache-scan", action="store_true")
    parser.add_argument(
        "--allow-evaluation",
        action="store_true",
        default=os.environ.get("MODEL_GATE_ALLOW_EVALUATION") == "1",
        help=(
            "Downgrade `evaluation_only` weights found in a cache from FAIL to WARN. For a "
            "deliberate local evaluation only -- never in CI, and never for a run whose "
            "output is delivered."
        ),
    )
    parser.add_argument(
        "--skip-package-scan",
        action="store_true",
        help="Skip scanning installed packages for bundled model weights.",
    )
    args = parser.parse_args(argv)

    models, pending, extra = load_models(args.models_file)
    evaluation = extra.get("evaluation_only") or []
    findings = check_allowlist(models) + check_pending(pending) + check_evaluation(evaluation)

    if not args.skip_package_scan:
        allowed_packages = {
            str(name).lower() for name in (extra.get("allowed_bundled_packages") or [])
        }
        findings += scan_installed_packages(allowed_packages)

    scanned: list[Path] = []
    if not args.skip_cache_scan:
        scanned = args.cache_dir if args.cache_dir else default_cache_dirs()
        findings += scan_caches(
            models, pending, scanned, evaluation, allow_evaluation=args.allow_evaluation
        )

    print("MODEL GATE (permissive licence AND non-Chinese base-weight provenance)")
    if not models:
        print(
            "  allowlist is empty: this phase is model-free for text, so no model may be "
            "fetched at all"
        )
    for finding in findings:
        print(f"  {finding.level:<6} {finding.subject}: {finding.detail}")

    # Always say which directories were inspected: "no models found" is only reassuring if
    # you know where the gate looked.
    if scanned:
        print("  scanned: " + ", ".join(str(path) for path in scanned))
    elif args.skip_cache_scan:
        print("  cache scan skipped")

    if args.allow_evaluation:
        print(
            "  NOTE: --allow-evaluation is set. Evaluation-only weights do not fail this "
            "run. Nothing produced under it may be delivered."
        )

    failures = [finding for finding in findings if finding.level == "FAIL"]
    if failures:
        print(f"\nFAILED: {len(failures)} violation(s).")
        return 1
    print("\nPASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
