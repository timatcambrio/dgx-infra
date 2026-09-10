"""The coverage and conversion report — M1's actual deliverable.

The numbers this prints are a decision gate: they size the OCR problem before anyone commits
to an OCR model. So the report states the thresholds it used alongside the results, and it
never rounds a bad number into a good-looking one.

The STOP-AND-ASK section exists so a run that turned up something needing a human decision
cannot be mistaken for a clean run that happened to scroll past.
"""

from __future__ import annotations

import json
from typing import Any

from .config import Config
from .manifest import (
    STATUS_MISSING,
    TEXT_CLASS_CLEAN,
    TEXT_CLASS_ERROR,
    TEXT_CLASS_NEEDS_OCR,
    TEXT_CLASS_PARTIAL,
)

#: Above this fraction of documents needing OCR, the premise of a text-layer-first phase is
#: inverted and that is a decision for a human, not a threshold to tune.
NEEDS_OCR_ALARM_FRACTION = 0.30

_CLASS_ORDER = (
    TEXT_CLASS_CLEAN,
    TEXT_CLASS_PARTIAL,
    TEXT_CLASS_NEEDS_OCR,
    TEXT_CLASS_ERROR,
)


def build(manifest: dict[str, Any], config: Config) -> dict[str, Any]:
    """Assemble the report as plain data, so table and JSON rendering share one source."""
    documents = manifest.get("documents", [])
    rows: list[dict[str, Any]] = []

    for entry in documents:
        triage = entry.get("triage") or {}
        missing = entry.get("status") == STATUS_MISSING
        rows.append(
            {
                "source_file": entry.get("source_file"),
                "source_format": entry.get("source_format"),
                "status": entry.get("status"),
                "page_count": triage.get("page_count"),
                "chars_per_page_median": triage.get("chars_per_page_median"),
                "chars_per_page_mean": triage.get("chars_per_page_mean"),
                "alpha_ratio": triage.get("alpha_ratio"),
                "low_page_fraction": triage.get("low_page_fraction"),
                "low_pages": triage.get("low_pages") or [],
                "max_columns": triage.get("max_columns"),
                "ruled_tables": triage.get("ruled_tables"),
                "borderless_table_pages": triage.get("borderless_table_pages"),
                "text_class": "MISSING" if missing else triage.get("text_class"),
                "error": triage.get("error"),
                "converter": (entry.get("conversion") or {}).get("converter"),
            }
        )

    triaged = [
        row
        for row in rows
        if row["status"] != STATUS_MISSING and row["text_class"] in _CLASS_ORDER
    ]
    class_counts = {
        text_class: sum(1 for row in triaged if row["text_class"] == text_class)
        for text_class in _CLASS_ORDER
    }
    page_counts = {
        text_class: sum(
            row["page_count"] or 0
            for row in triaged
            if row["text_class"] == text_class
        )
        for text_class in _CLASS_ORDER
    }

    total_docs = len(triaged)
    total_pages = sum(page_counts.values())
    needs_ocr_docs = class_counts[TEXT_CLASS_NEEDS_OCR]
    needs_ocr_pages = page_counts[TEXT_CLASS_NEEDS_OCR]
    low_pages_total = sum(len(row.get("low_pages") or []) for row in triaged)

    totals = {
        "documents_in_manifest": len(rows),
        "documents_triaged": total_docs,
        "documents_missing": sum(1 for row in rows if row["status"] == STATUS_MISSING),
        "documents_untriaged": len(rows)
        - total_docs
        - sum(1 for row in rows if row["status"] == STATUS_MISSING),
        "pages_total": total_pages,
        "by_class": class_counts,
        "pages_by_class": page_counts,
        "needs_ocr_document_fraction": (
            round(needs_ocr_docs / total_docs, 4) if total_docs else None
        ),
        "needs_ocr_page_fraction": (
            round(needs_ocr_pages / total_pages, 4) if total_pages else None
        ),
        "low_pages_total": low_pages_total,
        "low_page_fraction_corpus": (
            round(low_pages_total / total_pages, 4) if total_pages else None
        ),
    }

    return {
        "thresholds": {
            "MIN_CHARS_PER_PAGE": config.min_chars_per_page,
            "MIN_ALPHA_RATIO": config.min_alpha_ratio,
            "MAX_LOW_PAGE_FRACTION": config.max_low_page_fraction,
        },
        "documents": rows,
        "totals": totals,
        "skipped_extensions": manifest.get("skipped_extensions") or {},
        "stop_and_ask": _stop_and_ask(rows, totals, manifest),
    }


def _stop_and_ask(
    rows: list[dict[str, Any]], totals: dict[str, Any], manifest: dict[str, Any]
) -> list[str]:
    items: list[str] = []

    fraction = totals["needs_ocr_document_fraction"]
    if fraction is not None and fraction > NEEDS_OCR_ALARM_FRACTION:
        items.append(
            f"{fraction:.1%} of triaged documents classify needs_ocr, above the "
            f"{NEEDS_OCR_ALARM_FRACTION:.0%} alarm line. This inverts the premise of a "
            "text-layer-first phase and is a decision to take before continuing, not after."
        )

    for row in rows:
        if row["text_class"] == TEXT_CLASS_ERROR:
            items.append(
                f"{row['source_file']}: unreadable — {row['error'] or 'no detail'}. "
                "Encrypted or corrupt sources need a human decision."
            )

    missing = [row["source_file"] for row in rows if row["status"] == STATUS_MISSING]
    if missing:
        items.append(
            f"{len(missing)} manifest entr{'y' if len(missing) == 1 else 'ies'} point at "
            f"files no longer in SOURCE_DIR: {', '.join(sorted(missing)[:5])}"
            + (" ..." if len(missing) > 5 else "")
        )

    skipped = manifest.get("skipped_extensions") or {}
    if skipped:
        listed = ", ".join(f"{ext} ({count})" for ext, count in sorted(skipped.items()))
        items.append(
            f"Unsupported formats present in SOURCE_DIR and not converted: {listed}. "
            "Confirm none of these matter before treating the corpus as covered."
        )

    return items


def render_table(report: dict[str, Any]) -> str:
    """Fixed-width text table. No colour, no unicode box drawing — this gets pasted around."""
    thresholds = report["thresholds"]
    lines = [
        "TEXT-LAYER COVERAGE REPORT",
        "",
        "Thresholds: "
        + ", ".join(f"{key}={value}" for key, value in thresholds.items()),
        "",
    ]

    header = (
        f"{'SOURCE FILE':<44} {'FMT':<5} {'PAGES':>5} {'MED C/PG':>8} "
        f"{'ALPHA':>6} {'LOW%':>6}  CLASS"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for row in sorted(report["documents"], key=lambda item: item["source_file"] or ""):
        source = row["source_file"] or ""
        if len(source) > 44:
            source = "..." + source[-41:]
        lines.append(
            f"{source:<44} {(row['source_format'] or ''):<5} "
            f"{_num(row['page_count'], 0):>5} {_num(row['chars_per_page_median'], 1):>8} "
            f"{_num(row['alpha_ratio'], 3):>6} {_pct(row['low_page_fraction']):>6}  "
            f"{row['text_class'] or 'untriaged'}"
        )

    totals = report["totals"]
    lines += [
        "",
        "CORPUS TOTALS",
        f"  documents in manifest : {totals['documents_in_manifest']}",
        f"  documents triaged     : {totals['documents_triaged']}",
        f"  documents missing     : {totals['documents_missing']}",
        f"  documents untriaged   : {totals['documents_untriaged']}",
        f"  pages total           : {totals['pages_total']}",
        "",
        "  by class (documents / pages)",
    ]
    for text_class in _CLASS_ORDER:
        lines.append(
            f"    {text_class:<11}: {totals['by_class'][text_class]:>4} / "
            f"{totals['pages_by_class'][text_class]:>5}"
        )

    lines += [
        "",
        f"  needs_ocr, documents  : {_pct(totals['needs_ocr_document_fraction'])}",
        f"  needs_ocr, pages      : {_pct(totals['needs_ocr_page_fraction'])}",
        f"  low-text pages        : {totals['low_pages_total']} of "
        f"{totals['pages_total']} ({_pct(totals['low_page_fraction_corpus'])})",
    ]

    # Layout facts decide whether model-free geometric extraction is enough for a document,
    # or whether it should be escalated to a layout model. Reported rather than acted on:
    # escalation is a deliberate choice, since it costs an ML runtime and a model download.
    multi_column = [row for row in report["documents"] if (row.get("max_columns") or 1) > 1]
    borderless = [row for row in report["documents"] if row.get("borderless_table_pages")]
    if multi_column or borderless:
        lines += ["", "LAYOUT NOTES (which documents geometry may struggle with)"]
        for row in sorted(multi_column, key=lambda item: item["source_file"] or ""):
            lines.append(
                f"  {row['source_file']}: {row['max_columns']} columns detected -- check "
                "reading order in the converted output"
            )
        for row in sorted(borderless, key=lambda item: item["source_file"] or ""):
            lines.append(
                f"  {row['source_file']}: {row['borderless_table_pages']} page(s) with "
                "borderless tables -- recovered by column alignment, worth spot-checking"
            )

    # A document can be `clean` overall and still hold individual pages with no usable text.
    # Those pages are where content goes missing silently, so name them: a cover or a
    # divider is nothing to worry about, a full-page scanned figure is.
    with_low_pages = [
        row
        for row in sorted(report["documents"], key=lambda item: item["source_file"] or "")
        if row.get("low_pages")
    ]
    if with_low_pages:
        lines += [
            "",
            "LOW-TEXT PAGES (under MIN_CHARS_PER_PAGE, inside otherwise-usable documents)",
            "  Check these by eye: a cover or divider is fine, a scanned figure is content",
            "  that will be missing from kb/.",
        ]
        for row in with_low_pages:
            pages = ", ".join(str(number) for number in row["low_pages"][:20])
            if len(row["low_pages"]) > 20:
                pages += ", ..."
            lines.append(f"  {row['source_file']}")
            lines.append(f"    page(s): {pages}")

    if report["stop_and_ask"]:
        lines += ["", "=" * 78, "STOP AND ASK", "=" * 78]
        for item in report["stop_and_ask"]:
            lines.append(f"  * {item}")

    return "\n".join(lines)


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def _num(value: Any, places: int) -> str:
    if value is None:
        return "-"
    return f"{value:.{places}f}" if places else f"{value}"


def _pct(value: Any) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"
