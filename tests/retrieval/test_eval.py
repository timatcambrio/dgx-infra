"""`kb eval` (brief §6.7) against the fixture cases and the indexed fixtures.

Reuses `test_search.py`'s session-scoped `indexed_dsn` fixture (and its `_with_conn`/
`_embed_fn` helpers) so the fixtures are indexed only once and every connection stays
inside the event loop that opened it (see that module's docstring).
"""

from __future__ import annotations

from pathlib import Path

import asyncpg
from test_search import _embed_fn, _run, _with_conn  # noqa: F401
from test_search import indexed_dsn  # noqa: F401 -- pytest fixture

from retrieval import eval as eval_module

CASES_PATH = Path(__file__).resolve().parent.parent.parent / "eval" / "retrieval.yaml"


def test_fused_hit_at_5_is_100_percent_on_the_fixture_cases(indexed_dsn: str) -> None:
    cases = eval_module.load_cases(CASES_PATH)
    assert len(cases) >= 8

    async def _go(conn: asyncpg.Connection):
        return await eval_module.run_eval(conn, cases, k=5, embed_query_fn=_embed_fn)

    result = _run(indexed_dsn, _go)

    assert result.rate("fused") == 1.0
    assert len(result.table_lines) == len(cases) + 1  # header + one row per case


def test_wrong_expected_slug_reports_a_dash(indexed_dsn: str) -> None:
    bad_case = eval_module.EvalCase(
        id="deliberately-wrong",
        question="FORM-7731",
        expected_slug="not-a-real-document",
    )

    async def _go(conn: asyncpg.Connection):
        return await eval_module.run_eval(conn, [bad_case], k=5, embed_query_fn=_embed_fn)

    result = _run(indexed_dsn, _go)
    assert result.cases[0].ranks == {"lexical": None, "vector": None, "fused": None}
    line = result.table_lines[1]
    assert "-" in line
    assert result.rate("lexical") == 0.0
