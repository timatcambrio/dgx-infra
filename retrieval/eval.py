"""`kb eval` — retrieval regression harness (brief §6.7).

Metric: **section hit@k** — a case passes for a leg if any of that leg's top-k sections has
`slug == expected_slug` and satisfies every optional condition given (`expected_phrase` in
the section's full text, case-insensitively; `expected_page` within the section's page
range). Exit 0 always: the number is recorded, not enforced, except in the S2 tests where
the fixture cases must all hit. Never tune `k`, chunk sizes, or the RRF constant to chase
this (brief hard rule 7) — plant a more distinctive token in the fixture, or fix a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import asyncpg
import yaml

from .search import SectionHit, search_legs

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES_PATH = REPO_ROOT / "eval" / "retrieval.yaml"
LEGS = ("lexical", "vector", "fused")


@dataclass
class EvalCase:
    id: str
    question: str
    expected_slug: str
    expected_phrase: Optional[str] = None
    expected_page: Optional[int] = None


@dataclass
class CaseResult:
    case_id: str
    #: leg name -> 1-based rank of the first passing section, or None.
    ranks: dict[str, Optional[int]]


@dataclass
class EvalResult:
    cases: list[CaseResult]

    def rate(self, leg: str) -> float:
        if not self.cases:
            return 0.0
        hits = sum(1 for c in self.cases if c.ranks[leg] is not None)
        return hits / len(self.cases)

    @property
    def table_lines(self) -> list[str]:
        header = f"{'case id':30}  {'lexical':>7}  {'vector':>7}  {'fused':>7}"
        lines = [header]

        def fmt(rank: Optional[int]) -> str:
            return str(rank) if rank is not None else "-"

        for c in self.cases:
            lines.append(
                f"{c.case_id:30}  {fmt(c.ranks['lexical']):>7}  "
                f"{fmt(c.ranks['vector']):>7}  {fmt(c.ranks['fused']):>7}"
            )
        return lines


def load_cases(path: Path) -> list[EvalCase]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cases = []
    for raw in data.get("cases", []):
        cases.append(
            EvalCase(
                id=raw["id"],
                question=raw["question"],
                expected_slug=raw["expected_slug"],
                expected_phrase=raw.get("expected_phrase"),
                expected_page=raw.get("expected_page"),
            )
        )
    return cases


async def _section_text(conn: asyncpg.Connection, slug: str, block_first: int, block_last: int) -> str:
    rows = await conn.fetch(
        "SELECT text FROM blocks WHERE slug = $1 AND ordinal BETWEEN $2 AND $3 ORDER BY ordinal",
        slug,
        block_first,
        block_last,
    )
    return "\n\n".join(r["text"] for r in rows)


async def _first_passing_rank(
    conn: asyncpg.Connection, hits: list[SectionHit], case: EvalCase
) -> Optional[int]:
    for i, h in enumerate(hits, start=1):
        if h.slug != case.expected_slug:
            continue
        if case.expected_page is not None:
            if h.page_first is None or h.page_last is None:
                continue
            if not (h.page_first <= case.expected_page <= h.page_last):
                continue
        if case.expected_phrase is not None:
            text = await _section_text(conn, h.slug, h.block_first, h.block_last)
            if case.expected_phrase.lower() not in text.lower():
                continue
        return i
    return None


async def run_eval(
    conn: asyncpg.Connection,
    cases: list[EvalCase],
    *,
    k: int = 5,
    embed_query_fn: Callable[[str], list[float]],
) -> EvalResult:
    results: list[CaseResult] = []
    for case in cases:
        legs = await search_legs(
            conn, case.question, k=k, filters=None, embed_query_fn=embed_query_fn, legs=LEGS
        )
        ranks = {leg: await _first_passing_rank(conn, legs[leg], case) for leg in LEGS}
        results.append(CaseResult(case_id=case.id, ranks=ranks))
    return EvalResult(cases=results)
