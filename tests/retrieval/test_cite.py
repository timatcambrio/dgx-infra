"""`retrieval.cite.build_citation` (brief §6.5.2)."""

from __future__ import annotations

from retrieval.cite import build_citation


def test_citation_with_pages() -> None:
    citation = build_citation(
        title="Budget Reallocation Form",
        heading_path="Budget Reallocation Form › Carried-Over Federal Funds",
        source_file="budget-form.pdf",
        page_first=3,
        page_last=3,
        doc_date="UNCONFIRMED",
        first_block_id="budget-form:p003:b000",
        last_block_id="budget-form:p003:b004",
    )
    assert citation == (
        "Budget Reallocation Form, Budget Reallocation Form › Carried-Over Federal "
        "Funds (source: budget-form.pdf, pages 3–3, dated UNCONFIRMED; "
        "blocks budget-form:p003:b000…budget-form:p003:b004)"
    )


def test_citation_without_pages_omits_pages_clause() -> None:
    citation = build_citation(
        title="Reference Table",
        heading_path="Reference Table",
        source_file="reference-table.csv",
        page_first=None,
        page_last=None,
        doc_date="UNCONFIRMED",
        first_block_id="reference-table:p000:b000",
        last_block_id="reference-table:p000:b000",
    )
    assert citation == (
        "Reference Table, Reference Table (source: reference-table.csv, "
        "dated UNCONFIRMED; blocks reference-table:p000:b000…reference-table:p000:b000)"
    )
    assert "pages" not in citation
