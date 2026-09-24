"""Check a `kb eval` cases file before you trust its numbers.

`kb eval` scores a case as a miss whenever ANY of `expected_slug`, `expected_page` and
`expected_phrase` fails -- and all three must be satisfied by the SAME section. That makes
two very different things look identical in the table: retrieval missed, or the case asked
for something no single section can satisfy. This script separates them, so a case file is
a floor for the retriever rather than for the sectioniser.

    uv run python scripts/check_eval_cases.py --cases /path/to/cases.yaml

Read-only: `DATABASE_URL` (the SELECT-only role) plus the kb/ markdown. Exit 1 if any case
is unsatisfiable, 0 otherwise (over-constrained cases are warnings, not failures).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from retrieval import config as config_module  # noqa: E402

SECTIONS_SQL = """
    SELECT s.section_index, s.page_first, s.page_last, s.heading_path,
           string_agg(b.text, E'\n' ORDER BY b.ordinal) AS txt
    FROM sections s
    JOIN blocks b ON b.slug = s.slug AND b.ordinal BETWEEN s.block_first AND s.block_last
    WHERE s.slug = $1
    GROUP BY s.section_index, s.page_first, s.page_last, s.heading_path
    ORDER BY s.section_index
"""


async def check(cases_path: Path) -> int:
    cfg = config_module.load()
    data = yaml.safe_load(cases_path.read_text(encoding="utf-8")) or {}
    cases = data.get("cases", [])
    if not cases:
        print(f"no cases in {cases_path}")
        return 1

    conn = await asyncpg.connect(cfg.database_url)
    try:
        known = {r["slug"] for r in await conn.fetch("SELECT slug FROM documents")}
        cache: dict[str, list[asyncpg.Record]] = {}
        errors = warnings = 0

        for case in cases:
            cid = case.get("id", "<no id>")
            slug = case.get("expected_slug")
            page = case.get("expected_page")
            phrase = case.get("expected_phrase")
            problems: list[str] = []
            notes: list[str] = []

            if slug not in known:
                print(f"FAIL  {cid}: expected_slug {slug!r} is not indexed")
                errors += 1
                continue

            if slug not in cache:
                cache[slug] = await conn.fetch(SECTIONS_SQL, slug)
            sections = cache[slug]

            on_page = [
                s for s in sections
                if page is not None
                and s["page_first"] is not None
                and s["page_first"] <= page <= s["page_last"]
            ]
            with_phrase = [
                s for s in sections
                if phrase is not None and phrase.lower() in (s["txt"] or "").lower()
            ]

            if page is not None and not on_page:
                problems.append(f"no section covers page {page}")
            if phrase is not None and not with_phrase:
                problems.append(f"phrase {phrase!r} is in no section")

            # The trap: each constraint is satisfiable alone, but not together.
            if page is not None and phrase is not None and on_page and with_phrase:
                both = {s["section_index"] for s in on_page} & {
                    s["section_index"] for s in with_phrase
                }
                if not both:
                    pages = sorted({s["page_first"] for s in with_phrase})
                    problems.append(
                        f"OVER-CONSTRAINED: no single section has both page {page} and the "
                        f"phrase (phrase is in section(s) "
                        f"{sorted(s['section_index'] for s in with_phrase)} on page(s) {pages}); "
                        f"drop one constraint or split into two cases"
                    )

            if phrase is not None and len(with_phrase) > 3:
                notes.append(f"phrase appears in {len(with_phrase)} sections — weak evidence")
            if page is not None and len(on_page) > 6:
                notes.append(
                    f"page {page} is split across {len(on_page)} sections — often a "
                    f"flattened table; check the conversion"
                )

            if problems:
                print(f"FAIL  {cid}: " + "; ".join(problems))
                errors += 1
            elif notes:
                print(f"WARN  {cid}: " + "; ".join(notes))
                warnings += 1
            else:
                print(f"ok    {cid}")

        print(f"\n{len(cases)} cases: {errors} unsatisfiable, {warnings} warnings")
        return 1 if errors else 0
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", required=True, type=Path)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(check(args.cases)))


if __name__ == "__main__":
    main()
