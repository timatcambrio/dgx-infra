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

It also checks that the server can actually READ what it is given, which is the second
way this mount goes wrong and the one that takes the whole stack down. Measured 2026-10-01
on a fresh host: `kb token issue` run by root on the host writes the store mode 0600
root-owned, `kb-mcp` runs as uid 10001, and so the server could not read a single record.
`kb serve` exits 2 on an unreadable store, the container crash-loops, and the only symptom
at the front door is Caddy answering 502 — nothing anywhere names the permission. The same
applies to the DIRECTORY, which `make token` has to write a temp file into.

Exits 0 when consistent or when neither is set (the legacy KB_TOKENS-only path, which
`kb serve` warns about on its own). Takes an optional path to the .env to check, which
defaults to the repo's own; `make compose-env-check` passes none.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from retrieval import config as config_module  # noqa: E402



#: The uid and gid `compose/Dockerfile` creates and runs as (`useradd --uid 10001`), for
#: both `kb-mcp` and the `kb-token` one-off. Hardcoded because the remedies below have to
#: name a number; `tests/test_compose_token_mount.py` asserts the Dockerfile still agrees.
SERVER_UID = 10001
SERVER_GID = 10001

#: Permission bits per class, in the order POSIX consults them: owner, group, other.
_BITS = {
    "r": (stat.S_IRUSR, stat.S_IRGRP, stat.S_IROTH),
    "w": (stat.S_IWUSR, stat.S_IWGRP, stat.S_IWOTH),
    "x": (stat.S_IXUSR, stat.S_IXGRP, stat.S_IXOTH),
}


def _allows(info: "os.stat_result", want: str) -> bool:
    """Whether SERVER_UID/SERVER_GID holds every bit in `want` on `info`.

    POSIX consults exactly one class — owner if the uid matches, else group if the gid
    does, else other — so this is not a union of the three.
    """
    if info.st_uid == SERVER_UID:
        which = 0
    elif info.st_gid == SERVER_GID:
        which = 1
    else:
        which = 2
    return all(info.st_mode & _BITS[bit][which] for bit in want)


def _stat(path: Path) -> "tuple[os.stat_result | None, bool]":
    """`(info, denied)` for `path`: the stat, or None with whether we were refused.

    `Path.exists()` cannot be used for this. `_ignore_error` swallows ENOENT and ENOTDIR
    and never EACCES, so on the setup this script RECOMMENDS -- a 0700 directory owned by
    SERVER_UID -- it raises in the face of the login user instead of answering. Measured
    2026-10-05 on a fresh host; see tests/test_compose_token_mount.py.

    Absent and unreadable are different answers and the caller treats them differently,
    so they are reported separately rather than both collapsing to False.
    """
    try:
        return path.stat(), False
    except (FileNotFoundError, NotADirectoryError):
        return None, False
    except PermissionError:
        return None, True


def _setup_hint(token_dir: str) -> str:
    return (
        "The container-first way to avoid this entirely is to let the stack write the\n"
        "store itself, as the uid that has to read it:\n\n"
        f"    sudo install -d -o {SERVER_UID} -g {SERVER_GID} -m 700 {token_dir}\n"
        '    make token ARGS="issue you@example.com"\n\n'
        "`kb token` preserves an existing store's owner and mode on every later write, so\n"
        "this is a one-time step and a later `kb token` run on the host cannot undo it.\n"
    )


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

    # Only what exists is checked. A deployment that has not issued its first token yet is
    # not broken -- `kb serve` refuses to start on its own and names `kb token issue` -- and
    # a directory Compose has not been pointed at yet is not this script's business.
    directory = Path(token_dir)
    dir_info, dir_denied = _stat(directory)
    if dir_denied:
        print(
            f"cannot inspect {token_dir}: permission denied as uid {os.geteuid()}. Its own\n"
            "parent directory does not allow this user to look, so nothing here can be\n"
            f"verified -- including whether uid {SERVER_UID} can write the store.\n\n"
            f"Check it with:\n\n    sudo {sys.executable} {__file__}\n",
            file=sys.stderr,
        )
        return 2
    if dir_info is not None and stat.S_ISDIR(dir_info.st_mode) and not _allows(dir_info, "wx"):
        print(
            f"{token_dir} is not writable by uid {SERVER_UID}, which is what `make token`\n"
            "runs as. `kb token` writes a temp file beside the store and renames it over\n"
            "the top, so it needs the directory, not just the file.\n\n"
            + _setup_hint(token_dir),
            file=sys.stderr,
        )
        return 2

    store = Path(token_file)
    info, denied = _stat(store)
    if denied:
        # Not a fault, and specifically safe to pass. The directory check above has
        # already run -- `stat` on a directory needs only `+x` on its PARENT, so it is
        # reachable even when the directory itself shuts us out -- and it is what pins
        # the thing that matters: that SERVER_UID can write here. A store we cannot see
        # inside a directory SERVER_UID owns is the documented setup working, not a
        # misconfiguration. The root-owned store that took the stack down on 2026-10-01
        # is still refused, by that directory check, without root.
        print(
            f"note: {token_file} could not be inspected as uid {os.geteuid()} -- "
            f"{token_dir}\nbelongs to another uid and does not let this user in. That is "
            "the setup this\nscript recommends, and the directory itself checked out, so "
            "this is not an error.\n\n"
            f"To verify the store's own mode and owner:\n\n    sudo {sys.executable} {__file__}\n",
            file=sys.stderr,
        )
        return 0
    if info is not None and not _allows(info, "r"):
        print(
            f"{token_file} is not readable by uid {SERVER_UID}, which is the uid kb-mcp\n"
            f"runs as (it is {stat.S_IMODE(info.st_mode):04o} owned by "
            f"{info.st_uid}:{info.st_gid}). `kb serve` exits 2 on a store it cannot read,\n"
            "so the container would crash-loop and Caddy would answer 502 without anything\n"
            "naming the permission.\n\n"
            f"Hand this store over:\n\n    sudo chown {SERVER_UID}:{SERVER_GID} {token_file}\n\n"
            + _setup_hint(token_dir),
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
