"""The container-first make targets must not need `uv` on the host.

Measured 2026-10-05 on a fresh AWS GPU host, at step 6 of the deployment runbook:

    $ make token ARGS="issue me@example.com"
    make: uv: No such file or directory

`make token` exists precisely because the deployment target has `docker compose` and
nothing else -- every documented path to a token used to be `uv run kb token ...`, which
is unavailable there. But `token`, `compose-up` and `compose-index` all depend on
`compose-env-check`, whose last line ran `$(PYTHON) scripts/check_token_paths.py` with
`PYTHON := $(UV) run python`. So the whole container-first path still required a host
`uv`, and the three targets a bare host needs most were the three that could not run.

The dependency was accidental, not needed: `scripts/check_token_paths.py` imports only
the standard library plus `retrieval.config`, which is itself stdlib-only, so the system
`python3` runs it. Pinning that here because the failure is invisible on any developer
machine -- `uv` is always on PATH there, which is why this shipped twice.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAKEFILE = REPO / "Makefile"

#: Targets a host with Docker and nothing else must be able to run.
CONTAINER_FIRST_TARGETS = ("compose-env-check", "token", "compose-up", "compose-index", "gpu-check")


def _recipe(target: str) -> str:
    """The recipe lines (tab-indented) of one target."""
    text = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n|\n(?=\t))*)", text, re.M)
    assert match, f"no recipe found for {target!r}; this test reads the Makefile directly"
    return match.group(1)


def test_no_container_first_target_invokes_uv() -> None:
    for target in CONTAINER_FIRST_TARGETS:
        recipe = _recipe(target)
        assert "$(UV)" not in recipe and "$(PYTHON)" not in recipe and "uv run" not in recipe, (
            f"`make {target}` invokes uv, which the deployment host does not have -- see "
            f"this module's docstring. Use $(PREFLIGHT_PYTHON) for a stdlib-only script."
        )


def test_the_preflight_interpreter_is_not_uv() -> None:
    """The variable the pre-flight uses must default to a system interpreter. Pointing it
    back at `uv` would restore the defect while leaving the test above passing."""
    text = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(r"^PREFLIGHT_PYTHON\s*\?=\s*(.+)$", text, re.M)
    assert match, "Makefile no longer defines PREFLIGHT_PYTHON"
    assert "uv" not in match.group(1), match.group(1)


def test_the_preflight_script_stays_stdlib_only() -> None:
    """What makes the fix valid. A third-party import here would mean the check needs a
    resolved environment after all, and the system python3 would fail at import time --
    on the deployment host, inside a `make compose-up` that used to work."""
    source = (REPO / "scripts" / "check_token_paths.py").read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import)\s+([A-Za-z_][\w.]*)", source, re.M)
    allowed = {"__future__", "os", "stat", "sys", "pathlib", "retrieval"}
    assert set(imports) <= allowed, f"unexpected imports: {set(imports) - allowed}"
