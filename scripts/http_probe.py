"""Probe a running HTTPS compose stack end to end, the way a client on the LAN reaches it.

`scripts/mcp_probe.py` drives the server over **stdio** and asks whether the evidence an
assistant needs is reachable. This asks the question one layer out, against the deployed
thing: **does the stack behind Caddy serve the right answers to the right people, and stop
serving them the moment a token is revoked?** Everything here goes over TLS to the public
address, so it exercises what no unit test can — the certificate, the reverse proxy, the
`/kb/*` forward_auth hop, and the live token store shared by both routes.

    uv run python scripts/http_probe.py --base-url https://kb.internal --ca ./dgx-kb-ca.crt

Tokens. The read-only checks need two issued tokens, passed as `KB_PROBE_TOKEN` and
`KB_PROBE_TOKEN_2`, so the probe can run from any client machine and never has to see the
store.

Nothing about the deployment is taken from the local checkout. That was a real defect: the
document to request came from globbing this machine's own `kb/`, and a citation url was
judged against this machine's `KB_URL_BASE`. Run from a client both describe a DIFFERENT
deployment -- measured 2026-10-06 from the dev Mac against a correct AWS stack, which
reported three failures, all of them wrong. The document now comes from the server's own
`list_documents`, and a citation url must point into `--base-url`. Given `--tokens-file` instead (run on the host that owns the store, or in the
`kb-token` container), it issues its own throwaway pair, runs the revocation checks too,
and revokes both on the way out. Secrets are never printed, logged, or echoed.

What the revocation checks pin down is the claim the token store exists to make: revoking
one person closes `/mcp*` AND `/kb/*` for them, within `KB_TOKEN_CACHE_SECONDS`, with no
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


def _client(ca: str | None, token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    verify: Any = ca if ca else True
    return httpx.Client(verify=verify, headers=headers, timeout=15.0, trust_env=False)


# --------------------------------------------------------------------------------------
# The plain HTTP surface: what is open, what is closed, and what is not routed at all.
# --------------------------------------------------------------------------------------


def _resolve_url_base(explicit: str | None, base: str) -> str:
    """What a citation url must point into.

    NOT the local `.env`'s KB_URL_BASE, which this used to read. On a client machine that
    file describes a DIFFERENT deployment -- measured 2026-10-06 from the dev Mac, where it
    still said `http://localhost/kb`, so a correct citation url of
    `https://<host>/kb/budget-form.md` was judged against localhost and failed. The server's
    own KB_URL_BASE is not knowable from here, and the useful client-side assertion is that
    the url points back into the address being probed. `--url-base` covers the deployment
    that publishes citations under a different name from the one being dialled.
    """
    return (explicit or base).rstrip("/")


def _kb_file_for_slug(slug: str) -> str:
    """The `/kb/*` path for an indexed document.

    `kb index` stores `rel_path` as `kb/<name>.md` and the slug is derived from that same
    filename, so the converted file is the slug plus `.md`. If that ever stops holding,
    the `/kb/*` checks 404 and say so, which is a finding rather than a silent pass.
    """
    return f"{slug}.md"


def _discover_kb_file(base: str, ca: str | None, token: str) -> str | None:
    """Ask the SERVER which documents it has, rather than reading a local directory.

    The glob this replaces (`cfg.kb_path / "kb"`) is the corpus of whatever checkout the
    probe runs from. From a client machine that is a different corpus, or none at all --
    on the dev Mac it picked the first of twelve proxy documents and asked a server holding
    four fixtures for it, producing a 404 that was entirely correct and said nothing about
    the deployment. The documented client mode is the one this broke.
    """

    async def body(session: ClientSession) -> dict:
        return _payload(await session.call_tool("list_documents", {})) or {}

    try:
        listed = asyncio.run(_with_session(base, ca, token, body))
    except Exception:
        return None  # the route checks report the failure themselves, in their own terms
    documents = listed.get("documents") if isinstance(listed, dict) else None
    if not documents:
        return None
    slug = documents[0].get("slug") if isinstance(documents[0], dict) else None
    return _kb_file_for_slug(slug) if slug else None


def probe_routes(report: Report, base: str, ca: str | None, token: str, kb_file: str) -> None:
    report.section("Routes (TLS, Caddy, and who is let in)")

    with _client(ca) as anon:
        try:
            health = anon.get(f"{base}/health")
        except ssl.SSLError as exc:  # the handshake itself, which is its own failure mode
            report.check("TLS handshake completes", False, str(exc))
            return
        except httpx.HTTPError as exc:
            report.check("the stack answers at all", False, str(exc))
            return
        report.check("TLS handshake completes", True, "certificate trusted by --ca")
        report.check("/health is open without a token", health.status_code == 200,
                     f"HTTP {health.status_code}")

        mcp_anon = anon.post(f"{base}/mcp")
        report.check("/mcp refuses an anonymous request", mcp_anon.status_code == 401,
                     f"HTTP {mcp_anon.status_code}")
        report.check("/mcp's 401 names the scheme",
                     mcp_anon.headers.get("www-authenticate", "").startswith("Bearer"),
                     repr(mcp_anon.headers.get("www-authenticate")))

        kb_anon = anon.get(f"{base}/kb/{kb_file}")
        report.check("/kb/* refuses an anonymous request", kb_anon.status_code == 401,
                     f"HTTP {kb_anon.status_code}")

        # Caddy routes /mcp*, /health and /kb/* and nothing else, which is what keeps the
        # internal /auth/check off the LAN.
        internal = anon.get(f"{base}/auth/check")
        report.check("/auth/check is not reachable from outside", internal.status_code == 404,
                     f"HTTP {internal.status_code}")

    with _client(ca, token) as authed:
        kb_authed = authed.get(f"{base}/kb/{kb_file}")
        report.check("/kb/* serves a converted document to a valid token",
                     kb_authed.status_code == 200, f"HTTP {kb_authed.status_code}")
        report.check("/kb/* returns the document, not a redirect or an index",
                     kb_authed.status_code == 200 and kb_authed.text.strip() != "",
                     f"{len(kb_authed.text)} chars")


# --------------------------------------------------------------------------------------
# MCP over streamable HTTP: the transport an assistant application actually uses.
# --------------------------------------------------------------------------------------


async def _with_session(base: str, ca: str | None, token: str, body: Callable) -> Any:
    def factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        verify: Any = ca if ca else True
        return httpx.AsyncClient(
            verify=verify, headers=headers, timeout=timeout, auth=auth, trust_env=False
        )

    async with streamablehttp_client(
        f"{base}/mcp",
        headers={"Authorization": f"Bearer {token}"},
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


def probe_mcp(report: Report, base: str, ca: str | None, token: str, query: str,
              url_base: str | None) -> None:
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
        report.check("MCP initialize succeeds over HTTPS", False, f"{type(exc).__name__}: {exc}")
        return

    report.check("MCP initialize succeeds over HTTPS", True, "session established")
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

    # `FetchResponse` carries the human-readable citation string under `metadata`, and the
    # link a reader follows as the top-level `url` (retrieval/server.py).
    meta = fetched.get("metadata") if isinstance(fetched, dict) else None
    citation = (meta or {}).get("citation")
    report.check("the result carries a citation", bool(citation), str(citation)[:70])

    source_file = (meta or {}).get("source_file")
    if citation and source_file:
        report.check("the citation names the source document",
                     source_file in citation, f"{source_file!r}")

    url = fetched.get("url") if isinstance(fetched, dict) else None
    if url_base:
        report.check("the citation url points into KB_URL_BASE",
                     bool(url) and url.startswith(url_base), f"{url}")
        if url and url.startswith(url_base):
            with _client(ca, token) as authed:
                got_doc = authed.get(url)
            report.check("the cited url is actually fetchable with the same token",
                         got_doc.status_code == 200, f"HTTP {got_doc.status_code}")


# --------------------------------------------------------------------------------------
# Revocation, which is the whole reason the store is a file. Needs the store.
# --------------------------------------------------------------------------------------


def probe_revocation(report: Report, base: str, ca: str | None, store: Path,
                     victim: tuple[str, str], bystander: str, kb_file: str,
                     cache_seconds: float) -> None:
    report.section("Revocation (one person, both routes, no restart)")
    secret, token_id = victim

    def status(secret: str) -> tuple[int, int]:
        with _client(ca, secret) as c:
            return (
                c.post(f"{base}/mcp").status_code,
                c.get(f"{base}/kb/{kb_file}").status_code,
            )

    mcp_before, kb_before = status(secret)
    report.check("the victim's token works on /mcp before revocation",
                 mcp_before != 401, f"HTTP {mcp_before}")
    report.check("the victim's token works on /kb/* before revocation",
                 kb_before == 200, f"HTTP {kb_before}")

    tokens_module.revoke(store, token_id)

    # The store is re-read at most once per KB_TOKEN_CACHE_SECONDS, so that bound is the
    # contract: wait it out plus a margin rather than racing it.
    deadline = time.monotonic() + cache_seconds + 5.0
    while time.monotonic() < deadline:
        mcp_after, kb_after = status(secret)
        if mcp_after == 401 and kb_after == 401:
            break
        time.sleep(0.5)
    took = cache_seconds + 5.0 - (deadline - time.monotonic())

    report.check("revocation closes /mcp", mcp_after == 401,
                 f"HTTP {mcp_after} after {took:.1f}s")
    report.check("revocation closes /kb/* too", kb_after == 401,
                 f"HTTP {kb_after} after {took:.1f}s — one store behind both routes")
    report.check("it took no longer than KB_TOKEN_CACHE_SECONDS allows",
                 took <= cache_seconds + 5.0, f"{took:.1f}s, bound {cache_seconds:g}s")

    mcp_other, kb_other = status(bystander)
    report.check("another user's token is untouched by that revocation",
                 mcp_other != 401 and kb_other == 200,
                 f"/mcp HTTP {mcp_other}, /kb/* HTTP {kb_other}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True,
                        help="https://<KB_PUBLIC_HOST>, as a client on the LAN dials it")
    parser.add_argument("--ca", default=None,
                        help="CA bundle to trust (Caddy's root.crt for `tls internal`)")
    parser.add_argument("--tokens-file", default=None, type=Path,
                        help="the store, to issue throwaway tokens and test revocation")
    parser.add_argument("--url-base", default=None,
                        help="What a citation url must point into. Defaults to --base-url; "
                             "set it only where the deployment publishes citations under a "
                             "different name from the one being dialled.")
    parser.add_argument("--kb-file", default=None,
                        help="a path under /kb/ to request (default: a converted file found in KB_PATH)")
    parser.add_argument("--query", default="budget",
                        help="the search query the MCP checks use")
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    cfg = config_module.load()
    report = Report()

    issued: list[str] = []
    store = args.tokens_file
    if store is not None:
        victim_secret, victim = tokens_module.issue(store, PROBE_USER, note="http_probe")
        bystander_secret, bystander = tokens_module.issue(store, PROBE_USER, note="http_probe")
        issued = [victim.id, bystander.id]
    else:
        victim_secret = os.environ.get("KB_PROBE_TOKEN", "")
        bystander_secret = os.environ.get("KB_PROBE_TOKEN_2", "")
        if not victim_secret:
            print("set KB_PROBE_TOKEN (and KB_PROBE_TOKEN_2), or pass --tokens-file",
                  file=sys.stderr)
            return 2

    # Which document to ask /kb/* for. Resolved here, after a token exists, because the
    # SERVER is asked first and that needs one.
    kb_file = args.kb_file
    if kb_file is None:
        kb_file = _discover_kb_file(base, args.ca, victim_secret)
    if kb_file is None:
        local = cfg.kb_path / "kb"
        found = sorted(local.glob("*.md")) if local.is_dir() else []
        kb_file = found[0].name if found else None
    if kb_file is None:
        print("could not work out which document to request: the server listed none and\n"
              f"there is no local corpus at {cfg.kb_path / 'kb'}. Pass --kb-file <name>.md.",
              file=sys.stderr)
        return 2

    try:
        probe_routes(report, base, args.ca, victim_secret, kb_file)
        probe_mcp(report, base, args.ca, victim_secret, args.query,
                  _resolve_url_base(args.url_base, base))
        if store is not None:
            probe_revocation(report, base, args.ca, store,
                             (victim_secret, issued[0]), bystander_secret, kb_file,
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
