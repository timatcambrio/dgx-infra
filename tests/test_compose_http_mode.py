"""Plain HTTP (`KB_TLS=off`) for a client that does not want certificates at all.

Requested 2026-10-11: the client's admins regard the DGX's network and VPN as the
boundary and do not want every user to trust a certificate. Stages 1-3 of the Codex test
already ran over plain HTTP across a LAN, so the client side is known to work; what this
pins down is the server side, where the switch is easy to get half right.

Each setting below has to agree with the others, and each mismatch fails somewhere that
does not name the cause:

- `KB_TLS=off` with `TLS_CERT` still set: Caddy is told to use a certificate on a site it
  serves over HTTP, and refuses to start. The front door just stops answering.
- Caddy serving on 80 while Compose publishes 443 (or the reverse): the TCP connection is
  refused and nothing in any log says why.

So the mode is one setting, the port is derived from it rather than set separately, and
`make compose-up` refuses each of the mismatches above before Compose runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "scripts" / "check_tls_mode.py"
ENTRYPOINT = REPO / "compose" / "caddy-entrypoint.sh"
CADDYFILE = REPO / "compose" / "Caddyfile"
COMPOSE = REPO / "compose" / "docker-compose.yml"
MAKEFILE = REPO / "Makefile"


def _check(tmp_path: Path, env_text: str, *args: str) -> subprocess.CompletedProcess:
    env_file = tmp_path / ".env"
    env_file.write_text(env_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(CHECK), *args, str(env_file)],
        capture_output=True, text=True, check=False,
    )


# --- the pre-flight ---------------------------------------------------------------------


@pytest.mark.parametrize("env_text", [
    "",
    "KB_TLS=on\n",
    "KB_TLS=on\nTLS_CERT=/c.pem\nTLS_KEY=/k.pem\n",
    "KB_TLS=off\n",
    'KB_TLS="off"\n',
])
def test_consistent_settings_pass(tmp_path: Path, env_text: str) -> None:
    got = _check(tmp_path, env_text)
    assert got.returncode == 0, got.stderr


@pytest.mark.parametrize("env_text, names", [
    ("KB_TLS=off\nTLS_CERT=/c.pem\nTLS_KEY=/k.pem\n", "TLS_CERT"),
    ("KB_TLS=no\n", "KB_TLS"),
])
def test_each_mismatch_is_refused_by_name(tmp_path: Path, env_text: str, names: str) -> None:
    got = _check(tmp_path, env_text)
    assert got.returncode == 2
    assert names in got.stderr, got.stderr


@pytest.mark.parametrize("env_text, port", [
    ("", "443"),
    ("KB_TLS=on\n", "443"),
    ("KB_TLS=off\n", "80"),
])
def test_the_port_is_derived_from_the_mode(tmp_path: Path, env_text: str, port: str) -> None:
    got = _check(tmp_path, env_text, "--port")
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == port


def test_a_missing_env_file_means_https(tmp_path: Path) -> None:
    """`make help` and every non-compose target expand nothing from this, but a compose
    target run before `.env` exists must still get a port Compose can parse; the missing
    file is then reported by compose-env-check, in its own words."""
    got = subprocess.run(
        [sys.executable, str(CHECK), "--port", str(tmp_path / "absent.env")],
        capture_output=True, text=True, check=False,
    )
    assert got.stdout.strip() == "443"


# --- the Caddyfile the entrypoint actually hands Caddy ----------------------------------


def _render(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess, str]:
    """Run the real entrypoint with a stand-in `caddy` that does nothing, and return what
    it wrote. The entrypoint reads and writes fixed container paths unless told otherwise,
    which is the only reason the two KB_CADDYFILE_* overrides exist."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "caddy"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    out = tmp_path / "Caddyfile.rendered"
    run_env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "KB_CADDYFILE_SRC": str(CADDYFILE),
        "KB_CADDYFILE_OUT": str(out),
        **env,
    }
    got = subprocess.run(["sh", str(ENTRYPOINT)], env=run_env,
                         capture_output=True, text=True, check=False)
    return got, out.read_text(encoding="utf-8") if out.exists() else ""


def _unfilled(rendered: str) -> list[str]:
    """Directive lines still carrying a `__NAME__` placeholder. Comments are skipped: the
    Caddyfile's own header names the placeholders while explaining them."""
    return [l for l in rendered.splitlines()
            if "__" in l and not l.lstrip().startswith("#")]


def _site_line(rendered: str) -> str:
    return next(l for l in rendered.splitlines()
                if "{$KB_PUBLIC_HOST:localhost}" in l and l.rstrip().endswith("{"))


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_https_is_still_the_default(tmp_path: Path) -> None:
    got, rendered = _render(tmp_path)
    assert got.returncode == 0, got.stderr
    assert "\ttls internal" in rendered
    assert "http://" not in _site_line(rendered)
    assert not _unfilled(rendered), "a placeholder was left unfilled"


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_http_mode_serves_every_name_over_http_with_no_tls_line(tmp_path: Path) -> None:
    """Caddy refuses a `tls` directive inside a site it serves over HTTP, so the line has
    to go, not merely be emptied. Every name gets the scheme: one left bare would be
    served over HTTPS on 443, which Compose does not publish in this mode."""
    got, rendered = _render(tmp_path, KB_TLS="off")
    assert got.returncode == 0, got.stderr
    assert not any(l.strip().startswith("tls") for l in rendered.splitlines())
    names = [n.strip() for n in _site_line(rendered).rstrip().rstrip("{").split(",")]
    assert names and all(n.startswith("http://") for n in names), names
    assert not _unfilled(rendered), "a placeholder was left unfilled"


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_http_mode_with_a_certificate_refuses_to_start(tmp_path: Path) -> None:
    got, _ = _render(tmp_path, KB_TLS="off", TLS_CERT="/c.pem", TLS_KEY="/k.pem")
    assert got.returncode != 0
    assert "TLS_CERT" in got.stderr


# --- the port Compose publishes ---------------------------------------------------------


def test_compose_publishes_the_derived_port() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert '"${KB_LISTEN_PORT:-443}:${KB_LISTEN_PORT:-443}"' in text
    assert '"443:443"' not in text


def test_the_makefile_derives_the_port_and_runs_the_check() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "scripts/check_tls_mode.py --port" in text
    assert "KB_LISTEN_PORT=$(KB_LISTEN_PORT) docker compose" in text
    env_check = text.split("compose-env-check:")[1].split("\n\n")[0]
    assert "scripts/check_tls_mode.py" in env_check
