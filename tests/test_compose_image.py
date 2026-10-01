"""What the `kb-mcp` image needs from the network at container start: nothing.

Measured 2026-10-01 on a fresh host, by taking the network away:

    $ docker run --rm --network none compose-kb-mcp kb token list
    cause: Failed to fetch: `https://files.pythonhosted.org/.../packaging-26.3-py3-none-any.whl`
    cause: dns error
    hint: `packaging` (v26.3) was included because `dgx-pipeline:dev` (v0.1.0) depends on
          `pytest` (v8.4.2) which depends on `packaging`

The image is built with `uv sync --extra serve --frozen --no-dev`, but the ENTRYPOINT was a
plain `uv run`, and `uv run` SYNCS THE PROJECT AGAIN — with none of those flags. So every
container start re-resolved the dependency set, added the `dev` group on top of the image
(`pytest`, `reportlab`, and their own dependencies), and fetched it from pypi, because
UV_CACHE_DIR is under /tmp and nothing survives between containers. With egress it was
slow and put fixture-generation libraries into a production image. Without egress the
container did not start at all.

The deployment target is an on-prem federal machine. It does not get to call pypi to start
a server, and `--no-dev` at build time means nothing if the entrypoint undoes it.

`--no-sync` runs the command against the environment the build already installed: the one
the lockfile pinned and the build validated.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "compose" / "Dockerfile"


def _entrypoint() -> str:
    line = next(
        l for l in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if l.startswith("ENTRYPOINT")
    )
    return line


def test_the_entrypoint_does_not_sync_at_container_start() -> None:
    assert "--no-sync" in _entrypoint(), (
        "a bare `uv run` re-syncs the project at every start, which needs pypi — see this "
        "module's docstring"
    )


def test_the_entrypoint_still_only_names_uv_run() -> None:
    """`docker compose run --rm kb-mcp kb index` works by APPENDING to the entrypoint, so
    the entrypoint must not name `kb` itself — the comment above it in the Dockerfile says
    so, and `--no-sync` must not be the edit that breaks it."""
    entrypoint = _entrypoint()
    assert '"kb"' not in entrypoint, entrypoint
    assert entrypoint.count('"') == 6, f"expected exactly uv, run, --no-sync: {entrypoint}"


def test_the_build_still_installs_without_the_dev_group() -> None:
    """The two have to agree: `--no-sync` keeps whatever the build installed, so the build
    is now the only thing deciding what is in the image."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    build = next(l for l in text.splitlines() if "uv sync" in l)
    assert "--no-dev" in build, build
    assert "--frozen" in build, build
    assert "--extra serve" in build, build
