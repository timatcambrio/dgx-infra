"""The stack serves no documents, only the MCP endpoint.

Decided 2026-10-11. Until then Caddy also served the converted markdown under `/kb/*`
(through a `kb-static` file server and a `forward_auth` token check), so that every search
result's `url` was a link a reader could click. The converted files are written for
retrieval, not for people, and opening them raised more questions than it answered, so
the route was removed. Each result still carries the `citation` string, which names the
title, section, source file, pages and date.

What this pins is that nothing quietly brings the route back half-way. A citation `url`
that points at a route nobody serves is the worst outcome of all: a dead link in every
answer. So the three pieces go together: no file server, no route, and no `KB_URL_BASE`
for the server inside the stack, which makes every `url` null.

The MCP tools still return whole documents to anyone allowed to call them; removing the
route is a product decision, not an access control.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
COMPOSE = REPO / "compose" / "docker-compose.yml"
CADDYFILE = REPO / "compose" / "Caddyfile"
MAKEFILE = REPO / "Makefile"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _directives(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def test_there_is_no_document_file_server() -> None:
    assert "kb-static" not in _compose()["services"]


def test_caddy_routes_no_document_path() -> None:
    lines = _directives(CADDYFILE.read_text(encoding="utf-8"))
    assert not any("/kb" in l for l in lines), [l for l in lines if "/kb" in l]
    assert not any(l.startswith("forward_auth") for l in lines)


def test_caddy_does_not_wait_for_a_service_that_no_longer_exists() -> None:
    depends = _compose()["services"]["caddy"].get("depends_on", [])
    assert "kb-static" not in depends


def test_the_server_in_the_stack_is_given_no_url_base() -> None:
    """With no KB_URL_BASE every result's `url` is null, rather than a link to a route
    that does not exist."""
    env = _compose()["services"]["kb-mcp"]["environment"]
    assert "KB_URL_BASE" not in env


def test_compose_env_check_no_longer_polices_url_base() -> None:
    block = MAKEFILE.read_text(encoding="utf-8").split("compose-env-check:")[1].split("\n\n")[0]
    assert "KB_URL_BASE" not in block
