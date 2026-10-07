#!/bin/sh
# Make a fresh Linux host able to run the compose stack: Docker, the compose plugin, and
# the NVIDIA Container Toolkit, in that order, idempotently.
#
# This is the one place in the deployment that installs HOST packages. Everything after it
# is container-first -- `make compose-up`, `make token`, `make compose-index` -- because
# the deployment target is given `docker compose` and nothing else. Two things have to be
# true before Compose can reserve a GPU for `ollama`, and neither implies the other:
# the host must have a driver and a card, and Docker must be able to hand them to a
# container (compose/gpu-detect.sh explains why that decision is made before Compose
# runs). This script installs the second; it refuses rather than attempt the first.
#
# WHY THE DRIVER IS NOT INSTALLED HERE. A kernel driver install is not idempotent, often
# wants a reboot, and on a cloud host is the AMI's job: AWS's Deep Learning AMIs and
# Ubuntu's `nvidia-driver-###-server` packages both ship one already. A script that
# installed one would be the least reliable step in the deployment and would hide which
# driver the measurement ran against. So: `nvidia-smi` must already work.
#
#   sh scripts/bootstrap_host.sh            install what is missing, then verify
#   sh scripts/bootstrap_host.sh --check    verify only, install nothing
#
# Verification is the point of the last step: it runs a throwaway CUDA container and reads
# `nvidia-smi` from inside it. A host where `nvidia-smi` works and the container one fails
# is exactly the state `KB_GPU=on` exists to refuse, and it is invisible from the host.

set -eu

CHECK_ONLY=""
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

say()  { printf '%s\n' "$*" >&2; }
step() { printf '\n== %s\n' "$*" >&2; }
fail() { printf 'FAILED: %s\n' "$*" >&2; exit 2; }

# A CUDA base image is the smallest thing that carries `nvidia-smi` for the container-side
# check. Pinned: `latest` would change what the recorded measurement ran against.
CUDA_IMAGE=${CUDA_IMAGE:-nvidia/cuda:12.4.1-base-ubuntu22.04}

[ "$(uname -s)" = "Linux" ] || fail "this installs Linux packages; run it on the deployment host, not the dev machine"
command -v apt-get >/dev/null 2>&1 || fail "no apt-get: this script covers Debian/Ubuntu hosts only (the DGX and AWS Ubuntu AMIs)"

SUDO=""
[ "$(id -u)" = "0" ] || SUDO="sudo"

step "NVIDIA driver (not installed by this script -- see the header)"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L 2>/dev/null | grep -q '^GPU [0-9]'; then
    say "ok: $(nvidia-smi -L | wc -l | tr -d ' ') GPU(s) visible to the host driver"
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader >&2
else
    fail "no GPU visible to nvidia-smi. Launch a GPU instance with a driver-bearing image
       (an AWS Deep Learning AMI, or Ubuntu plus 'apt-get install nvidia-driver-<ver>-server'
       and a reboot), then run this again. KB_GPU=on would refuse to start the stack here."
fi

step "Docker Engine and the compose plugin"
if docker compose version >/dev/null 2>&1; then
    say "ok: $(docker --version), $(docker compose version | head -1)"
else
    [ -n "$CHECK_ONLY" ] && fail "docker compose is not installed (run without --check to install it)"
    # Docker's own apt repository, not the distro's docker.io: the compose PLUGIN
    # (`docker compose`, not the retired `docker-compose` script) ships there, and the
    # Makefile's COMPOSE invocation is the plugin form.
    $SUDO install -m 0755 -d /etc/apt/keyrings
    . /etc/os-release
    $SUDO curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
    $SUDO chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
        | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
    $SUDO apt-get update -qq
    $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    say "installed: $(docker compose version | head -1)"
fi

step "NVIDIA Container Toolkit (what lets Docker pass a GPU through)"
if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"' \
   || command -v nvidia-ctk >/dev/null 2>&1; then
    say "ok: Docker can resolve a device reservation"
else
    [ -n "$CHECK_ONLY" ] && fail "the NVIDIA Container Toolkit is not installed (run without --check)"
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | $SUDO gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | $SUDO tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
    $SUDO apt-get update -qq
    $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nvidia-container-toolkit
    # Registers the `nvidia` runtime with the daemon; the restart is what makes it visible
    # to `docker info`, which is what gpu-detect.sh reads.
    $SUDO nvidia-ctk runtime configure --runtime=docker
    $SUDO systemctl restart docker
    say "installed and registered"
fi

step "Docker group membership (so the deploy needs no sudo)"
# Group membership is read at login, so a user added below still cannot reach the daemon
# from this session. The passthrough check then goes through sudo; a plain `docker run`
# was refused at the socket and reported as a GPU failure on a host whose GPUs were fine.
DOCKER="docker"
JUST_ADDED=""
if [ "$(id -u)" = "0" ] || id -nG | tr ' ' '\n' | grep -qx docker; then
    say "ok"
else
    [ -n "$CHECK_ONLY" ] && fail "this login session is not in the docker group. If this script added you,
       log out and back in, then run it again. Every make target needs the group."
    $SUDO usermod -aG docker "$(id -un)"
    DOCKER="$SUDO docker"
    JUST_ADDED=1
    say "added $(id -un) to the docker group -- LOG OUT AND BACK IN before the make targets"
    say "(group membership is read at login). The check below uses sudo until then."
fi

step "GPU passthrough, verified inside a container"
# The whole reason this script ends here rather than at the install. A host whose own
# nvidia-smi works while the container's does not is the silent half-configured state
# `KB_GPU=on` exists to catch, and nothing on the host reveals it.
if $DOCKER run --rm --gpus all "$CUDA_IMAGE" nvidia-smi -L >/dev/null 2>&1; then
    say "ok: a container sees"
    $DOCKER run --rm --gpus all "$CUDA_IMAGE" nvidia-smi -L >&2
else
    fail "a container cannot see the GPUs even though the host can. Try 'sudo systemctl restart docker';
       if it persists, 'nvidia-ctk runtime configure --runtime=docker' did not take. Until this
       passes, 'make compose-up' with KB_GPU=on will refuse to start -- which is correct."
fi

step "Ready"
say "This host can run the stack on its GPUs. Next: compose/gpu-detect.sh (or 'make gpu-check')"
say "reports the same verdict from the repo, and 'make compose-up' acts on it."
[ -n "$JUST_ADDED" ] && say "Log out and back in first, then confirm with: sh scripts/bootstrap_host.sh --check"
exit 0
