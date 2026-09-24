"""Probe the MCP server's contract, end to end over stdio.

`kb eval` asks "did the right section rank top-5?" against the database. This asks a
different question: **is the evidence an assistant needs actually reachable through the MCP
tools, with an honest citation?** It launches `kb serve --transport stdio` as a subprocess,
exactly as an assistant application would, and drives the real tools.

    uv run python scripts/mcp_probe.py --cases /path/to/mcp-cases.yaml

What it checks mechanically, per case:

  tools      all five expected tools are advertised (once, at startup)
  search     returns at least one result, and the expected slug is among them
  fetch      resolves the expected id and returns MORE than the search snippet
  evidence   expected_phrase is present in the FETCHED text -- reachable, not just indexed
  citation   non-empty, names the source file, and the url starts with KB_URL_BASE
  neighbours get_section(id, neighbours=1) is a superset of fetch(id)

What it deliberately does NOT check: whether an assistant's prose answer is correct, or
whether it chose to call `fetch` before quoting. Those are properties of the assistant, not
of this server, and grading them automatically would need an LLM judge. Cases carry a
`requires:` list of such behaviours; the probe prints them as a checklist, and for a case
marked `expect_damage: true` it prints the fetched text so you can confirm the damage is
visible to a reader at all.

Read-only. Needs the same environment `kb serve` needs: DATABASE_URL, OLLAMA_BASE_URL,
KB_URL_BASE, and a populated index.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from retrieval import config as config_module  # noqa: E402

EXPECTED_TOOLS = {"search", "fetch", "list_documents", "get_outline", "get_section"}


def _payload(result: Any) -> Any:
    """FastMCP returns typed results as structuredContent; fall back to parsing text."""
    sc = getattr(result, "structuredContent", None)
    if sc:
        return sc
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return None


class Probe:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def check(self, ok: bool, case: str, name: str, detail: str = "") -> bool:
        self.checks += 1
        if not ok:
            self.failures.append(f"{case}: {name}{(' — ' + detail) if detail else ''}")
        return ok


async def run(cases_path: Path, server_log: Optional[Path] = None) -> int:
    cfg = config_module.load()
    cases = (yaml.safe_load(cases_path.read_text(encoding="utf-8")) or {}).get("cases", [])
    if not cases:
        print(f"no cases in {cases_path}")
        return 1

    kb = Path(sys.executable).parent / "kb"
    params = StdioServerParameters(
        command=str(kb), args=["serve", "--transport", "stdio"], cwd=str(REPO_ROOT)
    )
    p = Probe()
    manual: list[tuple[str, list[str]]] = []

    log_path = server_log or Path("mcp-probe-server.log")
    errlog = log_path.open("w", encoding="utf-8")
    print(f"server call log -> {log_path}\n")
    async with stdio_client(params, errlog=errlog) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            names = {t.name for t in (await session.list_tools()).tools}
            missing = EXPECTED_TOOLS - names
            p.check(not missing, "<startup>", "all five tools advertised", f"missing {missing}")
            print(f"tools advertised: {', '.join(sorted(names))}\n")

            for case in cases:
                cid = case.get("id", "<no id>")
                question = case["question"]
                slug = case.get("expected_slug")
                want_id = case.get("expected_id")
                phrase = case.get("expected_phrase")
                print(f"--- {cid}")
                print(f"    Q: {question}")

                sr = _payload(await session.call_tool("search", {"query": question, "k": 8}))
                results = (sr or {}).get("results", [])
                if not p.check(bool(results), cid, "search returned results"):
                    print("    search: NO RESULTS\n")
                    continue

                ids = [r["id"] for r in results]
                print(f"    search -> {ids[0]}" + (f" (+{len(ids) - 1} more)" if len(ids) > 1 else ""))
                if slug:
                    hit = [i for i in ids if i.split(":")[1] == slug] if ids else []
                    p.check(bool(hit), cid, f"expected slug {slug} in search results",
                            f"got {[i.split(':')[1] for i in ids[:5]]}")

                # An assistant fetches the promising one or two, not only the top hit,
                # so "reachable" is judged over the first few results for the right slug.
                candidates = [want_id] if want_id else ((hit[:3] if slug and hit else ids[:1]))
                target, fr = None, None
                for cand in candidates:
                    got = _payload(await session.call_tool("fetch", {"id": cand}))
                    target, fr = cand, got
                    if not phrase or (got and phrase.lower() in (got.get("text") or "").lower()):
                        break
                if not p.check(bool(fr and fr.get("text")), cid, f"fetch({target}) returned text"):
                    print()
                    continue

                text = fr["text"]
                meta = fr.get("metadata") or {}
                snippet = next((r["snippet"] for r in results if r["id"] == target), "")
                p.check(len(text) > len(snippet), cid, "fetch returns more than the snippet",
                        f"fetch {len(text)} chars vs snippet {len(snippet)}")

                citation = meta.get("citation") or ""
                p.check(bool(citation), cid, "citation present")
                src = meta.get("source_file")
                if citation and src:
                    p.check(src in citation, cid, "citation names the source file", f"{src!r}")
                url = fr.get("url") or ""
                p.check(url.startswith(cfg.kb_url_base), cid, "url is under KB_URL_BASE", url[:80])

                if phrase:
                    p.check(phrase.lower() in text.lower(), cid,
                            f"evidence {phrase!r} reachable in fetched text")

                gs = _payload(await session.call_tool(
                    "get_section", {"id": target, "neighbours": 1}))
                if gs and gs.get("text"):
                    p.check(len(gs["text"]) >= len(text), cid,
                            "get_section(neighbours=1) is a superset of fetch")

                print(f"    fetch  -> {len(text)} chars | citation: {citation[:70]}...")

                if case.get("expect_damage"):
                    print("    ! expect_damage — fetched text for your inspection:")
                    for line in text.splitlines()[:14]:
                        print(f"      | {line[:100]}")
                if case.get("requires"):
                    manual.append((cid, case["requires"]))
                print()

    print("=" * 72)
    print(f"{p.checks} mechanical checks, {len(p.failures)} failed")
    for f in p.failures:
        print(f"  FAIL  {f}")

    errlog.close()

    if manual:
        print("\nAssistant behaviours to judge by hand (ask these through your assistant):")
        for cid, reqs in manual:
            print(f"  {cid}")
            for r in reqs:
                print(f"    [ ] {r}")

    return 1 if p.failures else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument(
        "--server-log",
        type=Path,
        help="Write the server's stderr (its one-JSON-line-per-call log) here instead of "
             "interleaving it with this report. Defaults to mcp-probe-server.log.",
    )
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.cases, args.server_log)))


if __name__ == "__main__":
    main()
