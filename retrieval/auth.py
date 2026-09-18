"""Bearer-token authentication for `kb serve --transport http` (brief §6.5.6) — DECIDED:
a plain ASGI middleware, not FastMCP's `AuthSettings`/`TokenVerifier`. Those advertise
OAuth protected-resource metadata for an authorization server we do not have; our tokens
are pre-shared secrets handed out by Tim, not something a client negotiates.

Tokens come from `Config.kb_tokens` (brief §7.1: `KB_TOKENS`, comma-separated). Only paths
starting with `/mcp` are protected; `/health` and everything else — crucially including
ASGI `lifespan` scope messages, which are not `scope["type"] == "http"` and so always pass
straight through — are exempt. That lifespan pass-through is what enters
`mcp.session_manager.run()` when this middleware wraps `mcp.streamable_http_app()`: that
Starlette app declares `lifespan=lambda app: self.session_manager.run()` itself
(`mcp/server/fastmcp/server.py:streamable_http_app`), and uvicorn drives the ASGI lifespan
protocol on startup — so nothing here needs to enter the session manager explicitly, only
avoid swallowing the messages that let Starlette do it.
"""

from __future__ import annotations

import hmac
from typing import Iterable

from starlette.types import ASGIApp, Receive, Scope, Send

#: Only paths under this prefix require a bearer token. `/health` (brief §6.5.4) and any
#: other route a future milestone adds outside `/mcp` are open by design.
PROTECTED_PREFIX = "/mcp"


def _token_ok(header_value: bytes, tokens: Iterable[str]) -> bool:
    if not header_value.startswith(b"Bearer "):
        return False
    supplied = header_value[len(b"Bearer ") :].decode("utf-8", errors="replace")
    # `hmac.compare_digest` against every configured token: constant-time per comparison,
    # and which token matched is never observable from timing (brief §6.5.6, §8.4).
    return any(hmac.compare_digest(supplied, t) for t in tokens)


async def _send_401(send: Send) -> None:
    """Empty body, `WWW-Authenticate: Bearer` (brief §6.5.6)."""
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"www-authenticate", b"Bearer"),
                (b"content-length", b"0"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})


class BearerMiddleware:
    """Pure ASGI middleware (brief §6.5.6). Wrap `mcp.streamable_http_app()` with this
    before handing the result to uvicorn."""

    def __init__(self, app: ASGIApp, tokens: tuple[str, ...]) -> None:
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PROTECTED_PREFIX):
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        header_value = headers.get(b"authorization", b"")
        if not _token_ok(header_value, self.tokens):
            await _send_401(send)
            return

        await self.app(scope, receive, send)
