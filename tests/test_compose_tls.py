"""How the HTTPS stack answers a client that sends no SNI.

Measured 2026-10-01 against the real stack, bringing it up on a fresh Linux host and
dialling it the way stages 1-4 dialled the plain-HTTP server — by IP address:

    $ curl https://<ip>/health
    OpenSSL/3.0.13: error:0A000438:SSL routines::tlsv1 alert internal error

    $ openssl s_client -connect <ip>:443
    SSL alert number 80
    no peer certificate available

TLS SNI carries host NAMES only, so a client dialling an IP sends none, and Caddy had no
default certificate to answer with — it failed the handshake before any HTTP, before any
Host header, and therefore before `KB_PUBLIC_HOST` or the bearer token were in the
picture. The same request with SNI (`https://localhost/health`, or any name the site
block lists) completed the handshake.

That makes the HTTP -> HTTPS move a silent break for every IP-addressed client: stages 1-4
worked over plain HTTP, where there is no SNI to be missing. `default_sni` names the
certificate to serve such a client, and `KB_PUBLIC_HOST` is the right one because the site
block already lists it, so Caddy already holds a certificate for it under `tls internal`.

This is not the same failure as the `KB_PUBLIC_HOST` one the README warns about. That one
is MCP's DNS-rebinding protection reading a Host header, which is an HTTP-layer refusal of
a completed connection. This one is TLS, one layer down, and no Host header is ever read.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CADDYFILE = REPO / "compose" / "Caddyfile"

#: The Caddyfile spells KB_PUBLIC_HOST this way everywhere, default included.
PUBLIC_HOST = "{$KB_PUBLIC_HOST:localhost}"


def _text() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def test_an_sni_less_client_is_given_a_default_certificate() -> None:
    assert f"default_sni {PUBLIC_HOST}" in _text(), (
        "without it, `curl https://<ip>/health` fails in the TLS handshake with alert 80 "
        "and no certificate — see this module's docstring"
    )


def test_the_default_certificate_is_one_the_site_has() -> None:
    """`default_sni` naming anything the site block does not list would hand out a
    certificate Caddy never issues, turning a handshake failure into a name mismatch."""
    text = _text()
    site_line = next(l for l in text.splitlines() if l.startswith(PUBLIC_HOST))
    names = [n.strip() for n in site_line.rstrip("{").strip().split(",")]
    assert PUBLIC_HOST in names, site_line


def test_the_default_is_set_once_globally() -> None:
    """A per-site `default_sni` is not a `tls` subdirective in Caddy 2 — `caddy validate`
    rejects it with "unknown subdirective". It belongs in the global options block, which
    is also the only place it can apply to a connection that named no site."""
    lines = _text().splitlines()
    start = lines.index("{")                      # the global options block opens on its own line
    end = start + lines[start:].index("}")
    global_block = "\n".join(lines[start + 1 : end])
    assert "default_sni" in global_block, global_block
    assert _text().count("default_sni") == 1
