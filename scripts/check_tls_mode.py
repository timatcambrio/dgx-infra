"""Pre-flight for `make compose-up`: the settings that depend on HTTPS versus HTTP agree.

`KB_TLS=off` serves the stack over plain HTTP on port 80, for a deployment whose network
and VPN are its boundary and whose users should not have to trust a certificate. Unset or
`on` keeps HTTPS on 443, the default.

Three other settings must agree with it, and each mismatch fails somewhere that does not
name the cause (see tests/test_compose_http_mode.py): a certificate on an HTTP site stops
Caddy starting at all, and a `KB_URL_BASE` with the wrong scheme leaves the service
working while every citation link points at a port nothing listens on.

    python3 scripts/check_tls_mode.py [.env]          exit 2 naming the mismatch, else 0
    python3 scripts/check_tls_mode.py --port [.env]   print the port Caddy listens on

`--port` is how the Makefile gets `KB_LISTEN_PORT` for Compose, so the port is derived from
the mode and is never a second setting to keep in step with it. It never fails: before
`.env` exists it prints 443 and leaves the complaint to compose-env-check.

Standard library only, like check_token_paths.py, so a host with no venv can run it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PORTS = {"on": "443", "off": "80"}


def _read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    want_port = "--port" in argv
    rest = [a for a in argv if a != "--port"]
    env = _read_env(Path(rest[0]) if rest else REPO_ROOT / ".env")

    mode = env.get("KB_TLS", "") or "on"

    if want_port:
        print(PORTS.get(mode, PORTS["on"]))
        return 0

    if mode not in PORTS:
        print(f"KB_TLS must be `on` or `off` (or unset, meaning on). Found KB_TLS={mode}",
              file=sys.stderr)
        return 2

    url_base = env.get("KB_URL_BASE", "")

    if mode == "off":
        if env.get("TLS_CERT") or env.get("TLS_KEY"):
            print(
                "KB_TLS=off serves plain HTTP, but TLS_CERT/TLS_KEY are set. Caddy refuses a\n"
                "certificate on a site it serves over HTTP and would not start. Remove both\n"
                "lines from .env, or remove KB_TLS=off to serve HTTPS with that certificate.",
                file=sys.stderr,
            )
            return 2
        if url_base and not url_base.startswith("http://"):
            print(
                f"KB_TLS=off serves plain HTTP on port 80, but KB_URL_BASE={url_base}.\n"
                "Every citation link would point at a port nothing listens on. Set:\n\n"
                "    KB_URL_BASE=http://<KB_PUBLIC_HOST>\n",
                file=sys.stderr,
            )
            return 2
        return 0

    if url_base and not url_base.startswith("https://"):
        print(
            f"The stack serves HTTPS on port 443, but KB_URL_BASE={url_base}.\n"
            "Every citation link would point at a port nothing listens on. Either set\n\n"
            "    KB_URL_BASE=https://<KB_PUBLIC_HOST>\n\n"
            "or, to serve plain HTTP instead, add KB_TLS=off.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
