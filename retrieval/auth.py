"""Bearer-token authentication for `kb serve --transport http` (brief §6.5.6) — DECIDED:
a plain ASGI middleware, not FastMCP's `AuthSettings`/`TokenVerifier`. Those advertise
OAuth protected-resource metadata for an authorization server we do not have; our tokens
are pre-shared secrets, not something a client negotiates.

Tokens are resolved by a `tokens.TokenStore`, NOT by a tuple captured at process start.
That is the whole point of the store: it re-reads its file as it changes, so revoking one
person's access is a file write that takes effect in seconds, without a restart and so
without dropping every other user. See `tokens.py` for why that mattered enough to change.

Only paths starting with `/mcp` are protected, plus the internal `/auth/check` endpoint
below. `/health` and everything else — crucially including ASGI `lifespan` scope messages,
which are not `scope["type"] == "http"` and so always pass straight through — are exempt.
That lifespan pass-through is what enters `mcp.session_manager.run()` when this middleware
wraps `mcp.streamable_http_app()`: that Starlette app declares
`lifespan=lambda app: self.session_manager.run()` itself
(`mcp/server/fastmcp/server.py:streamable_http_app`), and uvicorn drives the ASGI lifespan
protocol on startup — so nothing here needs to enter the session manager explicitly, only
avoid swallowing the messages that let Starlette do it.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import TYPE_CHECKING, Any

from starlette.types import ASGIApp, Receive, Scope, Send

from .tokens import ANONYMOUS as ANONYMOUS_IDENTITY
from .tokens import Identity, TokenFileError, TokenStore

if TYPE_CHECKING:  # pragma: no cover
    from .config import Config

#: Only paths under this prefix require a bearer token. `/health` (brief §6.5.4) and any
#: other route a future milestone adds outside `/mcp` are open by design.
#:
#: NOTE for the OAuth work, if it is ever done: this is a plain `startswith`, so
#: `/mcp/.well-known/...` is treated as protected and answered 401. RFC 9728 discovery
#: metadata must be anonymous, or a client is told to authenticate in order to learn how
#: to authenticate. Exempt `.well-known` here when that work starts.
PROTECTED_PREFIX = "/mcp"

#: Caddy asks this endpoint whether a request's `Authorization` header is good, for routes
#: it proxies to a backend with no auth of its own — today `/kb/*` -> `kb-static`
#: (`compose/Caddyfile`). 204 or 401, no body either way.
#:
#: Before this existed, Caddy matched `/kb/*` against a regex of every token, baked into
#: its config at container start. That made the token list a second, independent copy:
#: revoking in the store left the same credential reading the entire converted corpus over
#: `/kb/*` until `caddy` was restarted — the outage this change exists to avoid. One store
#: now governs both routes.
#:
#: Not reachable from the LAN: `compose/Caddyfile` routes only `/mcp*`, `/health` and
#: `/kb/*`, and answers everything else 404, so this stays inside the compose network. It
#: would be a mild oracle if exposed (it confirms whether a guessed token is valid), which
#: is no more than `/mcp` itself reveals, but there is no reason to publish it.
AUTH_CHECK_PATH = "/auth/check"

#: One JSON line per authentication decision, on stderr beside the tool-call log
#: (brief §6.5.3). This is the per-user audit trail that one-token-per-user buys and a
#: shared static token could never provide: `token` and `user` identify *who*, not just
#: that someone with a valid token called.
_AUTH_LOGGER = logging.getLogger("kb.server.auth")
_AUTH_LOGGER.setLevel(logging.INFO)
if not _AUTH_LOGGER.handlers:  # pragma: no cover - import-time wiring
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _AUTH_LOGGER.addHandler(_handler)
    _AUTH_LOGGER.propagate = False


def build_store(cfg: "Config", *, allow_anonymous: bool = False) -> TokenStore:
    """The one place a `TokenStore` is built from settings, so `cli.py`'s pre-flight check
    and `server.py`'s middleware cannot disagree about which credentials exist."""
    return TokenStore(
        path=cfg.kb_tokens_file,
        static_tokens=cfg.kb_tokens,
        allow_anonymous=allow_anonymous,
        cache_seconds=cfg.kb_token_cache_seconds,
    )


def _log(event: str, path: str, identity: Identity | None, reason: str | None = None) -> None:
    line: dict[str, Any] = {"event": event, "path": path}
    if identity is not None:
        line["token"] = identity.id
        line["user"] = identity.user
        line["source"] = identity.source
    if reason is not None:
        line["reason"] = reason
    _AUTH_LOGGER.info(json.dumps(line, separators=(",", ":")))


def _supplied_secret(header_value: bytes) -> str | None:
    """The secret out of an `Authorization` header, or `None` if it is not a bearer one."""
    if not header_value.startswith(b"Bearer "):
        return None
    return header_value[len(b"Bearer ") :].decode("utf-8", errors="replace")


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


async def _send_204(send: Send) -> None:
    await send({"type": "http.response.start", "status": 204, "headers": [(b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b""})


class BearerMiddleware:
    """Pure ASGI middleware (brief §6.5.6). Wrap `mcp.streamable_http_app()` with this
    before handing the result to uvicorn."""

    def __init__(self, app: ASGIApp, store: TokenStore) -> None:
        self.app = app
        self.store = store

    def _authenticate(self, scope: Scope) -> Identity | None:
        headers = dict(scope["headers"])
        secret = _supplied_secret(headers.get(b"authorization", b""))
        if secret is None:
            return None
        try:
            return self.store.resolve(secret)
        except TokenFileError as exc:
            # Only reachable if the file broke *and* there was no last good copy, which
            # `kb serve` already refuses to start into. Deny rather than let through.
            _log("auth.error", scope["path"], None, reason=str(exc))
            return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        is_check = path == AUTH_CHECK_PATH
        if not is_check and not path.startswith(PROTECTED_PREFIX):
            await self.app(scope, receive, send)
            return

        identity = self._authenticate(scope)

        if identity is None and self.store.allow_anonymous:
            # `--allow-anonymous` (dev only) means exactly what `kb serve` warns it means:
            # the request is served with no authentication. It used to mean the opposite —
            # an empty token tuple rejected everything — which made the flag useless and
            # its warning false.
            identity = ANONYMOUS_IDENTITY

        if identity is None:
            _log("auth.denied", path, None)
            await _send_401(send)
            return

        _log("auth.ok", path, identity)

        if is_check:
            await _send_204(send)
            return

        # Recorded for the roadmap per-team ACL work (§10): scoping retrieval to the
        # documents the *caller* may see needs the caller's identity to reach the tool
        # layer, and this is where it is known. Nothing reads it yet.
        scope.setdefault("state", {})["kb_identity"] = identity

        await self.app(scope, receive, send)
