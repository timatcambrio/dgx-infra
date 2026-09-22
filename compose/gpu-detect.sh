#!/bin/sh
# Decide whether the compose stack reserves this host's NVIDIA GPUs for `ollama`, and
# print the Compose arguments that carry out the decision.
#
# Why this is a script and not a line in the compose file: Docker never passes a GPU into
# a container implicitly, and Compose has no conditional device reservation. A compose
# file that always reserves fails to start wherever there is no GPU; one that never
# reserves leaves a multi-GPU host embedding on its CPUs. So the choice is made here,
# before Compose runs, and expressed as an extra `-f` override file.
#
#   KB_GPU=auto   (default) use the GPUs when they are usable, say which way it went
#   KB_GPU=on     require them; exit 2 naming what is missing rather than degrade quietly
#   KB_GPU=off    never use them
#
# KB_GPU is read from the environment first and from `.env` otherwise. Compose's
# `--env-file` populates Compose's own interpolation, not this script's shell, so a
# `KB_GPU=on` line in `.env` would silently do nothing if this script did not read it.
#
# stdout carries Compose arguments and nothing else, so it can be substituted straight
# into a command line. Every human-readable word goes to stderr. `--quiet` suppresses the
# verdict for the substitution call, which would otherwise print it twice per target.

set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OVERRIDE="$HERE/docker-compose.gpu.yml"

QUIET=""
[ "${1:-}" = "--quiet" ] && QUIET=1

say() { [ -n "$QUIET" ] || printf '%s\n' "$*" >&2; }
fail() { printf '%s\n' "$*" >&2; exit 2; }

# Two independent things must hold and neither implies the other: the host must have a
# driver and at least one GPU, and Docker must be able to hand them to a container.
host_has_gpu() {
    command -v nvidia-smi >/dev/null 2>&1 || return 1
    nvidia-smi -L 2>/dev/null | grep -q '^GPU [0-9]'
}

docker_can_pass_gpu() {
    # The NVIDIA Container Toolkit registers an `nvidia` runtime with the daemon. A newer
    # CDI-based install may not register one but ships `nvidia-ctk`; either is enough for
    # a device reservation to resolve.
    if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'; then
        return 0
    fi
    command -v nvidia-ctk >/dev/null 2>&1
}

ENV_FILE=${KB_ENV_FILE:-$HERE/../.env}

MODE=${KB_GPU:-}
if [ -z "$MODE" ] && [ -f "$ENV_FILE" ]; then
    # One named key, read literally: never source `.env`, which would execute it and is
    # the file holding the database password.
    MODE=$(sed -n 's/^[[:space:]]*KB_GPU[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" \
           | tr -d '"'"'"'"' \
           | tail -1)
fi
[ -n "$MODE" ] || MODE=auto

case "$MODE" in
    off)
        say "GPU: off (KB_GPU=off); ollama will embed on the CPU"
        exit 0
        ;;
    on|auto) ;;
    *)
        fail "KB_GPU must be auto, on or off (got '$MODE')"
        ;;
esac

MISSING=""
if ! host_has_gpu; then
    MISSING="no NVIDIA GPU visible to nvidia-smi on this host"
elif ! docker_can_pass_gpu; then
    MISSING="Docker cannot pass a GPU through: install the NVIDIA Container Toolkit and restart the daemon"
fi

if [ -n "$MISSING" ]; then
    # `on` is how a production deploy states that the GPUs are the point. Falling back to
    # the CPU there is the defect this script exists to remove, so it is an error.
    [ "$MODE" = on ] && fail "GPU: required (KB_GPU=on) but unavailable -- $MISSING"
    say "GPU: none usable, ollama will embed on the CPU ($MISSING)"
    exit 0
fi

say "GPU: reserving all NVIDIA GPUs for ollama"
printf '%s\n' "-f $OVERRIDE"
