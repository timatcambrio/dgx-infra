"""`scripts/make_tls_cert.sh` must never leave a certificate and key that do not match.

Measured 2026-10-13 against the shipped script, after a good first run: re-running it
with `https://<ip>` (or `http://<ip>`) as the host made openssl write a NEW key.pem, then
reject the name and write no certificate. The script exited 1 having printed only its
`subjectAltName = ...` line, because openssl's stderr went to /dev/null. The old cert.pem
stayed beside the new key; Caddy then logs `private key does not match public key` at
every start and restarts forever, so port 443 refuses connections and nothing says why.

The same silent exit happened when `tls/cert.pem` was a directory, which is what Docker
creates when the stack is started with TLS_CERT set before the certificate exists.

These tests drive the script in a scratch copy of the repo layout, so the repo's own
`tls/` is never touched.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "make_tls_cert.sh"

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="no openssl")


def _layout(tmp_path: Path) -> Path:
    """A scratch repo root holding only the script, where `tls/` is written."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "make_tls_cert.sh")
    return tmp_path


def _run(root: Path, *args: str, path: str | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": path or "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    return subprocess.run(
        ["sh", str(root / "scripts" / "make_tls_cert.sh"), *args],
        capture_output=True, text=True, env=env, cwd=str(root),
    )


def _existing_pair(root: Path) -> tuple[bytes, bytes]:
    tls = root / "tls"
    tls.mkdir(exist_ok=True)
    (tls / "cert.pem").write_bytes(b"OLD CERT\n")
    (tls / "key.pem").write_bytes(b"OLD KEY\n")
    return b"OLD CERT\n", b"OLD KEY\n"


def _pubkey(*cmd: str) -> str:
    return subprocess.run(["openssl", *cmd, "-pubout"] if cmd[0] == "pkey" else
                          ["openssl", *cmd, "-noout", "-pubkey"],
                          capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("bad", [
    "https://10.10.150.34",
    "http://10.10.150.34",
    "10.10.150.34/",
    "10.10.150.34:443",
    "dgx 01",
])
def test_a_host_with_a_scheme_port_or_path_is_refused_before_anything_is_written(tmp_path, bad):
    """The value that produced the mismatched pair on 2026-10-13 was `https://<ip>`."""
    root = _layout(tmp_path)
    cert, key = _existing_pair(root)
    r = _run(root, bad, "10.10.150.34")
    assert r.returncode == 2, r.stderr
    assert "bare host name or IP" in r.stderr
    assert (root / "tls" / "cert.pem").read_bytes() == cert
    assert (root / "tls" / "key.pem").read_bytes() == key


def test_an_extra_name_with_a_scheme_is_refused_too(tmp_path):
    root = _layout(tmp_path)
    cert, key = _existing_pair(root)
    r = _run(root, "10.10.150.34", "https://10.10.150.34")
    assert r.returncode == 2, r.stderr
    assert (root / "tls" / "key.pem").read_bytes() == key


def test_an_openssl_failure_keeps_the_old_pair_and_says_why(tmp_path):
    """openssl writes the key before it fails on the certificate. Whatever it manages to
    write must not replace a working pair, and its own message must reach the operator."""
    root = _layout(tmp_path)
    cert, key = _existing_pair(root)
    fake = tmp_path / "bin"
    fake.mkdir()
    # Writes whatever -keyout names, then fails the way openssl did on 2026-10-13.
    (fake / "openssl").write_text(
        '#!/bin/sh\n'
        'while [ $# -gt 0 ]; do [ "$1" = -keyout ] && echo NEW KEY > "$2"; shift; done\n'
        'echo "Error adding extensions defined via -addext" >&2\n'
        'exit 1\n', encoding="utf-8")
    (fake / "openssl").chmod(0o755)
    r = _run(root, "10.10.150.34", path=f"{fake}:/usr/bin:/bin")
    assert r.returncode != 0
    assert "Error adding extensions" in r.stderr
    assert (root / "tls" / "cert.pem").read_bytes() == cert
    assert (root / "tls" / "key.pem").read_bytes() == key
    assert sorted(p.name for p in (root / "tls").iterdir()) == ["cert.pem", "key.pem"]


def test_a_directory_where_the_certificate_belongs_is_named_not_failed_silently(tmp_path):
    root = _layout(tmp_path)
    (root / "tls" / "cert.pem").mkdir(parents=True)
    r = _run(root, "10.10.150.34")
    assert r.returncode == 2, r.stderr
    assert "is a directory" in r.stderr
    assert "Docker" in r.stderr


@needs_openssl
def test_a_good_run_writes_a_matching_pair_over_an_old_one(tmp_path):
    root = _layout(tmp_path)
    _existing_pair(root)
    r = _run(root, "10.10.150.34", "10.10.150.34")
    assert r.returncode == 0, r.stderr
    cert, key = root / "tls" / "cert.pem", root / "tls" / "key.pem"
    assert _pubkey("x509", "-in", str(cert)) == _pubkey("pkey", "-in", str(key))
    assert key.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in (root / "tls").iterdir()) == ["cert.pem", "key.pem"]
    assert f"TLS_CERT={cert}" in r.stdout
