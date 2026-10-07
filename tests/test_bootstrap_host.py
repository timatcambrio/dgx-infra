"""`scripts/bootstrap_host.sh` and the docker group it adds the login user to.

Group membership is read at login, so a user the script has just added to `docker` still
cannot reach the daemon in the same session. The script's last step, the GPU passthrough
check, used to call plain `docker run` regardless. On a host where the user was not yet in
the group, the first run therefore ended in `FAILED: a container cannot see the GPUs` on a
host whose GPUs were fine: the container never started, because the socket refused the
user. An operator following the runbook would read that as a driver or toolkit fault.

These drive the script with fakes on PATH: a Linux `uname`, an `id` that reports group
membership as the test chooses, a `sudo` that marks what it ran, and a `docker` whose
`run` succeeds only through that `sudo` unless the user is in the group.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "bootstrap_host.sh"


def _fake_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _fakes(tmp_path: Path, *, in_group: bool) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    groups = "tester docker" if in_group else "tester"
    _fake_bin(d, "uname", 'echo Linux')
    _fake_bin(d, "apt-get", "exit 0")
    _fake_bin(d, "usermod", "exit 0")
    _fake_bin(
        d, "id",
        'case "$1" in -u) echo 1000;; -un) echo tester;; -nG) echo "' + groups + '";; esac',
    )
    _fake_bin(d, "sudo", 'SUDO_USED=1 exec "$@"')
    _fake_bin(
        d, "nvidia-smi",
        'case "$1" in -L) echo "GPU 0: NVIDIA A100 (UUID: GPU-0)";; *) echo "A100, 550, 80 GiB";; esac',
    )
    in_group_flag = "1" if in_group else "0"
    _fake_bin(
        d, "docker",
        f"""case "$1" in
  compose) echo "Docker Compose version v2.29.0"; exit 0;;
  --version) echo "Docker version 27.0.0"; exit 0;;
  info) printf '%s' '{{"nvidia":{{}},"runc":{{}}}}'; exit 0;;
  run)
    if [ "{in_group_flag}" = 1 ] || [ "${{SUDO_USED:-}}" = 1 ]; then
      echo "GPU 0: NVIDIA A100 (UUID: GPU-0)"; exit 0
    fi
    echo "permission denied while trying to connect to the Docker daemon socket" >&2
    exit 1;;
esac""",
    )
    return d


def _run(tmp_path: Path, *args: str, in_group: bool):
    fake = _fakes(tmp_path, in_group=in_group)
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        capture_output=True, text=True,
        env={"PATH": f"{fake}:/usr/bin:/bin"},
    )


def test_first_run_that_adds_the_user_to_docker_still_verifies_the_gpus(tmp_path):
    """The first run on a host where the login user is not in `docker` must not report a
    GPU failure caused only by the socket refusing a user it has just added."""
    r = _run(tmp_path, in_group=False)
    assert r.returncode == 0, r.stderr
    assert "cannot see the GPUs" not in r.stderr
    assert "== Ready" in r.stderr
    assert "log out" in r.stderr.lower()


def test_check_without_the_group_says_to_log_in_again_not_that_the_gpus_failed(tmp_path):
    """`--check` in a session that predates the group change must name the group and the
    fix, rather than blaming the GPUs."""
    r = _run(tmp_path, "--check", in_group=False)
    assert r.returncode != 0
    assert "docker group" in r.stderr
    assert "log out" in r.stderr.lower()
    assert "cannot see the GPUs" not in r.stderr


def test_check_in_the_group_ends_ready(tmp_path):
    """The state the runbook asks for after logging back in."""
    r = _run(tmp_path, "--check", in_group=True)
    assert r.returncode == 0, r.stderr
    assert "== Ready" in r.stderr
