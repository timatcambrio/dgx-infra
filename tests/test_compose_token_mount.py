"""How the compose stack hands `kb-mcp` its credential store.

This file exists for one reason, measured on 2026-10-01 rather than reasoned about: a
single-file bind mount binds the host file's **inode**, and `kb token issue|revoke` write
atomically — temp file, then `os.replace` — which installs a new one. With the file
mounted directly, the container does not merely go stale; the path disappears entirely
(`No such file or directory`), `tokens.read` treats that as "no records", and **every
file-backed token stops working on the first issue or revoke**. That is the exact outage
the revocation work removes, reintroduced by the mount.

Mounting the directory tracks the replacement correctly. These tests pin that down so a
later edit cannot quietly simplify it back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
BASE = REPO / "compose" / "docker-compose.yml"

CONTAINER_DIR = "/etc/kb"
CONTAINER_FILE = "/etc/kb/tokens.json"


def _kb_mcp() -> dict:
    return yaml.safe_load(BASE.read_text(encoding="utf-8"))["services"]["kb-mcp"]


def _token_mounts() -> list[str]:
    return [v for v in _kb_mcp()["volumes"] if CONTAINER_DIR in v]


def _split_mount(spec: str) -> tuple[str, str, list[str]]:
    """`source:target:flags`, split from the RIGHT — the source holds a `${VAR:-default}`
    whose own colon would break a left-to-right split."""
    rest, _, flags = spec.rpartition(":")
    source, _, target = rest.rpartition(":")
    return source, target, flags.split(",")


def test_the_token_store_is_mounted_as_a_directory() -> None:
    mounts = _token_mounts()
    assert len(mounts) == 1, mounts
    source, target, flags = _split_mount(mounts[0])
    assert target == CONTAINER_DIR, (
        f"mounted at {target!r}; mounting {CONTAINER_FILE!r} directly binds the host "
        "file's inode and breaks on the first `kb token` write -- see this module's docstring"
    )
    assert flags == ["ro"], "the server never writes the store; only `kb token` does"
    assert "KB_TOKENS_DIR" in source, source


def test_the_container_is_told_the_file_inside_that_directory() -> None:
    """The directory is what is mounted; KB_TOKENS_FILE still points at the file, because
    that is what `retrieval/config.py` reads."""
    assert _kb_mcp()["environment"]["KB_TOKENS_FILE"] == CONTAINER_FILE


def test_the_default_source_is_a_directory_that_exists() -> None:
    """A directory mount whose source does not exist fails the container at start, which
    is a worse failure than `kb serve`'s own "no usable credential" refusal. So the
    default has to be a real, empty, committed directory."""
    source, _, _ = _split_mount(_token_mounts()[0])
    default = source.split(":-")[1].rstrip("}")
    assert default.startswith("./"), default
    resolved = (BASE.parent / default).resolve()
    assert resolved.is_dir(), f"{resolved} must exist and be committed"
    assert not (resolved / "tokens.json").exists(), (
        f"{resolved}/tokens.json is committed; a real credential store must never be in git"
    )


def test_caddy_is_not_given_the_tokens() -> None:
    """`/kb/*` asks kb-mcp per request now; caddy holding its own copy of the token list
    is the stale-second-copy problem the revocation work removed."""
    caddy = yaml.safe_load(BASE.read_text(encoding="utf-8"))["services"]["caddy"]
    env = caddy.get("environment", {})
    assert "KB_TOKENS" not in env
    assert "KB_TOKENS_FILE" not in env
    assert "KB_TOKENS_DIR" not in env
    assert not [v for v in caddy.get("volumes", []) if "tokens" in v]


# --- the two settings cannot silently disagree ------------------------------------------
#
# `make compose-up` runs scripts/check_token_paths.py before Compose. The failure it
# prevents is the quiet one: `kb token revoke` writing to a file the server is not reading,
# reporting success, and changing nothing.

import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

CHECK = REPO / "scripts" / "check_token_paths.py"


def _check(tmp_path: Path, env_body: str) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / ".env"
    env_file.write_text(env_body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(CHECK), str(env_file)], capture_output=True, text=True
    )


def test_matching_paths_pass(tmp_path: Path) -> None:
    """Under tmp_path, not /srv/kb: the check now also looks at the permissions of the
    paths it is handed, so naming a real directory would make the result depend on what
    happens to exist on the machine running the tests."""
    store = tmp_path / "srv" / "kb"
    store.mkdir(parents=True)
    store.chmod(0o777)
    r = _check(tmp_path, f"KB_TOKENS_FILE={store}/tokens.json\nKB_TOKENS_DIR={store}\n")
    assert r.returncode == 0, r.stderr


def test_neither_set_passes(tmp_path: Path) -> None:
    """The legacy KB_TOKENS-only path. `kb serve` warns about that itself; this check is
    about the two new settings disagreeing, so it stays quiet."""
    r = _check(tmp_path, "KB_PATH=/srv/kb\nKB_TOKENS=abc\n")
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


def test_a_mismatched_directory_is_refused_and_names_the_fix(tmp_path: Path) -> None:
    """The silent failure this exists to prevent: `kb token revoke` writes to a file the
    server is not reading, reports success, and changes nothing."""
    r = _check(tmp_path, "KB_TOKENS_FILE=/srv/kb/tokens.json\nKB_TOKENS_DIR=/etc/kb\n")
    assert r.returncode == 2
    assert "KB_TOKENS_DIR=/srv/kb" in r.stderr


def test_the_file_without_the_directory_is_refused(tmp_path: Path) -> None:
    r = _check(tmp_path, "KB_TOKENS_FILE=/srv/kb/tokens.json\n")
    assert r.returncode == 2
    assert "KB_TOKENS_DIR=/srv/kb" in r.stderr


def test_the_directory_without_the_file_is_refused(tmp_path: Path) -> None:
    r = _check(tmp_path, "KB_TOKENS_DIR=/srv/kb\n")
    assert r.returncode == 2
    assert "KB_TOKENS_FILE=/srv/kb/tokens.json" in r.stderr


def test_relative_paths_are_refused(tmp_path: Path) -> None:
    """Compose resolves a relative bind-mount source against compose/, not the repo root —
    the same trap KB_PATH already has a check for."""
    r = _check(tmp_path, "KB_TOKENS_FILE=kb/tokens.json\nKB_TOKENS_DIR=kb\n")
    assert r.returncode == 2
    assert "ABSOLUTE" in r.stderr


def test_the_check_is_wired_into_compose_env_check() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    block = makefile.split("compose-env-check:")[1].split("\n\n")[0]
    assert "check_token_paths.py" in block, (
        "make compose-up must run the pre-flight, or a mismatched KB_TOKENS_FILE / "
        "KB_TOKENS_DIR reaches a deployment silently"
    )


def test_compose_up_depends_on_the_env_check() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    line = next(l for l in makefile.splitlines() if l.startswith("compose-up:"))
    assert "compose-env-check" in line


# --- issuing and revoking without a host venv --------------------------------------------
#
# The deployment is container-first: the DGX gets `docker compose` and nothing else, no
# host Python and no `uv`. Every documented way to manage a token was `uv run kb token
# ...`, so on that machine there was no way to issue the FIRST one — and the quickstart
# asked for one before `make compose-up`.
#
# A second, sharper reason, measured 2026-10-01: a store written by root on the host is
# mode 0600 root-owned, and the server runs as uid 10001, so it could not read the store
# at all and crash-looped. A store written by this service is created BY uid 10001, so it
# is readable by the server with no chown anywhere and without widening the mode.
#
# `kb-mcp`'s own mount stays read-only: the server never writes the store, and exactly one
# thing in the stack does.

TOOLS_SERVICE = "kb-token"


def _services() -> dict:
    return yaml.safe_load(BASE.read_text(encoding="utf-8"))["services"]


def _kb_token() -> dict:
    services = _services()
    assert TOOLS_SERVICE in services, (
        f"no {TOOLS_SERVICE!r} service: on a host with no Python there is then no way to "
        "issue the first token, and the quickstart asks for one before the stack is up"
    )
    return services[TOOLS_SERVICE]


def test_the_tools_service_mounts_the_store_writable() -> None:
    mounts = [v for v in _kb_token()["volumes"] if CONTAINER_DIR in v]
    assert len(mounts) == 1, mounts
    source, target, flags = _split_mount(mounts[0])
    assert target == CONTAINER_DIR, f"mounted at {target!r}; see this module's docstring"
    assert flags == ["rw"], (
        "spelled out rather than left to Compose's default, because the one difference "
        "from kb-mcp's mount is the whole point of this service"
    )
    assert "KB_TOKENS_DIR" in source, source


def test_the_tools_service_is_told_the_same_file_as_the_server() -> None:
    assert _kb_token()["environment"]["KB_TOKENS_FILE"] == CONTAINER_FILE


def test_the_server_still_cannot_write_the_store() -> None:
    """Adding a writer must not have widened the server's own mount."""
    _, _, flags = _split_mount(_token_mounts()[0])
    assert flags == ["ro"]


def test_the_tools_service_runs_as_the_same_user_as_the_server() -> None:
    """Both are built from compose/Dockerfile, which runs as uid 10001. That identity is
    what makes a container-issued store readable by the server, so neither may override
    `user:` — a `user: root` here would recreate the unreadable root-owned store."""
    assert _kb_token()["build"] == _kb_mcp()["build"]
    assert "user" not in _kb_token()
    assert "user" not in _kb_mcp()


def test_the_tools_service_does_not_need_the_database() -> None:
    """Tokens live in a file so that authentication survives Postgres being down. Managing
    them must not acquire that dependency through the back door — `kb token revoke` has to
    work during exactly the outage the file is there for."""
    svc = _kb_token()
    assert "depends_on" not in svc, svc.get("depends_on")


def test_the_tools_service_is_not_started_by_compose_up() -> None:
    """`make compose-up` runs the `prod` profile. A one-off command must not be a
    long-running service in it."""
    assert _kb_token()["profiles"] == ["tools"]
    assert "restart" not in _kb_token()


def test_make_token_runs_the_tools_service() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "\ntoken:" in makefile, "no `make token` target"
    block = makefile.split("\ntoken:")[1].split("\n\n")[0]
    assert TOOLS_SERVICE in block
    assert "--profile tools" in block
    assert "run --rm" in block


def test_make_token_preflights_the_paths() -> None:
    """Issuing into the wrong directory is the silent failure this module's pre-flight
    exists for, and `make token` is now the first command that can commit it."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    line = next(l for l in makefile.splitlines() if l.startswith("token:"))
    assert "compose-env-check" in line


# --- the pre-flight checks that the server can actually read what it is given -------------


SERVER_UID = 10001


def test_the_dockerfile_still_runs_as_the_uid_the_pre_flight_assumes() -> None:
    """`scripts/check_token_paths.py` reports a remedy naming a uid. If the image's uid
    changes and that number does not, the remedy is wrong and the deployment stays
    broken."""
    text = (REPO / "compose" / "Dockerfile").read_text(encoding="utf-8")
    assert f"--uid {SERVER_UID}" in text
    check = (REPO / "scripts" / "check_token_paths.py").read_text(encoding="utf-8")
    assert str(SERVER_UID) in check


def _check_with_store(tmp_path: Path, mode: int) -> subprocess.CompletedProcess[str]:
    store = tmp_path / "store"
    store.mkdir()
    (store / "tokens.json").write_text("{}\n", encoding="utf-8")
    (store / "tokens.json").chmod(mode)
    store.chmod(0o777)
    return _check(
        tmp_path,
        f"KB_TOKENS_FILE={store}/tokens.json\nKB_TOKENS_DIR={store}\n",
    )


@pytest.mark.skipif(
    os.geteuid() == SERVER_UID, reason="the test process IS the server uid here"
)
def test_a_store_the_server_cannot_read_is_refused(tmp_path: Path) -> None:
    """The crash loop, caught before Compose starts anything. `kb serve` exits 2 on an
    unreadable store, so without this the symptom is a restarting container and a 502."""
    r = _check_with_store(tmp_path, 0o600)
    assert r.returncode == 2, r.stdout + r.stderr
    assert str(SERVER_UID) in r.stderr
    assert "make token" in r.stderr, "the remedy has to name the command that avoids it"


def test_a_store_the_server_can_read_passes(tmp_path: Path) -> None:
    r = _check_with_store(tmp_path, 0o644)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_directory_the_tools_service_cannot_write_is_refused(tmp_path: Path) -> None:
    """`make token` writes as uid 10001, so it needs the directory, not just the file:
    `write` creates a temp file beside the store and renames it over the top."""
    store = tmp_path / "store"
    store.mkdir()
    store.chmod(0o755)
    r = _check(tmp_path, f"KB_TOKENS_FILE={store}/tokens.json\nKB_TOKENS_DIR={store}\n")
    assert r.returncode == 2, r.stdout + r.stderr
    assert str(SERVER_UID) in r.stderr


def test_a_store_that_does_not_exist_yet_passes(tmp_path: Path) -> None:
    """Nothing is wrong with a deployment that has not issued its first token yet, as long
    as the directory can be written. `kb serve` refuses to start on its own, by name."""
    store = tmp_path / "store"
    store.mkdir()
    store.chmod(0o777)
    r = _check(tmp_path, f"KB_TOKENS_FILE={store}/tokens.json\nKB_TOKENS_DIR={store}\n")
    assert r.returncode == 0, r.stdout + r.stderr
