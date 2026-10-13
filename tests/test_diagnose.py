"""`scripts/diagnose.sh` is sent to a client to run on a host nobody else can reach, and its
whole output is sent back. So it must name the faults that have actually stopped a
deployment, and it must never print a secret.

On 2026-10-13 a client's proxy restarted in a loop, and with no access to the host every
fact needed a separate round trip. The faults below are the ones measured that day to stop
the proxy: a certificate and key that do not match, a directory Docker created where the
certificate belongs, a start script with Windows line endings. A host name with a scheme
does not stop the proxy but breaks every request after it.

Docker and curl are faked: these tests assert what the script concludes from the files,
not what a running stack says.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "diagnose.sh"

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="no openssl")

SECRETS = {"POSTGRES_PASSWORD": "pg-secret-1", "KB_INDEX_PASSWORD": "idx-secret-2",
           "KB_READ_PASSWORD": "read-secret-3", "KB_TOKENS": "token-secret-4"}


def _openssl(*args: str) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _pair(tls: Path, *, matching: bool) -> None:
    tls.mkdir(parents=True, exist_ok=True)
    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-keyout", str(tls / "key.pem"), "-out", str(tls / "cert.pem"),
             "-subj", "/CN=10.10.150.34",
             "-addext", "subjectAltName = DNS:10.10.150.34,IP:10.10.150.34")
    if not matching:
        # What a re-run that fails after writing the key leaves behind.
        _openssl("genpkey", "-algorithm", "RSA", "-out", str(tls / "key.pem"))


def _layout(tmp_path: Path, *, host: str = "10.10.150.34", tls_lines: bool = True) -> Path:
    root = tmp_path / "dgx-infra"
    shutil.copytree(REPO / "compose", root / "compose")
    (root / "scripts").mkdir(parents=True)
    for name in ("diagnose.sh", "make_tls_cert.sh"):
        shutil.copy(REPO / "scripts" / name, root / "scripts" / name)
    lines = [f"KB_PUBLIC_HOST={host}", f"KB_URL_BASE=https://{host}",
             "KB_PATH=/home/kbadmin/dgx-knowledge"]
    lines += [f"{k}={v}" for k, v in SECRETS.items()]
    if tls_lines:
        lines += [f"TLS_CERT={root}/tls/cert.pem", f"TLS_KEY={root}/tls/key.pem"]
    (root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def _run(root: Path) -> str:
    fake = root.parent / "bin"
    fake.mkdir(exist_ok=True)
    for name in ("docker", "curl", "nvidia-smi"):
        (fake / name).write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (fake / name).chmod(0o755)
    openssl_dir = str(Path(shutil.which("openssl")).parent)
    r = subprocess.run(
        ["sh", str(root / "scripts" / "diagnose.sh")], capture_output=True, text=True,
        cwd=str(root), env={"PATH": f"{fake}:{openssl_dir}:/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    assert r.returncode == 0, r.stderr
    return r.stdout + r.stderr


def test_a_mismatched_pair_is_named(tmp_path):
    root = _layout(tmp_path)
    _pair(root / "tls", matching=False)
    assert "certificate and key DO NOT MATCH" in _run(root)


def test_a_matching_pair_is_reported_as_one(tmp_path):
    root = _layout(tmp_path)
    _pair(root / "tls", matching=True)
    out = _run(root)
    assert "certificate and key MATCH" in out
    assert "is not among the certificate's names" not in out


def test_an_unreadable_key_is_not_mistaken_for_a_match(tmp_path):
    """Two fingerprints of nothing are equal. A garbage key must not read as a match."""
    root = _layout(tmp_path)
    _pair(root / "tls", matching=True)
    (root / "tls" / "key.pem").write_text("not a key\n", encoding="utf-8")
    out = _run(root)
    assert "MATCH" not in out.replace("DO NOT MATCH", "")
    assert "cannot compare" in out


def test_a_directory_where_the_certificate_belongs_is_named(tmp_path):
    root = _layout(tmp_path)
    (root / "tls" / "cert.pem").mkdir(parents=True)
    (root / "tls" / "key.pem").write_text("x\n", encoding="utf-8")
    assert "is a DIRECTORY" in _run(root)


def test_a_scheme_in_the_public_host_is_named(tmp_path):
    root = _layout(tmp_path, host="https://10.10.150.34", tls_lines=False)
    assert "is not a bare name or IP" in _run(root)


def test_windows_line_endings_in_the_proxy_start_script_are_named(tmp_path):
    root = _layout(tmp_path, tls_lines=False)
    entry = root / "compose" / "caddy-entrypoint.sh"
    entry.write_bytes(entry.read_bytes().replace(b"\n", b"\r\n"))
    assert "compose/caddy-entrypoint.sh: Windows line endings" in _run(root)


def test_no_secret_from_env_or_the_private_key_is_printed(tmp_path):
    root = _layout(tmp_path)
    _pair(root / "tls", matching=True)
    out = _run(root)
    for key, value in SECRETS.items():
        assert value not in out, f"{key}'s value was printed"
        assert f"{key} is set (value not shown)" in out
    body = "".join((root / "tls" / "key.pem").read_text().splitlines()[1:-1])
    assert body[:40] not in out.replace("\n", "")
