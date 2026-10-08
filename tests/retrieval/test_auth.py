"""`retrieval.auth`: the bearer middleware in front of the HTTP
transport, tested against a minimal ASGI app so these tests need neither Postgres nor a
running MCP session — `test_server_http.py` covers the real `/mcp` traffic end to end.

`test_tokens.py` covers the store itself (file format, hashing, caching, revocation
timing); here the store is a means to an end and mostly built from `static_tokens`.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import logging
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from retrieval import auth as auth_module
from retrieval import tokens as tokens_module

TOKENS = ("right-token", "other-token")

@contextlib.contextmanager
def _audit_lines():
    """The JSON lines the auth logger emits during the block.

    Not `caplog`, and not stderr capture: the logger sets `propagate = False` (so the
    audit stream stays one JSON object per line whatever configures the root logger) and
    its handler binds `sys.stderr` at import, before pytest replaces it. Attaching a
    handler for the duration is the one way to read it that depends on neither.
    """
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Collect()
    logger = logging.getLogger("kb.server.auth")
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)




async def _health(_request):
    return JSONResponse({"ok": True})


async def _mcp(request):
    # Echoes the identity the middleware recorded, so the ACL hook is covered rather than
    # merely present.
    identity = request.scope.get("state", {}).get("kb_identity")
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"user": identity.user if identity else None},
        }
    )


def _app(store: tokens_module.TokenStore | None = None) -> Starlette:
    inner = Starlette(
        routes=[
            Route("/health", _health, methods=["GET"]),
            Route("/mcp", _mcp, methods=["POST"]),
        ]
    )
    return auth_module.BearerMiddleware(
        inner, store or tokens_module.TokenStore(static_tokens=TOKENS)
    )


def _client(store: tokens_module.TokenStore | None = None) -> TestClient:
    return TestClient(_app(store))


def test_health_is_open_without_a_token() -> None:
    resp = _client().get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_mcp_without_header_is_401_with_empty_body_and_www_authenticate() -> None:
    resp = _client().post("/mcp", json={})
    assert resp.status_code == 401
    assert resp.content == b""
    assert resp.headers["www-authenticate"] == "Bearer"


def test_mcp_with_wrong_token_is_401() -> None:
    resp = _client().post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401
    assert resp.content == b""


def test_mcp_with_malformed_header_is_401() -> None:
    resp = _client().post("/mcp", json={}, headers={"Authorization": "right-token"})
    assert resp.status_code == 401


def test_mcp_with_right_token_is_let_through() -> None:
    resp = _client().post("/mcp", json={}, headers={"Authorization": "Bearer right-token"})
    assert resp.status_code == 200
    assert resp.json()["result"] == {"user": "(KB_TOKENS)"}


def test_mcp_with_second_configured_token_is_also_let_through() -> None:
    """Two tokens are accepted independently — exercises the loop over `KB_TOKENS` rather
    than just the first configured token."""
    resp = _client().post("/mcp", json={}, headers={"Authorization": "Bearer other-token"})
    assert resp.status_code == 200


def test_constant_time_compare_is_used() -> None:
    """`hmac.compare_digest` is referenced by the store the middleware resolves through
   . It moved out of `auth.py` with the store; the property has not."""
    assert "hmac.compare_digest" in inspect.getsource(tokens_module)


def test_non_mcp_path_is_not_protected() -> None:
    """Only paths under `/mcp` require a token; a request to some other path (here 404,
    since the test app defines no route for it) reaches the wrapped app unauthenticated
    rather than being rejected by the middleware."""
    resp = _client().get("/other")
    assert resp.status_code == 404  # from the inner Starlette app, not a 401


# --------------------------------------------------------------------------------------
# Identity, and the file-backed store the revocation fix exists for.
# --------------------------------------------------------------------------------------


def _store_with(tmp_path: Path, user: str = "alice@example.com") -> tuple[tokens_module.TokenStore, str, str]:
    path = tmp_path / "tokens.json"
    secret, record = tokens_module.issue(path, user)
    return tokens_module.TokenStore(path=path, cache_seconds=0.0), secret, record.id


def test_issued_token_authenticates_and_names_its_owner(tmp_path: Path) -> None:
    store, secret, _ = _store_with(tmp_path)
    resp = _client(store).post("/mcp", json={}, headers={"Authorization": f"Bearer {secret}"})
    assert resp.status_code == 200
    assert resp.json()["result"] == {"user": "alice@example.com"}


def test_revoking_takes_effect_without_rebuilding_the_app(tmp_path: Path) -> None:
    """The whole point of the change: one user's access is withdrawn while the server
    keeps running, and the other user is untouched."""
    path = tmp_path / "tokens.json"
    alice_secret, alice = tokens_module.issue(path, "alice@example.com")
    bob_secret, _ = tokens_module.issue(path, "bob@example.com")
    client = _client(tokens_module.TokenStore(path=path, cache_seconds=0.0))

    assert client.post("/mcp", json={}, headers={"Authorization": f"Bearer {alice_secret}"}).status_code == 200
    assert client.post("/mcp", json={}, headers={"Authorization": f"Bearer {bob_secret}"}).status_code == 200

    tokens_module.revoke(path, alice.id)

    assert client.post("/mcp", json={}, headers={"Authorization": f"Bearer {alice_secret}"}).status_code == 401
    assert client.post("/mcp", json={}, headers={"Authorization": f"Bearer {bob_secret}"}).status_code == 200


# --------------------------------------------------------------------------------------
# `/auth/check` — what Caddy asks on behalf of `/kb/*`.
# --------------------------------------------------------------------------------------


def test_auth_check_with_a_good_token_is_204_with_no_body() -> None:
    resp = _client().get("/auth/check", headers={"Authorization": "Bearer right-token"})
    assert resp.status_code == 204
    assert resp.content == b""


def test_auth_check_without_a_token_is_401() -> None:
    resp = _client().get("/auth/check")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_auth_check_never_reaches_the_wrapped_app() -> None:
    """It is answered by the middleware itself, so no route has to exist for it — and a
    token good enough for `/auth/check` cannot be used to reach anything else through it."""
    resp = _client().get("/auth/check", headers={"Authorization": "Bearer right-token"})
    assert resp.status_code == 204  # not the inner app's 404


def test_revoking_closes_kb_files_too(tmp_path: Path) -> None:
    """The gap this endpoint closes: before it, `/kb/*` was gated by a token regex baked
    into Caddy at container start, so a revoked token still read the whole corpus until
    `caddy` restarted."""
    store, secret, token_id = _store_with(tmp_path)
    client = _client(store)
    assert client.get("/auth/check", headers={"Authorization": f"Bearer {secret}"}).status_code == 204
    tokens_module.revoke(tmp_path / "tokens.json", token_id)
    assert client.get("/auth/check", headers={"Authorization": f"Bearer {secret}"}).status_code == 401


# --------------------------------------------------------------------------------------
# `--allow-anonymous`, and the audit log.
# --------------------------------------------------------------------------------------


def test_allow_anonymous_actually_serves_requests_with_no_token() -> None:
    """`kb serve --allow-anonymous` warns that "every request to /mcp* will be accepted
    with NO authentication". Before the store it did the opposite — an empty token tuple
    matched nothing, so it rejected everything and the flag was useless."""
    store = tokens_module.TokenStore(allow_anonymous=True)
    resp = _client(store).post("/mcp", json={})
    assert resp.status_code == 200
    assert resp.json()["result"] == {"user": "anonymous"}


def test_allow_anonymous_still_prefers_a_real_identity(tmp_path: Path) -> None:
    """A valid token is resolved to its owner even with the flag on, so the audit log of a
    dev run is still useful."""
    path = tmp_path / "tokens.json"
    secret, _ = tokens_module.issue(path, "alice@example.com")
    store = tokens_module.TokenStore(path=path, allow_anonymous=True, cache_seconds=0.0)
    resp = _client(store).post("/mcp", json={}, headers={"Authorization": f"Bearer {secret}"})
    assert resp.json()["result"] == {"user": "alice@example.com"}


def test_each_decision_is_logged_as_one_json_line_naming_the_user(tmp_path: Path) -> None:
    """The per-user audit trail one-token-per-user buys: a
    shared static token could record that *someone* called, never who.

    """
    store, secret, _ = _store_with(tmp_path, user="carol@example.com")
    client = _client(store)
    with _audit_lines() as emitted:
        client.post("/mcp", json={}, headers={"Authorization": f"Bearer {secret}"})
        client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})

    lines = [json.loads(line) for line in emitted]
    assert [line["event"] for line in lines] == ["auth.ok", "auth.denied"]
    assert lines[0]["user"] == "carol@example.com"
    assert lines[0]["path"] == "/mcp"
    assert lines[0]["source"] == "file"
    # A denial names no user, because there is no credential to attribute it to.
    assert "user" not in lines[1]


def test_no_log_line_mentions_the_secret(tmp_path: Path) -> None:
    store, secret, _ = _store_with(tmp_path)
    with _audit_lines() as emitted:
        _client(store).post("/mcp", json={}, headers={"Authorization": f"Bearer {secret}"})
    assert secret not in "".join(emitted)
