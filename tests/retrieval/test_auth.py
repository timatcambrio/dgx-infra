"""`retrieval.auth` (brief §6.5.6, §8.4): the bearer middleware in front of the HTTP
transport, tested against a minimal ASGI app so these tests need neither Postgres nor a
running MCP session — `test_server_http.py` covers the real `/mcp` traffic end to end.
"""

from __future__ import annotations

import inspect

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from retrieval import auth as auth_module

TOKENS = ("right-token", "other-token")


async def _health(_request):
    return JSONResponse({"ok": True})


async def _mcp(_request):
    return JSONResponse({"jsonrpc": "2.0", "id": 1, "result": {}})


def _app() -> Starlette:
    inner = Starlette(
        routes=[
            Route("/health", _health, methods=["GET"]),
            Route("/mcp", _mcp, methods=["POST"]),
        ]
    )
    return auth_module.BearerMiddleware(inner, TOKENS)


def _client() -> TestClient:
    return TestClient(_app())


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
    assert resp.json()["result"] == {}


def test_mcp_with_second_configured_token_is_also_let_through() -> None:
    """Two tokens are accepted independently — exercises the `any(...)` over `KB_TOKENS`
    rather than just the first configured token."""
    resp = _client().post("/mcp", json={}, headers={"Authorization": "Bearer other-token"})
    assert resp.status_code == 200


def test_constant_time_compare_is_used() -> None:
    """`hmac.compare_digest` is referenced by the middleware module (brief §8.4)."""
    source = inspect.getsource(auth_module)
    assert "hmac.compare_digest" in source


def test_non_mcp_path_is_not_protected() -> None:
    """Only paths under `/mcp` require a token; a request to some other path (here 404,
    since the test app defines no route for it) reaches the wrapped app unauthenticated
    rather than being rejected by the middleware."""
    resp = _client().get("/other")
    assert resp.status_code == 404  # from the inner Starlette app, not a 401
