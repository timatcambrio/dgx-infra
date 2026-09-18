"""Dotted leaders (a table of contents' rows of dots) are typography, not content.

A run of four or more dots collapses to one ellipsis, and a paragraph that holds several
"title … page" entries is broken into one entry per line. Both are applied by
`normalize_markdown`, so every converter benefits and the result is deterministic.
"""

from __future__ import annotations

import re

from pipeline.convert import convert_entry
from pipeline.converters import normalize_markdown

LEADER_RUN = re.compile(r"\.{4,}")


def test_leader_runs_collapse_to_one_ellipsis():
    assert normalize_markdown("Scope ........ 6") == "Scope … 6"
    assert normalize_markdown("Scope . . . . . . 6") == "Scope … 6"
    assert normalize_markdown("Scope.......6") == "Scope … 6"


def test_three_dots_and_decimals_are_left_alone():
    assert normalize_markdown("Wait for it... then go.") == "Wait for it... then go."
    assert normalize_markdown("Version 1.2.3.4 shipped.") == "Version 1.2.3.4 shipped."


def test_a_joined_table_of_contents_becomes_one_entry_per_line():
    joined = (
        "Section 1 Governance .......... 5 1.1 Purpose ........5 1.2 Scope ...... 6 "
        "Appendix A Forms ........ iv Appendix B Contacts ...... vi"
    )
    out = normalize_markdown(joined)
    assert out.split("\n") == [
        "Section 1 Governance … 5",
        "1.1 Purpose … 5",
        "1.2 Scope … 6",
        "Appendix A Forms … iv",
        "Appendix B Contacts … vi",
    ]


def test_fewer_than_three_entries_are_not_split():
    # Two leader entries in ordinary prose are not a table of contents.
    text = "See Fees ........ 12 and Refunds ........ 14 for details."
    assert "\n" not in normalize_markdown(text)
    assert normalize_markdown(text) == "See Fees … 12 and Refunds … 14 for details."


def test_normalisation_is_idempotent():
    joined = "A ...... 1 B ...... 2 C ...... 3 D ...... 4"
    once = normalize_markdown(joined)
    assert normalize_markdown(once) == once


def test_toc_fixture_converts_to_one_entry_per_line(config, entry_for):
    result = convert_entry(entry_for("toc_page.pdf"), config)
    assert result.output is not None, result.message
    text = result.output.read_text(encoding="utf-8")
    assert not LEADER_RUN.search(text), "a leader run survived conversion"
    entry_lines = [line for line in text.splitlines() if " … " in line]
    assert len(entry_lines) == 12, entry_lines
    assert entry_lines[0].startswith("Section 1 Governance … 5")
    assert entry_lines[-1] == "Appendix B Contacts … vi"
