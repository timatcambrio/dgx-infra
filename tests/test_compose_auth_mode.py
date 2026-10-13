"""`KB_AUTH=off` has to reach `kb serve` inside the compose stack.

The stack starts `kb-mcp` with a fixed command line, so the `--allow-anonymous` flag is
out of an operator's reach; the setting is what they change, and it does nothing unless
Compose passes it into the container. Without this, `KB_AUTH=off` in `.env` would be
silently ignored and the server would refuse to start for want of a credential, which is
the opposite of what the operator chose and reads like a fault.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COMPOSE = REPO / "compose" / "docker-compose.yml"


def test_kb_mcp_is_given_kb_auth() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    kb_mcp = text.split("\n  kb-mcp:\n")[1].split("\n  kb-token:\n")[0]
    assert "KB_AUTH: ${KB_AUTH:-on}" in kb_mcp
