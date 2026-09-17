#!/usr/bin/env python3
"""Generate `tests/retrieval/fixtures/kb/` (brief §8.1): synthetic, seeded, nonsense-but-
searchable `kb/` documents in Stage 1's exact contract. Content is never read from a real
document — every word here comes from a fixed word list and a seeded RNG.

Deterministic: re-running this script must produce byte-identical output (`make
fixtures-retrieval` is checked for a no-op diff). Frontmatter is written through
`pipeline.frontmatter.build`/`render` so the contract cannot drift out from under the
fixtures.

Four documents (brief §8.1):
  - `handbook`       — 40 "pages", ~60k chars, headings at levels 1-4, two tables (one
                        opening a section, one oversized), one oversized paragraph.
  - `budget-form`     — 6 pages, ~12k chars, ten annotation blocks, six boxed_text blocks,
                        an INCOMPLETE callout, and the planted tokens `FORM-7731` and
                        `carry over` used by the retrieval eval fixtures (S2).
  - `deck`            — 30 pages, ~15k chars, one `###` heading per page, 2-3 blocks each.
  - `reference-table` — a CSV-style document with no sidecar and no block anchors (§5.3
                        fallback path), one markdown table.

`expected.json` is derived from what this script actually generates (never hand-typed), so
it cannot drift from the fixtures it describes. Section/chunk counts are not recorded yet —
the chunker (`retrieval/chunk.py`) is built in S1.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from pipeline.frontmatter import build as fm_build  # noqa: E402
from pipeline.frontmatter import render as fm_render  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "kb"

SEED = "dgx-stage2-fixtures-20260917"

#: A fixed, nonsense word list. Every generated sentence draws from this list and nothing
#: else — no real document text ever enters these fixtures.
WORDS = (
    "zynth quorval bexnit drommel fenwick glarous humbolt ivrixal jonquel klaxor "
    "lumbric morvane nixplat oswent parvix quexel rimstal svorna tulgren uxbane "
    "vantrix wexlar yorbin zathum brindle corvel dansith eluvor florak grentin "
    "hallux implor juxtane kelbron limnara mordax nuvalen orquist plenith raxvel "
    "sembrik torvane umblist vexrona wisplor xandric yellum zorvath abnitor "
    "clavendish delquor entrivan"
).split()

CALLOUT_TOKEN = "FORM-7731"
CARRY_OVER = "carry over"


def rng_for(slug: str) -> random.Random:
    return random.Random(f"{SEED}:{slug}")


def word(rng: random.Random) -> str:
    return rng.choice(WORDS)


def sentence(rng: random.Random, n_words: int = 12) -> str:
    words = [word(rng) for _ in range(n_words)]
    text = " ".join(words)
    return text[0].upper() + text[1:] + "."


def paragraph(rng: random.Random, n_sentences: int = 4) -> str:
    return " ".join(sentence(rng, rng.randint(8, 16)) for _ in range(n_sentences))


def bullet_list(rng: random.Random, n_items: int = 4) -> str:
    return "\n".join(f"- {sentence(rng, rng.randint(4, 8))}" for _ in range(n_items))


def table(rng: random.Random, rows: int, cols: int, plant: str | None = None) -> str:
    header = [f"Column {c + 1}" for c in range(cols)]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * cols) + " |"]
    for r in range(rows):
        cells = [word(rng) for _ in range(cols)]
        if plant is not None and r == 0:
            cells[0] = plant
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


class DocBuilder:
    """Collects (page, kind, text) blocks and renders them into the §5.1 contract."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.blocks: list[dict] = []

    def add(self, page: int | None, kind: str, text: str) -> None:
        self.blocks.append({"page": page, "kind": kind, "text": text})

    def total_chars(self) -> int:
        return sum(len(b["text"]) for b in self.blocks)

    def render(
        self,
        *,
        title: str,
        source_format: str,
        text_class: str,
        has_sidecar: bool = True,
        with_anchors: bool = True,
    ) -> dict:
        body_parts: list[str] = []
        sidecar_blocks: list[dict] = []
        per_page_counter: dict[int, int] = {}

        for blk in self.blocks:
            page = blk["page"]
            key = page if page is not None else 0
            n = per_page_counter.get(key, 0)
            per_page_counter[key] = n + 1
            page_component = page if page is not None else 0
            block_id = f"{self.slug}:p{page_component:03d}:b{n:03d}"
            if with_anchors:
                body_parts.append(f"<!-- dgx:block={block_id} -->")
            body_parts.append(blk["text"])
            body_parts.append("")
            sidecar_blocks.append(
                {
                    "block_id": block_id,
                    "page": page,
                    "kind": blk["kind"],
                    "confidence": "geometry",
                }
            )

        body = "\n".join(body_parts).strip("\n") + "\n"
        content_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
        source_file = f"{self.slug}.{source_format}"
        converter = (
            "pdfplumber-geometry (model-free)"
            if source_format == "pdf"
            else f"{source_format}-fallback (model-free)"
        )

        meta = fm_build(
            title=title,
            source_file=source_file,
            source_format=source_format,
            converter=converter,
            content_sha256=content_sha256,
            text_class=text_class,
            doc_date="UNCONFIRMED",
            retrieved="2026-09-17",
            source_url=None,
        )
        full_text = fm_render(meta, body)
        (FIXTURES_DIR / f"{self.slug}.md").write_text(full_text, encoding="utf-8")

        sidecar = None
        if has_sidecar:
            sidecar = {
                "version": 1,
                "source_file": source_file,
                "source_format": source_format,
                "content_sha256": content_sha256,
                "converter": converter,
                "blocks": sidecar_blocks,
            }
            (FIXTURES_DIR / f"{self.slug}.provenance.json").write_text(
                json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
            )

        kind_counts: dict[str, int] = {}
        for blk in self.blocks:
            kind_counts[blk["kind"]] = kind_counts.get(blk["kind"], 0) + 1

        pages = [b["page"] for b in self.blocks if b["page"] is not None]
        return {
            "slug": self.slug,
            "title": title,
            "source_format": source_format,
            "text_class": text_class,
            "has_sidecar": has_sidecar,
            "chars": len(body),
            "block_count": len(self.blocks),
            "block_kind_counts": kind_counts,
            "page_count": (max(pages) if pages else None),
        }


def build_handbook() -> dict:
    rng = rng_for("handbook")
    doc = DocBuilder("handbook")
    page = 1
    doc.add(page, "heading", "# Handbook")
    doc.add(page, "paragraph", paragraph(rng, 5))

    section_headings = [
        "Introduction",
        "Travel Policy",
        "Expense Reporting",
        "Approvals",
        "Per Diem Rates",
        "Booking Procedures",
        "Reimbursement Timeline",
        "Mileage",
        "Lodging",
        "Miscellaneous",
    ]
    table_at_start_placed = False
    table_oversized_placed = False
    long_paragraph_placed = False
    detail_heading_placed = False

    for idx, heading in enumerate(section_headings):
        page += 1
        level = 2 if idx % 3 == 0 else 3
        doc.add(page, "heading", f"{'#' * level} {heading}")

        if not table_at_start_placed:
            doc.add(page, "table", table(rng, rows=5, cols=4))
            table_at_start_placed = True

        for _ in range(rng.randint(3, 5)):
            if rng.random() < 0.3:
                page += 1
            doc.add(page, "paragraph", paragraph(rng, rng.randint(4, 7)))

        doc.add(page, "list", bullet_list(rng, rng.randint(3, 5)))

        if not detail_heading_placed and idx == 2:
            page += 1
            doc.add(page, "heading", "#### Detail Note")
            doc.add(page, "paragraph", paragraph(rng, 3))
            detail_heading_placed = True

        if not table_oversized_placed and idx == 4:
            page += 1
            doc.add(page, "table", table(rng, rows=45, cols=6))  # > CHUNK_MAX (2500 chars)
            table_oversized_placed = True

        if not long_paragraph_placed and idx == 6:
            page += 1
            big_paragraph = "\n\n".join(paragraph(rng, 6) for _ in range(7))  # > CHUNK_MAX
            doc.add(page, "paragraph", big_paragraph)
            long_paragraph_placed = True

    while page < 40 or doc.total_chars() < 58_000:
        page += 1
        if page % 5 == 0:
            doc.add(page, "heading", "### Additional Notes")
        doc.add(page, "paragraph", paragraph(rng, rng.randint(5, 9)))

    return doc.render(title="Employee Handbook", source_format="pdf", text_class="clean")


def build_budget_form() -> dict:
    rng = rng_for("budget-form")
    doc = DocBuilder("budget-form")

    doc.add(1, "heading", "# Budget Form")
    doc.add(1, "paragraph", paragraph(rng, 3))
    doc.add(1, "table", table(rng, rows=4, cols=3, plant=CALLOUT_TOKEN))
    doc.add(1, "annotation", f"> **Annotation** [near: Column 1] {sentence(rng, 6)}")
    doc.add(1, "annotation", f"> **Annotation** [near: Column 2] {sentence(rng, 6)}")
    doc.add(1, "boxed_text", f"> **Boxed text:** {sentence(rng, 8)}")

    doc.add(
        2,
        "heading",
        "## Carryover Provisions",
    )
    doc.add(
        2,
        "paragraph",
        "> **INCOMPLETE — pages 2, 3 are mostly image (up to 36.8% of the page), and "
        "that content is not in the text layer.** No OCR was attempted.",
    )
    doc.add(2, "paragraph", paragraph(rng, 3))
    doc.add(2, "annotation", f"> **Annotation** [near: Carryover Provisions] {sentence(rng, 6)}")
    doc.add(2, "annotation", f"> **Annotation** {sentence(rng, 6)}")
    doc.add(2, "boxed_text", f"> **Boxed text:** {sentence(rng, 8)}")

    doc.add(3, "heading", "### Carried-Over Federal Funds")
    carry_over_row = table(rng, rows=3, cols=3, plant=f"Cell recording {CARRY_OVER} amounts")
    doc.add(3, "table", carry_over_row)
    doc.add(3, "annotation", f"> **Annotation** [points to: Carried-Over Federal Funds] {sentence(rng, 6)}")
    doc.add(3, "annotation", f"> **Annotation** {sentence(rng, 6)}")
    doc.add(3, "annotation", f"> **Annotation** {sentence(rng, 6)}")
    doc.add(3, "boxed_text", f"> **Boxed text:** {sentence(rng, 8)}")

    doc.add(4, "heading", "## Signatures")
    doc.add(4, "paragraph", paragraph(rng, 4))
    doc.add(4, "annotation", f"> **Annotation** {sentence(rng, 6)}")
    doc.add(4, "boxed_text", f"> **Boxed text:** {sentence(rng, 8)}")

    doc.add(5, "heading", "### Distribution")
    doc.add(5, "paragraph", paragraph(rng, 4))
    doc.add(5, "annotation", f"> **Annotation** {sentence(rng, 6)}")
    doc.add(5, "annotation", f"> **Annotation** {sentence(rng, 6)}")
    doc.add(5, "boxed_text", f"> **Boxed text:** {sentence(rng, 8)}")

    doc.add(6, "heading", "### Notes")
    doc.add(6, "paragraph", paragraph(rng, 5))
    doc.add(6, "list", bullet_list(rng, 4))
    doc.add(6, "boxed_text", f"> **Boxed text:** {sentence(rng, 8)}")

    while doc.total_chars() < 11_500:
        doc.add(6, "paragraph", paragraph(rng, 3))

    return doc.render(title="Budget Reallocation Form", source_format="pdf", text_class="partial")


def build_deck() -> dict:
    rng = rng_for("deck")
    doc = DocBuilder("deck")
    doc.add(1, "heading", "# Program Review Deck")

    for page in range(1, 31):
        doc.add(page, "heading", f"### Slide {page}")
        n_blocks = rng.randint(1, 2)  # plus the heading = 2-3 blocks per page
        for _ in range(n_blocks):
            if rng.random() < 0.5:
                doc.add(page, "paragraph", paragraph(rng, rng.randint(3, 5)))
            else:
                doc.add(page, "list", bullet_list(rng, rng.randint(3, 6)))

    return doc.render(title="Program Review Deck", source_format="pdf", text_class="clean")


def build_reference_table() -> dict:
    """CSV-style document: no sidecar, no anchors (§5.3 fallback path)."""
    rng = rng_for("reference-table")
    doc = DocBuilder("reference-table")
    doc.add(None, "heading", "# Reference Table")
    doc.add(None, "table", table(rng, rows=12, cols=5))
    return doc.render(
        title="Reference Table",
        source_format="csv",
        text_class="clean",
        has_sidecar=False,
        with_anchors=False,
    )


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    # Clear stale output so a renamed/removed document does not linger.
    for existing in FIXTURES_DIR.glob("*"):
        if existing.name != "expected.json":
            existing.unlink()

    documents = [
        build_handbook(),
        build_budget_form(),
        build_deck(),
        build_reference_table(),
    ]

    expected = {
        "_note": (
            "Block counts are generated (S0). Section and chunk counts are added in S1 "
            "once retrieval/chunk.py exists."
        ),
        "documents": {d["slug"]: d for d in documents},
    }
    (FIXTURES_DIR / "expected.json").write_text(
        json.dumps(expected, indent=2) + "\n", encoding="utf-8"
    )

    for d in documents:
        print(f"{d['slug']}: {d['block_count']} blocks, {d['chars']} chars")


if __name__ == "__main__":
    main()
