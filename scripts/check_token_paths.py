"""Pre-flight for `make compose-up`: KB_TOKENS_DIR must be KB_TOKENS_FILE's parent.

The compose stack needs both, for one reason each. `KB_TOKENS_FILE` is the path `kb token`
and a host-venv `kb serve` read. `KB_TOKENS_DIR` is what gets bind-mounted into `kb-mcp`,
and it has to be the *directory* — a single-file bind mount binds the host file's inode,
and `kb token` replaces that inode on every write, so the container would lose the file
entirely (see `tests/test_compose_token_mount.py`).

Two settings that must agree is a footgun, so this checks them before Compose runs rather
than leaving a deployment where `kb token revoke` reports success and changes nothing the
server can see. That failure is silent and is exactly the one the revocation work exists to
prevent, so it is worth a few lines here.

Exits 0 when consistent or when neither is set (the legacy KB_TOKENS-only path, which
`kb serve` warns about on its own). Takes an optional path to the .env to check, which
defaults to the repo's own; `make compose-env-check` passes none.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from retrieval import config as config_module  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env: dict[str, str] = {}
    env_path = Path(argv[0]) if argv else REPO_ROOT / ".env"
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")

    token_file = env.get("KB_TOKENS_FILE", "")
    token_dir = env.get("KB_TOKENS_DIR", "")

    if not token_file and not token_dir:
        # Neither set: the container mounts the committed empty default and `kb serve`
        # refuses to start unless KB_TOKENS is set. Both are loud, so say nothing here.
        return 0

    if token_file and not token_dir:
        print(
            "KB_TOKENS_FILE is set but KB_TOKENS_DIR is not. The compose stack mounts the\n"
            "DIRECTORY, not the file, so kb-mcp would be given the committed empty default\n"
            f"and would not see your store. Add to .env:\n\n    KB_TOKENS_DIR={Path(token_file).parent}\n",
            file=sys.stderr,
        )
        return 2

    if token_dir and not token_file:
        print(
            "KB_TOKENS_DIR is set but KB_TOKENS_FILE is not, so `kb token` would write to\n"
            f"{config_module.DEFAULT_TOKENS_FILE}\nwhile kb-mcp reads {token_dir}/tokens.json "
            "— issuing and revoking would appear to\nwork and change nothing the server sees. "
            f"Add to .env:\n\n    KB_TOKENS_FILE={Path(token_dir) / 'tokens.json'}\n",
            file=sys.stderr,
        )
        return 2

    if not token_file.startswith("/") or not token_dir.startswith("/"):
        print(
            "KB_TOKENS_FILE and KB_TOKENS_DIR must both be ABSOLUTE paths for the compose\n"
            "stack: Compose resolves a relative bind-mount source against compose/, not the\n"
            "repo root.",
            file=sys.stderr,
        )
        return 2

    parent = Path(token_file).parent
    if parent != Path(token_dir):
        print(
            "KB_TOKENS_DIR must be KB_TOKENS_FILE's parent directory, or `kb token` writes\n"
            "somewhere kb-mcp is not reading — revocation would report success and have no\n"
            f"effect. Found:\n\n    KB_TOKENS_FILE={token_file}\n    KB_TOKENS_DIR={token_dir}\n"
            f"\nExpected KB_TOKENS_DIR={parent}\n",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
