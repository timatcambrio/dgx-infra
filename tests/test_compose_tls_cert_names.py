"""The supplied certificate must carry every name the stack answers for.

`tests/test_compose_tls.py` pins what Caddy serves a client that sends no SNI. This pins
the other half of the same problem, which only exists on the TLS_CERT/TLS_KEY path:

With `tls internal` Caddy issues a certificate per name, on demand, from its own CA, so
the set of names it can answer for is open and the question never arises. With ONE
supplied certificate the set is closed at the moment the certificate is made, and a client
dialling a name that is not on it fails verification -- a different failure from the
handshake one, at a different layer, with a different message, and a failure no test
process can see because neither a certificate nor Caddy exists in one.

compose/Caddyfile serves a single site block for three names, and the global `default_sni`
names a fourth thing (whatever $KB_PUBLIC_HOST is) for SNI-less clients. So the names on
the certificate `scripts/make_tls_cert.sh` writes must cover the site block's list, or the
deployment is reachable by some of its own documented addresses and not others -- which is
exactly the shape of the bug that cost the 2026-10-01 run its first hour, one layer up.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CADDYFILE = REPO / "compose" / "Caddyfile"
CERT_SCRIPT = REPO / "scripts" / "make_tls_cert.sh"

#: How the Caddyfile spells KB_PUBLIC_HOST. On the certificate this is the script's
#: positional argument, so it is covered by construction rather than by name.
PUBLIC_HOST_PLACEHOLDER = "{$KB_PUBLIC_HOST:localhost}"


def _site_names() -> list[str]:
    """The names the Caddyfile's one site block answers for."""
    line = next(
        l for l in CADDYFILE.read_text(encoding="utf-8").splitlines()
        if l.startswith(PUBLIC_HOST_PLACEHOLDER)
    )
    return [n.strip() for n in line.rstrip("{").strip().split(",")]


def test_the_script_covers_every_name_the_site_block_serves() -> None:
    script = CERT_SCRIPT.read_text(encoding="utf-8")
    # The script's two SAN lists, read literally rather than by running openssl: what is
    # asserted is that no name the site block serves was left off them.
    dns = re.search(r'^DNS_NAMES="([^"]*)"', script, re.M)
    ips = re.search(r'^IP_NAMES="([^"]*)"', script, re.M)
    assert dns and ips, "the script no longer declares DNS_NAMES/IP_NAMES; this test reads them"
    covered = set(dns.group(1).split()) | set(ips.group(1).split())

    for name in _site_names():
        if name == PUBLIC_HOST_PLACEHOLDER:
            # Supplied as the script's required first argument, and refused if empty.
            assert '$PUBLIC_HOST localhost' in script or "$PUBLIC_HOST" in dns.group(1)
            continue
        assert name in covered, (
            f"compose/Caddyfile serves {name!r} but scripts/make_tls_cert.sh puts no such "
            f"SAN on the certificate, so a client dialling {name!r} fails verification "
            f"on the TLS_CERT path while working on the tls-internal one"
        )


def test_the_public_host_is_required_not_defaulted() -> None:
    """A certificate quietly made for `localhost` when KB_PUBLIC_HOST was forgotten is
    worse than no certificate: Caddy starts, the handshake completes for anyone on the
    host, and every real client fails verification with a name mismatch."""
    script = CERT_SCRIPT.read_text(encoding="utf-8")
    assert 'PUBLIC_HOST=${1:-}' in script
    assert 'exit 2' in script.split("PUBLIC_HOST=${1:-}")[1].split("shift")[0]


def test_an_ip_argument_becomes_an_ip_san() -> None:
    """A DNS SAN does not match a client that dialled an IP address -- they are separate
    certificate fields and a client checks the one matching what it typed. The AWS run
    reaches the instance by public IP before any name exists for it, so conflating the two
    would make the first probe of every deployment fail."""
    script = CERT_SCRIPT.read_text(encoding="utf-8")
    assert 'IP_NAMES="$IP_NAMES $extra"' in script
    assert 'IP:$n' in script and 'DNS:$n' in script
