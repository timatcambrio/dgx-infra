"""Probe a running compose stack end to end, the way a client on the LAN reaches it.

`scripts/mcp_probe.py` drives the server over **stdio** and asks whether the evidence an
assistant needs is reachable. This asks the question one layer out, against the deployed
thing: **does the stack behind Caddy serve the right answers to the right people, and stop
serving them the moment a token is revoked?** Everything here goes to the public address,
over TLS unless the stack runs with `KB_TLS=off` and `--base-url` is `http://`, so it
exercises what no unit test can: the certificate, the reverse proxy, which paths it
routes, and the live token store.

    uv run python scripts/http_probe.py --base-url https://kb.internal --ca ./dgx-kb-ca.crt

Tokens. The read-only checks need two issued tokens, passed as `KB_PROBE_TOKEN` and
`KB_PROBE_TOKEN_2`, so the probe can run from any client machine and never has to see the
store. Given `--tokens-file` instead (run on the host that owns the store, or in the
`kb-token` container), it issues its own throwaway pair, runs the revocation checks too,
and revokes both on the way out. Secrets are never printed, logged, or echoed. With
`--no-auth`, for a stack running `KB_AUTH=off`, it uses no token at all and expects `/mcp`
to answer without one.

Nothing about the deployment is taken from the local checkout: run from a client, this
machine's `.env` describes a different deployment (measured 2026-10-06, three false
failures from the dev Mac against a correct AWS stack).

The stack serves no converted documents (tests/test_compose_no_document_route.py), so the
probe checks that `/kb/` is NOT routed, with or without a token, and that results carry no
link. What the revocation checks pin down is the claim the token store exists to make:
revoking one person closes `/mcp*` for them within `KB_TOKEN_CACHE_SECONDS`, with no
restart and without touching anybody else.

Read-only against the corpus and the database. Exits 0 only if every check passed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import ssl
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from retrieval import config as config_module  # noqa: E402
from retrieval import tokens as tokens_module  # noqa: E402

EXPECTED_TOOLS = {"search", "fetch", "list_documents", "get_outline", "get_section"}

#: Issued with this owner so a store left behind by a crashed run is obvious, and so
#: `--tokens-file` runs never collide with a real user's record.
PROBE_USER = "http-probe@localhost"


class Report:
    """Every check's verdict, printed as it happens."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
        else:
            self.failures.append(name)
            print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
        return ok

    def section(self, title: str) -> None:
        print(f"\n{title}")

    def finish(self) -> int:
        total = self.passed + len(self.failures)
        print(f"\n{self.passed}/{total} checks passed")
        if self.failures:
            print("failed: " + ", ".join(self.failures))
        return 1 if self.failures else 0


def _auth_headers(token: str | None) -> dict[str, str]:
    """No token means no header at all, which is what a client with no credential
    configured sends; `Bearer ` with nothing after it is a different request."""
    return {"Authorization": f"Bearer {token}"} if token else {}


def _client(ca: str | None, token: str | None = None) -> httpx.Client:
    verify: Any = ca if ca else True
    return httpx.Client(verify=verify, headers=_auth_headers(token), timeout=15.0,
                        trust_env=False)


# --------------------------------------------------------------------------------------
# The plain HTTP surface: what is open, what is closed, and what is not routed at all.
# --------------------------------------------------------------------------------------


def probe_routes(report: Report, base: str, ca: str | None, token: str | None,
                 no_auth: bool) -> None:
    plain = base.startswith("http://")
    report.section("Routes (Caddy, and who is let in)" if plain
                   else "Routes (TLS, Caddy, and who is let in)")

    with _client(ca) as anon:
        try:
            health = anon.get(f"{base}/health")
        except ssl.SSLError as exc:  # the handshake itself, which is its own failure mode
            report.check("TLS handshake completes", False, str(exc))
            return
        except httpx.HTTPError as exc:
            report.check("the stack answers at all", False, str(exc))
            return
        if plain:
            # Said, not skipped: a reader of the report should see there was no TLS to check.
            report.check("plain HTTP connection completes", True, "no TLS (KB_TLS=off)")
        else:
            report.check("TLS handshake completes", True, "certificate trusted by --ca")
        report.check("/health is open without a token", health.status_code == 200,
                     f"HTTP {health.status_code}")

        mcp_anon = anon.post(f"{base}/mcp")
        if no_auth:
            # Any answer but 401 means the request reached the MCP server. An empty POST
            # is not a valid MCP message, so 200 is not expected here; the session checks
            # below are what prove it works.
            report.check("/mcp lets a request with no token through (KB_AUTH=off)",
                         mcp_anon.status_code != 401, f"HTTP {mcp_anon.status_code}")
        else:
            report.check("/mcp refuses an anonymous request", mcp_anon.status_code == 401,
                         f"HTTP {mcp_anon.status_code}")
            report.check("/mcp's 401 names the scheme",
                         mcp_anon.headers.get("www-authenticate", "").startswith("Bearer"),
                         repr(mcp_anon.headers.get("www-authenticate")))

        # Caddy routes /mcp* and /health and nothing else: no converted document is served
        # (tests/test_compose_no_document_route.py), and the internal /auth/check stays
        # off the LAN.
        kb_anon = anon.get(f"{base}/kb/")
        report.check("no document route is served (/kb/)", kb_anon.status_code == 404,
                     f"HTTP {kb_anon.status_code}")
        internal = anon.get(f"{base}/auth/check")
        report.check("/auth/check is not reachable from outside", internal.status_code == 404,
                     f"HTTP {internal.status_code}")

    if token:
        with _client(ca, token) as authed:
            kb_authed = authed.get(f"{base}/kb/")
        report.check("a valid token does not open a document route either",
                     kb_authed.status_code == 404, f"HTTP {kb_authed.status_code}")


# --------------------------------------------------------------------------------------
# MCP over streamable HTTP: the transport an assistant application actually uses.
# --------------------------------------------------------------------------------------


async def _with_session(base: str, ca: str | None, token: str | None, body: Callable) -> Any:
    def factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        verify: Any = ca if ca else True
        return httpx.AsyncClient(
            verify=verify, headers=headers, timeout=timeout, auth=auth, trust_env=False
        )

    async with streamablehttp_client(
        f"{base}/mcp",
        headers=_auth_headers(token),
        httpx_client_factory=factory,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await body(session)


def _payload(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    import json
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return text
    return None


def probe_mcp(report: Report, base: str, ca: str | None, token: str | None,
              query: str) -> None:
    report.section("MCP over streamable HTTP (what an assistant does)")

    async def body(session: ClientSession) -> dict:
        tools = {t.name for t in (await session.list_tools()).tools}
        search = _payload(await session.call_tool("search", {"query": query}))
        out: dict[str, Any] = {"tools": tools, "search": search}
        results = (search or {}).get("results") if isinstance(search, dict) else None
        if results:
            first = results[0]
            out["first"] = first
            ident = first.get("id")
            if ident:
                out["fetch"] = _payload(await session.call_tool("fetch", {"id": ident}))
        return out

    try:
        got = asyncio.run(_with_session(base, ca, token, body))
    except Exception as exc:  # a transport or protocol failure is one failed check, not a crash
        report.check("MCP initialize succeeds", False, f"{type(exc).__name__}: {exc}")
        return

    report.check("MCP initialize succeeds", True, "session established")
    missing = EXPECTED_TOOLS - got["tools"]
    report.check("every expected tool is advertised", not missing,
                 f"missing {sorted(missing)}" if missing else ", ".join(sorted(got["tools"])))

    results = (got.get("search") or {}).get("results") if isinstance(got.get("search"), dict) else None
    if not report.check("search returns at least one result", bool(results),
                        f"query {query!r}"):
        return

    first = got["first"]
    snippet = first.get("snippet") or ""
    fetched = got.get("fetch") or {}
    text = fetched.get("text") if isinstance(fetched, dict) else None
    report.check("fetch resolves the id search returned", bool(text),
                 f"id {first.get('id')!r}")
    if text:
        report.check("fetch returns more than the search snippet", len(text) > len(snippet),
                     f"{len(text)} chars vs a {len(snippet)}-char snippet")

    # `FetchResponse` carries the human-readable citation string under `metadata`. Its
    # top-level `url` is null in the deployment, which serves no documents.
    meta = fetched.get("metadata") if isinstance(fetched, dict) else None
    citation = (meta or {}).get("citation")
    report.check("the result carries a citation", bool(citation), str(citation)[:70])

    source_file = (meta or {}).get("source_file")
    if citation and source_file:
        report.check("the citation names the source document",
                     source_file in citation, f"{source_file!r}")

    url = fetched.get("url") if isinstance(fetched, dict) else None
    report.check("the result carries no link to a document (none is served)",
                 url is None, repr(url))


# --------------------------------------------------------------------------------------
# Revocation, which is the whole reason the store is a file. Needs the store.
# --------------------------------------------------------------------------------------


def probe_revocation(report: Report, base: str, ca: str | None, store: Path,
                     victim: tuple[str, str], bystander: str,
                     cache_seconds: float) -> None:
    report.section("Revocation (one person, no restart)")
    secret, token_id = victim

    def status(secret: str) -> int:
        with _client(ca, secret) as c:
            return c.post(f"{base}/mcp").status_code

    before = status(secret)
    report.check("the victim's token works on /mcp before revocation",
                 before != 401, f"HTTP {before}")

    tokens_module.revoke(store, token_id)

    # The store is re-read at most once per KB_TOKEN_CACHE_SECONDS, so that bound is the
    # contract: wait it out plus a margin rather than racing it.
    deadline = time.monotonic() + cache_seconds + 5.0
    while time.monotonic() < deadline:
        after = status(secret)
        if after == 401:
            break
        time.sleep(0.5)
    took = cache_seconds + 5.0 - (deadline - time.monotonic())

    report.check("revocation closes /mcp", after == 401, f"HTTP {after} after {took:.1f}s")
    report.check("it took no longer than KB_TOKEN_CACHE_SECONDS allows",
                 took <= cache_seconds + 5.0, f"{took:.1f}s, bound {cache_seconds:g}s")

    other = status(bystander)
    report.check("another user's token is untouched by that revocation",
                 other != 401, f"/mcp HTTP {other}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True,
                        help="https://<KB_PUBLIC_HOST> (http:// with KB_TLS=off), "
                             "as a client on the LAN dials it")
    parser.add_argument("--ca", default=None,
                        help="CA bundle to trust (Caddy's root.crt for `tls internal`)")
    parser.add_argument("--tokens-file", default=None, type=Path,
                        help="the store, to issue throwaway tokens and test revocation")
    parser.add_argument("--no-auth", action="store_true",
                        help="the stack runs with KB_AUTH=off: expect /mcp to answer with "
                             "no token, and use none")
    parser.add_argument("--query", default="budget",
                        help="the search query the MCP checks use")
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    cfg = config_module.load()
    report = Report()

    issued: list[str] = []
    store = None if args.no_auth else args.tokens_file
    victim_secret: str | None = None
    bystander_secret = ""
    if args.no_auth:
        # No credential exists to test, and revocation means nothing when a request with
        # no token is let through anyway.
        pass
    elif store is not None:
        victim_secret, victim = tokens_module.issue(store, PROBE_USER, note="http_probe")
        bystander_secret, bystander = tokens_module.issue(store, PROBE_USER, note="http_probe")
        issued = [victim.id, bystander.id]
    else:
        victim_secret = os.environ.get("KB_PROBE_TOKEN", "")
        bystander_secret = os.environ.get("KB_PROBE_TOKEN_2", "")
        if not victim_secret:
            print("set KB_PROBE_TOKEN (and KB_PROBE_TOKEN_2), pass --tokens-file, or pass\n"
                  "--no-auth if the stack runs with KB_AUTH=off", file=sys.stderr)
            return 2

    try:
        probe_routes(report, base, args.ca, victim_secret, args.no_auth)
        probe_mcp(report, base, args.ca, victim_secret, args.query)
        if store is not None and victim_secret:
            probe_revocation(report, base, args.ca, store,
                             (victim_secret, issued[0]), bystander_secret,
                             cfg.kb_token_cache_seconds)
        elif bystander_secret:
            report.section("Revocation")
            print("  SKIP  needs --tokens-file (the store), so it is not run from a client")
    finally:
        # Throwaway credentials never outlive the run, including on a failure.
        if store is not None:
            for token_id in issued:
                try:
                    tokens_module.revoke(store, token_id)
                except Exception:
                    print(f"  WARN  could not revoke probe token {token_id}", file=sys.stderr)

    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main())
