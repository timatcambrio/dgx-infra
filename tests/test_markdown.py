"""Markdown sources: converted without losing a line the author wrote.

Markdown is already the output format, so the converter's only jobs are to cut the file into
anchored blocks and to keep the conversion contract. Every other format is rebuilt from a
layout or a document model and then tidied by `normalize_markdown`; a Markdown source must
not be, because tidying text the author wrote is changing it. Each test here names one way
the author's text could be altered or dropped on the way into `kb/`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pipeline import config as config_module
from pipeline import manifest as manifest_module
from pipeline.convert import STATUS_STOP_AND_ASK, STATUS_WRITTEN, convert_entry
from pipeline.frontmatter import parse_file, validate
from retrieval.kbfiles import load_document

ANCHOR_PREFIX = "<!-- dgx:block="


@pytest.fixture
def source_root(tmp_path) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    return root


@pytest.fixture
def md_config(tmp_path, source_root, monkeypatch):
    monkeypatch.setenv("KB_PATH", str(tmp_path / "kb-repo"))
    return config_module.load(source_dir=source_root)


def _convert(source_root: Path, config, name: str, content: str | bytes):
    path = source_root / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    entry = manifest_module.new_entry(name, manifest_module.format_for(path))
    entry["sha256"] = manifest_module.sha256_of(path)
    entry["triage"] = {"text_class": "clean", "chars_per_page_mean": None}
    return entry, convert_entry(entry, config)


def _body(output: Path) -> str:
    text = output.read_text(encoding="utf-8")
    return text.split("\n---\n", 1)[1]


def _sidecar(output: Path) -> dict:
    return json.loads(output.with_suffix(".provenance.json").read_text(encoding="utf-8"))


def _content_lines(text: str) -> list[str]:
    """Non-blank lines, with the converter's own anchor lines removed."""
    return [
        line for line in text.split("\n") if line.strip() and not line.startswith(ANCHOR_PREFIX)
    ]


# ------------------------------------------------------------------------- registration


@pytest.mark.parametrize("name", ["notes.md", "notes.markdown", "NOTES.MD"])
def test_markdown_extensions_are_inventoried(name):
    """Before this, inventory counted `.md` files as skipped and they never reached `kb/`."""
    assert manifest_module.format_for(Path(name)) == "md"


# ------------------------------------------------------------------------- verbatim body

VERBATIM_SOURCE = """# Travel policy

Reimbursement follows the rates in effect.
This line follows a hard break, which is two trailing spaces.

Contents ........ 4
Rates . . . . . . 7

```python
# a comment, not a heading


print("two blank lines above stay")
```

| Code | Limit |
|---|---|
| A1 | 25 |

- first
- second

  continued paragraph inside the list item

See the [rate table][rates] for detail.

[rates]: https://example.invalid/rates "Rates"

<div>raw html stays</div>

---

> quoted guidance
"""


def test_every_source_line_survives_unchanged(source_root, md_config):
    """Each non-blank line of the source appears in the output, in order, byte for byte.

    The shared tidy-up step would collapse the dotted leaders into an ellipsis and the blank
    lines inside the code fence; the reference definition produces no parser token at all, so
    a token-driven splitter drops it. All three are the author's text.
    """
    _, result = _convert(source_root, md_config, "policy.md", VERBATIM_SOURCE)

    assert result.status == STATUS_WRITTEN, result.message
    assert _content_lines(_body(result.output)) == _content_lines(VERBATIM_SOURCE)


def test_blank_lines_inside_a_code_fence_are_kept(source_root, md_config):
    """Blank lines between blocks carry no meaning; inside a fence they are code."""
    _, result = _convert(source_root, md_config, "policy.md", VERBATIM_SOURCE)

    assert '# a comment, not a heading\n\n\nprint("two blank lines above stay")' in _body(
        result.output
    )


def test_a_hash_line_inside_a_code_fence_is_not_a_heading(source_root, md_config):
    """A `#` comment in code must not cut the document into a new section."""
    _, result = _convert(source_root, md_config, "policy.md", VERBATIM_SOURCE)

    headings = [b for b in _sidecar(result.output)["blocks"] if b["kind"] == "heading"]
    assert len(headings) == 1


def test_block_kinds_follow_the_markdown_structure(source_root, md_config):
    """Tables stay `table` blocks so search chunks them by row; lists stay `list` blocks."""
    _, result = _convert(source_root, md_config, "policy.md", VERBATIM_SOURCE)

    kinds = [b["kind"] for b in _sidecar(result.output)["blocks"]]
    assert kinds.count("table") == 1
    assert kinds.count("list") == 1
    confidences = {b["confidence"] for b in _sidecar(result.output)["blocks"]}
    assert "markdown:fence" in confidences


def test_conversion_is_deterministic(source_root, md_config):
    _, first = _convert(source_root, md_config, "policy.md", VERBATIM_SOURCE)
    first_bytes = first.output.read_bytes()
    _, second = _convert(source_root, md_config, "policy.md", VERBATIM_SOURCE)
    assert second.output.read_bytes() == first_bytes


def test_output_loads_for_indexing(source_root, md_config):
    """The anchors and the sidecar agree, so `kb index` accepts the file."""
    _, result = _convert(source_root, md_config, "policy.md", VERBATIM_SOURCE)

    document = load_document(result.output)
    assert document.has_sidecar
    assert document.page_count is None
    assert document.incomplete_pages == []


def test_crlf_and_byte_order_mark_are_read_as_text(source_root, md_config):
    """Windows editors write both. Neither is content; a BOM must not hide front matter."""
    raw = "﻿---\ntitle: Windows Notes\n---\n\n# Heading\r\n\r\nBody text.\r\n"
    _, result = _convert(source_root, md_config, "win.md", raw.encode("utf-8"))

    assert result.status == STATUS_WRITTEN, result.message
    assert parse_file(result.output)["title"] == "Windows Notes"
    assert "\r" not in result.output.read_text(encoding="utf-8")


def test_text_that_is_not_utf8_stops_and_asks(source_root, md_config):
    """Guessing an encoding would silently corrupt every non-ASCII character."""
    _, result = _convert(source_root, md_config, "latin.md", "Café prices\n".encode("cp1252"))

    assert result.status == STATUS_STOP_AND_ASK
    assert "UTF-8" in result.message


# ------------------------------------------------------------------------- front matter

FRONT_MATTER_SOURCE = """---
title: Field Guide
date: 2026-03-04
owner: Records Office
tags: [travel, rates]
---

# Field Guide

Body text.
"""


def test_source_front_matter_supplies_title_and_date(source_root, md_config):
    _, result = _convert(source_root, md_config, "guide.md", FRONT_MATTER_SOURCE)

    meta = parse_file(result.output)
    assert validate(meta) == []
    assert meta["title"] == "Field Guide"
    assert meta["doc_date"] == "2026-03-04"
    assert meta["source_format"] == "md"


def test_source_front_matter_is_kept_in_the_body(source_root, md_config):
    """Fields the contract has no place for (`owner`, `tags`) are the author's metadata too.

    The whole block is kept, unchanged, as a fenced YAML block at the top of the body, so a
    second `---` block cannot be mistaken for our own front matter or render as a heading.
    """
    _, result = _convert(source_root, md_config, "guide.md", FRONT_MATTER_SOURCE)

    body = _body(result.output)
    assert "```yaml\ntitle: Field Guide\ndate: 2026-03-04\nowner: Records Office\n" in body
    assert "tags: [travel, rates]\n```" in body
    assert body.count("\n---\n") == 0
    kinds = {b["confidence"] for b in _sidecar(result.output)["blocks"]}
    assert "markdown:front_matter" in kinds


def test_a_non_iso_date_is_not_promoted(source_root, md_config):
    """`doc_date` must be an honest ISO date; anything else stays UNCONFIRMED."""
    source = "---\ntitle: Memo\ndate: last spring\n---\n\nBody.\n"
    _, result = _convert(source_root, md_config, "memo.md", source)

    assert parse_file(result.output)["doc_date"] == "UNCONFIRMED"
    assert "date: last spring" in _body(result.output)


def test_manifest_title_wins_over_source_front_matter(source_root, md_config):
    path = source_root / "guide.md"
    path.write_text(FRONT_MATTER_SOURCE, encoding="utf-8")
    entry = manifest_module.new_entry("guide.md", "md")
    entry["sha256"] = manifest_module.sha256_of(path)
    entry["triage"] = {"text_class": "clean", "chars_per_page_mean": None}
    entry["title"] = "Curated Title"

    result = convert_entry(entry, md_config)

    assert parse_file(result.output)["title"] == "Curated Title"


# ------------------------------------------------------------------------- hazards


def test_a_fake_block_anchor_in_the_source_is_escaped(source_root, md_config):
    """A source line shaped like our anchor would be read back as a block boundary.

    The indexer would then find a block id the sidecar does not list and refuse the file.
    It is escaped with one leading space (still an HTML comment, still invisible when
    rendered), and the change is recorded rather than silent.
    """
    source = "# Notes\n\n<!-- dgx:block=other:p001:b001 -->\nText after it.\n"
    entry, result = _convert(source_root, md_config, "notes.md", source)

    body = _body(result.output)
    assert "\n <!-- dgx:block=other:p001:b001 -->\n" in body
    assert entry["conversion"]["escaped_anchors"] == 1
    flagged = [
        b for b in _sidecar(result.output)["blocks"] if b["confidence"] == "markdown:anchor-escaped"
    ]
    assert len(flagged) == 1
    load_document(result.output)  # does not raise


def test_images_are_flagged_incomplete(source_root, md_config):
    """An image's content is not in the text; the reader is told so, as for Word files."""
    source = "# Site\n\n![Floor plan](plan.png)\n\nText.\n"
    entry, result = _convert(source_root, md_config, "site.md", source)

    body = _body(result.output)
    assert "> **INCOMPLETE — this document references 1 image(s)" in body
    assert "![Floor plan](plan.png)" in body
    assert entry["conversion"]["images"] == 1


def test_incomplete_page_wording_in_the_source_is_not_read_as_missing_pages(
    source_root, md_config
):
    """The indexer reads `INCOMPLETE — pages N` as the PDF converter's missing-page note.

    A Markdown file has no pages, so the same words written by an author must not make the
    index report pages missing.
    """
    source = "# Status\n\nINCOMPLETE — pages 3, 4 are still in review.\n"
    _, result = _convert(source_root, md_config, "status.md", source)

    assert load_document(result.output).incomplete_pages == []


def test_setext_headings_get_their_real_level(source_root, md_config):
    """`Title` underlined with `=` is a level 1 heading, `-` level 2, not the default 3."""
    source = "Policy\n======\n\nIntro.\n\nScope\n-----\n\nDetail.\n"
    _, result = _convert(source_root, md_config, "setext.md", source)

    levels = [b.level for b in load_document(result.output).blocks if b.kind == "heading"]
    assert levels == [1, 2]


def test_empty_file_converts_to_an_empty_body(source_root, md_config):
    _, result = _convert(source_root, md_config, "empty.md", "")

    assert result.status == STATUS_WRITTEN, result.message
    assert _sidecar(result.output)["blocks"] == []


def test_content_sha256_is_of_the_source_bytes(source_root, md_config):
    _, result = _convert(source_root, md_config, "policy.md", VERBATIM_SOURCE)

    expected = hashlib.sha256(VERBATIM_SOURCE.encode("utf-8")).hexdigest()
    assert parse_file(result.output)["content_sha256"] == expected
