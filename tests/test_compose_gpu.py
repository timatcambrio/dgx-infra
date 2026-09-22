"""GPU reservation for the compose `ollama` service.

Docker never passes a GPU into a container implicitly, and Compose has no conditional
device reservation: `deploy.resources.reservations.devices` either applies or it does
not. A file that always reserves fails to start on a machine without a GPU; a file that
never reserves leaves an eight-GPU host embedding on its CPUs, which is what the stack
did before this. The choice is therefore made by `compose/gpu-detect.sh` before Compose
runs, and expressed as an extra `-f` override file.

The probe tests below drive that script with a fake `nvidia-smi` and a fake `docker` on
PATH, so they assert the decision rule itself rather than whatever hardware happens to be
under the test run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "compose" / "gpu-detect.sh"
BASE = REPO / "compose" / "docker-compose.yml"
OVERRIDE = REPO / "compose" / "docker-compose.gpu.yml"

GPU_LISTING = "\n".join(
    f"GPU {i}: Tesla V100-SXM2-32GB (UUID: GPU-0000000{i})" for i in range(8)
)


def _fake_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _fakes(tmp_path: Path, *, gpus: bool, toolkit: bool) -> Path:
    """A PATH directory whose `nvidia-smi` and `docker` report the given world."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    if gpus:
        _fake_bin(d, "nvidia-smi", f'[ "$1" = "-L" ] && cat <<EOF\n{GPU_LISTING}\nEOF')
    elif (d / "nvidia-smi").exists():
        (d / "nvidia-smi").unlink()
    runtimes = '{"nvidia":{"path":"nvidia-container-runtime"},"runc":{"path":"runc"}}'
    if not toolkit:
        runtimes = '{"runc":{"path":"runc"}}'
    _fake_bin(d, "docker", f"[ \"$1\" = info ] && printf '%s' '{runtimes}'")
    if not toolkit:
        # A CDI install ships nvidia-ctk without registering a runtime; absence of both is
        # what "Docker cannot pass a GPU through" means.
        _fake_bin(d, "nvidia-ctk", "exit 127")
        (d / "nvidia-ctk").unlink()
    return d


def _run(tmp_path: Path, *, mode: str | None, gpus: bool, toolkit: bool):
    fake = _fakes(tmp_path, gpus=gpus, toolkit=toolkit)
    env = {"PATH": f"{fake}:/usr/bin:/bin"}
    if mode is not None:
        env["KB_GPU"] = mode
    return subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True, env=env, cwd=str(REPO)
    )


# --- the decision rule -----------------------------------------------------------------


def test_auto_reserves_the_gpus_when_the_host_and_docker_both_have_them(tmp_path):
    r = _run(tmp_path, mode=None, gpus=True, toolkit=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["-f", str(OVERRIDE)]


def test_auto_falls_back_to_cpu_and_says_so_when_the_host_has_no_gpu(tmp_path):
    r = _run(tmp_path, mode="auto", gpus=False, toolkit=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""
    assert "cpu" in r.stderr.lower()


def test_auto_falls_back_to_cpu_when_docker_cannot_pass_a_gpu_through(tmp_path):
    """GPUs on the host are not enough: without the container toolkit the reservation
    would make every container fail to start."""
    r = _run(tmp_path, mode="auto", gpus=True, toolkit=False)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""
    assert "toolkit" in r.stderr.lower()


def test_on_refuses_to_start_on_the_cpu_rather_than_degrading_silently(tmp_path):
    """A production deploy states that it wants the GPUs; a silent CPU fallback there is
    the defect this whole change exists to remove."""
    r = _run(tmp_path, mode="on", gpus=False, toolkit=False)
    assert r.returncode == 2
    assert r.stdout.strip() == ""
    assert "KB_GPU=on" in r.stderr


def test_on_succeeds_when_the_gpus_are_there(tmp_path):
    r = _run(tmp_path, mode="on", gpus=True, toolkit=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["-f", str(OVERRIDE)]


def test_off_never_reserves_even_with_gpus_present(tmp_path):
    r = _run(tmp_path, mode="off", gpus=True, toolkit=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_an_unknown_mode_is_an_error_naming_the_three_valid_ones(tmp_path):
    r = _run(tmp_path, mode="yes", gpus=True, toolkit=True)
    assert r.returncode == 2
    for word in ("auto", "on", "off"):
        assert word in r.stderr


def test_stdout_carries_only_compose_arguments(tmp_path):
    """The Makefile substitutes stdout straight into a command line, so a stray word of
    prose there would become a bogus Compose argument."""
    for mode, gpus in (("auto", True), ("auto", False), ("off", True)):
        r = _run(tmp_path, mode=mode, gpus=gpus, toolkit=True)
        for token in r.stdout.split():
            assert token == "-f" or token.endswith(".yml")


# --- what the override actually declares ------------------------------------------------


def _config(*files: Path) -> dict:
    argv = ["docker", "compose"]
    for f in files:
        argv += ["-f", str(f)]
    argv += ["--profile", "prod", "config"]
    result = subprocess.run(argv, capture_output=True, text=True, cwd=str(REPO))
    assert result.returncode == 0, result.stderr
    return yaml.safe_load(result.stdout)


requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None, reason="needs the docker CLI to render compose config"
)


@requires_docker
def test_base_stack_reserves_no_device():
    ollama = _config(BASE)["services"]["ollama"]
    assert "deploy" not in ollama or not ollama["deploy"].get("resources", {}).get(
        "reservations", {}
    ).get("devices")


@requires_docker
def test_override_reserves_every_gpu_for_ollama_and_nothing_else():
    config = _config(BASE, OVERRIDE)
    devices = config["services"]["ollama"]["deploy"]["resources"]["reservations"]["devices"]
    assert len(devices) == 1
    device = devices[0]
    assert device["driver"] == "nvidia"
    assert "gpu" in device["capabilities"]
    # Plural: the host's whole set, not one card, and not an enumerated subset that would
    # go stale on different hardware.
    assert device.get("count") in ("all", -1)
    assert not device.get("device_ids")
    for name, service in config["services"].items():
        if name == "ollama":
            continue
        assert not service.get("deploy", {}).get("resources", {}).get("reservations", {}).get(
            "devices"
        ), name


@requires_docker
def test_override_changes_nothing_but_the_reservation():
    """An override file that quietly dropped a volume or a healthcheck would be a much
    worse bug than no GPU at all."""
    base = _config(BASE)
    with_gpu = _config(BASE, OVERRIDE)
    base_ollama = dict(base["services"]["ollama"])
    gpu_ollama = dict(with_gpu["services"]["ollama"])
    gpu_ollama.pop("deploy", None)
    base_ollama.pop("deploy", None)
    assert base_ollama == gpu_ollama
    assert set(base["services"]) == set(with_gpu["services"])
    for name in base["services"]:
        if name != "ollama":
            assert base["services"][name] == with_gpu["services"][name], name
