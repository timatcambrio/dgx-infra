"""Block-level provenance for converted markdown.

The source documents live outside the repo, so the markdown needs durable anchors that let a
reader jump from a retrieved passage back to the original file and page. These tests use only
synthetic fixtures.
"""

from __future__ import annotations

import json
import re

from pipeline.convert import convert_entry, provenance_path

BLOCK = re.compile(r"<!-- dgx:block=([^ ]+) -->")


def test_pdf_conversion_writes_block_anchors_and_sidecar(config, entry_for):
    result = convert_entry(entry_for("born_digital.pdf"), config)

    markdown = result.output.read_text(encoding="utf-8")
    sidecar = provenance_path(result.output)
    data = json.loads(sidecar.read_text(encoding="utf-8"))

    ids_in_markdown = BLOCK.findall(markdown)
    ids_in_sidecar = [block["block_id"] for block in data["blocks"]]

    assert ids_in_markdown
    assert ids_in_markdown == ids_in_sidecar
    assert ids_in_markdown[0] == "born-digital:p001:b001"
    assert markdown.count("<!-- dgx:block=") == len(data["blocks"])
    assert data["version"] == 1
    assert data["source_file"] == "born_digital.pdf"
    assert data["source_format"] == "pdf"
    assert data["content_sha256"]
    assert data["converter"] == result.converter
    assert all(isinstance(block["page"], int) for block in data["blocks"])


def test_pdf_sidecar_records_source_page_kind_and_geometry(config, entry_for):
    result = convert_entry(entry_for("born_digital.pdf"), config)
    data = json.loads(provenance_path(result.output).read_text(encoding="utf-8"))

    by_id = {block["block_id"]: block for block in data["blocks"]}

    assert by_id["born-digital:p001:b001"]["kind"] == "heading"
    assert by_id["born-digital:p001:b001"]["page"] == 1
    assert by_id["born-digital:p001:b001"]["confidence"] == "geometry"
    assert len(by_id["born-digital:p001:b001"]["bbox"]) == 4
    assert any(block["kind"] == "table" and block["page"] == 2 for block in data["blocks"])


def test_pdf_markdown_and_sidecar_are_byte_stable(config, entry_for):
    entry = entry_for("born_digital.pdf")

    first = convert_entry(entry, config, force=True)
    first_markdown = first.output.read_bytes()
    first_sidecar = provenance_path(first.output).read_bytes()

    second = convert_entry(entry, config, force=True)

    assert second.output.read_bytes() == first_markdown
    assert provenance_path(second.output).read_bytes() == first_sidecar


def test_missing_pdf_sidecar_prevents_false_unchanged_status(config, entry_for):
    entry = entry_for("born_digital.pdf")
    first = convert_entry(entry, config)
    provenance_path(first.output).unlink()

    second = convert_entry(entry, config)

    assert second.status == "written"
    assert provenance_path(second.output).is_file()
