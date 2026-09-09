#!/usr/bin/env python3
"""License gate — classifies dependencies by **call style**, not by licence string.

The policy this enforces: obligations attach to *conveying* software, not to using it, and
the risk that matters is linking. So

* copyleft invoked as a **subprocess** is fine (LibreOffice) — separate program, not a
  derivative work;
* copyleft **imported as a library** is banned, because the conservative reading of linking
  makes our own module a derivative work, and "the client downloads it themselves" does not
  cure that;
* **PyMuPDF / pymupdf4llm (AGPL) are import-only and therefore permanently banned**, checked
  by name and not merely by metadata, because the ban must hold whether or not the package
  happens to be installed on the machine running the gate.

Two independent checks, because either alone has a blind spot:

1. **Imports under `pipeline/`** are read from the AST. This catches `import fitz` even
   though PyMuPDF is not installed, which metadata inspection cannot.
2. **The installed dependency closure** is classified from dist-info metadata. This catches a
   strong-copyleft package pulled in transitively, which would be linked into our process at
   runtime even though nothing under `pipeline/` names it.

Both fail closed. Exceptions live in `license_allowlist.yaml` with a written justification
and an explicit declared call style.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PIPELINE_DIR = REPO_ROOT / "pipeline"
DEFAULT_ALLOWLIST = Path(__file__).resolve().parent / "license_allowlist.yaml"

#: Banned by name regardless of installation state or metadata. AGPL and import-only.
BANNED_IMPORT_MODULES = frozenset({"fitz", "fitz_old", "pymupdf", "pymupdf4llm"})

STRONG_COPYLEFT = "strong-copyleft"
WEAK_COPYLEFT = "weak-copyleft"
PERMISSIVE = "permissive"
UNKNOWN = "unknown"

#: "GPL-compatible" is a statement *about* compatibility, not a copyleft grant. Left in, it
#: misclassifies half of PyPI -- the Python-2.0 licence text says exactly this.
_COMPATIBILITY_NOISE = re.compile(r"gpl[- ]compatible", re.IGNORECASE)

_AGPL = re.compile(r"\bagpl\b|affero", re.IGNORECASE)
_LGPL = re.compile(r"\blgpl\b|lesser\s+general\s+public", re.IGNORECASE)
_GPL = re.compile(r"\bgpl\b|gnu\s+general\s+public", re.IGNORECASE)
_MPL = re.compile(r"\bmpl\b|mozilla\s+public", re.IGNORECASE)
_EPL = re.compile(r"\bepl\b|eclipse\s+public", re.IGNORECASE)


@dataclass
class Finding:
    level: str
    subject: str
    detail: str


def classify_license(text: str) -> str:
    """Classify a licence string. Order matters: LGPL and AGPL both contain 'GPL'."""
    if not text:
        return UNKNOWN
    cleaned = _COMPATIBILITY_NOISE.sub("", text)
    if _AGPL.search(cleaned):
        return STRONG_COPYLEFT
    if _LGPL.search(cleaned):
        return WEAK_COPYLEFT
    if _GPL.search(cleaned):
        return STRONG_COPYLEFT
    if _MPL.search(cleaned) or _EPL.search(cleaned):
        return WEAK_COPYLEFT
    return PERMISSIVE


def license_text_for(dist: metadata.Distribution) -> str:
    """Best available licence signal: classifiers, then SPDX expression, then free text.

    Classifiers come first because they are a controlled vocabulary; the `License` field is
    frequently a package's entire licence text or something like "see LICENSE".
    """
    meta = dist.metadata
    classifiers = [
        value
        for value in meta.get_all("Classifier") or []
        if value.startswith("License ::")
    ]
    parts = classifiers + [
        meta.get("License-Expression") or "",
        (meta.get("License") or "")[:400],
    ]
    return " | ".join(part for part in parts if part)


def imported_modules(pipeline_dir: Path) -> dict[str, set[Path]]:
    """Top-level module names imported anywhere under `pipeline_dir`, with their sources."""
    found: dict[str, set[Path]] = {}
    for path in sorted(pipeline_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise SystemExit(f"license_gate: cannot parse {path}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative import: our own package, never a dependency.
                names = [node.module.split(".")[0]] if node.module and not node.level else []
            else:
                continue
            for name in names:
                found.setdefault(name, set()).add(path)
    return found


def load_allowlist(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("allowed") or []
    return {entry["package"].lower().replace("_", "-"): entry for entry in entries}


def installed_distributions() -> dict[str, tuple[str, str]]:
    """`normalised name -> (version, classification)` for everything installed."""
    result: dict[str, tuple[str, str]] = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if not name:
            continue
        key = name.lower().replace("_", "-")
        result[key] = (dist.version or "?", classify_license(license_text_for(dist)))
    return result


def run(pipeline_dir: Path, allowlist_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    allowlist = load_allowlist(allowlist_path)
    imports = imported_modules(pipeline_dir)

    # Check 1: banned by name, whether installed or not.
    for module in sorted(BANNED_IMPORT_MODULES & set(imports)):
        where = ", ".join(
            str(path.relative_to(pipeline_dir.parent)) for path in sorted(imports[module])
        )
        findings.append(
            Finding(
                "FAIL",
                module,
                f"banned AGPL import-only module imported in {where}. PyMuPDF is never "
                "usable here, including for a quick check.",
            )
        )

    installed = installed_distributions()
    module_to_dists = metadata.packages_distributions()

    # Check 2: strong copyleft named in an import under pipeline/.
    for module in sorted(imports):
        for dist_name in module_to_dists.get(module, []):
            key = dist_name.lower().replace("_", "-")
            version, classification = installed.get(key, ("?", UNKNOWN))
            if classification != STRONG_COPYLEFT:
                continue
            if key in allowlist:
                findings.append(
                    Finding(
                        "ALLOWED",
                        f"{dist_name} {version}",
                        f"imported as {module}; allowlisted: {allowlist[key].get('justification', 'no justification given')}",
                    )
                )
                continue
            findings.append(
                Finding(
                    "FAIL",
                    f"{dist_name} {version}",
                    f"strong copyleft imported as `{module}` under pipeline/. Copyleft is "
                    "usable only as a subprocess.",
                )
            )

    # Check 3: strong copyleft anywhere in the installed closure. Even if pipeline/ never
    # names it, a dependency importing it links it into this process.
    imported_dists = {
        dist.lower().replace("_", "-")
        for module in imports
        for dist in module_to_dists.get(module, [])
    }
    for key, (version, classification) in sorted(installed.items()):
        if classification != STRONG_COPYLEFT or key in imported_dists:
            continue
        if key in allowlist:
            entry = allowlist[key]
            findings.append(
                Finding(
                    "ALLOWED",
                    f"{key} {version}",
                    f"call style '{entry.get('call_style', 'unspecified')}': "
                    f"{entry.get('justification', 'no justification given')}",
                )
            )
            continue
        findings.append(
            Finding(
                "FAIL",
                f"{key} {version}",
                "strong copyleft present in the installed dependency closure. If it is "
                "reached only as a subprocess, say so in license_allowlist.yaml with a "
                "justification; otherwise remove it.",
            )
        )

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline-dir", type=Path, default=DEFAULT_PIPELINE_DIR)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument(
        "--quiet", action="store_true", help="Print only failures and the verdict."
    )
    args = parser.parse_args(argv)

    if not args.pipeline_dir.is_dir():
        print(f"license_gate: no such directory: {args.pipeline_dir}", file=sys.stderr)
        return 1

    findings = run(args.pipeline_dir, args.allowlist)
    failures = [finding for finding in findings if finding.level == "FAIL"]

    print("LICENSE GATE (call style, not licence string)")
    if not args.quiet:
        for finding in findings:
            if finding.level != "FAIL":
                print(f"  {finding.level:<8} {finding.subject}: {finding.detail}")
    for finding in failures:
        print(f"  FAIL     {finding.subject}: {finding.detail}")

    if failures:
        print(f"\nFAILED: {len(failures)} violation(s).")
        return 1
    print("  no copyleft imports under pipeline/, closure clean")
    print("\nPASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
