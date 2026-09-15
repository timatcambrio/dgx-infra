#!/usr/bin/env python3
"""Evidence profile for a directory of PDFs — what each document offers, as counts.

Two uses, and the second is why this is a script rather than a note somewhere.

The first is ordinary: point it at a corpus and see what is in there before converting any
of it. Documents differ in what they even *offer* — one carries two hundred annotations and
no form fields, the next carries form fields and no annotations and is a screenshot besides —
and a converter tuned to whichever kind someone happened to look at first will quietly do
badly on the rest.

The second is that it can be run where the documents are and nothing else can. It emits
counts and filenames, never document text, so a profile of a corpus that cannot leave its
own machine can still be read, pasted into a ticket, or handed to someone deciding what to
build next. `pipeline report` gives the same facts with interpretation attached, but it needs
a manifest and a conversion run first; this needs neither.

PDFs only. The geometry facts it reports have no meaning for DOCX or CSV, which carry their
text structurally and are always classified `clean`.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config as config_module  # noqa: E402
from pipeline.triage import triage_pdf  # noqa: E402

#: Column headings, in the order `_row` produces them.
COLUMNS = (
    "pages", "class", "ch/pg", "cols", "ruled", "bordl",
    "annot", "w/CL", "fields", "imgpg", "img%",
)

LEGEND = """
  ruled / bordl  tables found by ruling lines / by column alignment
  annot / w/CL   text annotations / of those, how many state their own target
  fields         named form fields; with ruled ~ 0 the grid is not being reconstructed
  imgpg / img%   pages that are mostly raster image / the largest coverage seen
"""


def _row(result) -> tuple[str, ...]:
    return (
        str(result.page_count),
        result.text_class[:5],
        f"{result.chars_per_page_median or 0:.0f}",
        str(result.max_columns),
        str(result.ruled_tables),
        str(result.borderless_table_pages),
        str(result.annotations),
        str(result.annotations_with_callout),
        str(result.form_fields),
        str(len(result.image_pages)),
        f"{result.max_image_coverage:.0%}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source-dir", type=Path, default=None,
        help="Directory of PDFs to profile. Defaults to SOURCE_DIR.",
    )
    parser.add_argument(
        "--width", type=int, default=44, help="Filename column width (default 44)."
    )
    arguments = parser.parse_args()

    # Profiling reads and writes nothing in the knowledge base, but `load` resolves a path
    # for it regardless. A throwaway keeps a stray KB_PATH from mattering either way.
    os.environ.setdefault("KB_PATH", tempfile.mkdtemp(prefix="profile-"))
    try:
        config = config_module.load(source_dir=arguments.source_dir)
        root = config.source_dir()
    except config_module.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    pdfs = sorted(
        path for path in root.rglob("*.pdf") if not path.name.startswith(".")
    )
    if not pdfs:
        print(f"No PDFs under {root}", file=sys.stderr)
        return 1

    print(f"EVIDENCE PROFILE — {len(pdfs)} PDF(s) under {root}")
    print(LEGEND)
    width = arguments.width
    print(f"{'document':<{width}} " + " ".join(f"{name:>6}" for name in COLUMNS))
    for path in pdfs:
        cells = _row(triage_pdf(path, config))
        print(f"{path.name[:width]:<{width}} " + " ".join(f"{cell:>6}" for cell in cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
