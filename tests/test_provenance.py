"""Block-level provenance for converted markdown.

The source documents live outside the repo, so the markdown needs durable anchors that let a
reader jump from a retrieved passage back to the original file and page. These tests use only
synthetic fixtures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipeline.convert import convert_entry, provenance_path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

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


# ------------------------------------------------------------------- DOCX, DOC, and CSV


@pytest.mark.parametrize(
    "fixture_name", ["simple.docx", "tables_and_image.docx", "reference_table.csv"]
)
def test_sidecar_written_with_matching_anchors_and_null_page(fixture_name, config, entry_for):
    """Every block id in the sidecar has exactly one anchor in the markdown and vice versa."""
    result = convert_entry(entry_for(fixture_name), config)

    markdown = result.output.read_text(encoding="utf-8")
    sidecar = provenance_path(result.output)
    data = json.loads(sidecar.read_text(encoding="utf-8"))

    ids_in_markdown = BLOCK.findall(markdown)
    ids_in_sidecar = [block["block_id"] for block in data["blocks"]]

    assert ids_in_markdown
    assert ids_in_markdown == ids_in_sidecar
    assert markdown.count("<!-- dgx:block=") == len(data["blocks"])
    assert data["version"] == 1
    assert data["content_sha256"]
    assert data["converter"] == result.converter
    assert all(block["page"] is None for block in data["blocks"])
    assert all("bbox" not in block for block in data["blocks"])


def test_docx_content_sha256_matches_frontmatter(config, entry_for):
    result = convert_entry(entry_for("simple.docx"), config)
    markdown = result.output.read_text(encoding="utf-8")
    sidecar = json.loads(provenance_path(result.output).read_text(encoding="utf-8"))

    front_sha = re.search(r"content_sha256: (\w+)", markdown).group(1)
    assert front_sha == sidecar["content_sha256"]


def test_tables_and_image_docx_block_kinds(config, entry_for):
    result = convert_entry(entry_for("tables_and_image.docx"), config)
    data = json.loads(provenance_path(result.output).read_text(encoding="utf-8"))

    kinds = [block["kind"] for block in data["blocks"]]
    assert kinds.count("table") == 2
    assert kinds.count("list") == 1
    assert kinds.count("picture") == 1


def test_tables_and_image_docx_has_incomplete_note_simple_does_not(config, entry_for):
    with_image = convert_entry(entry_for("tables_and_image.docx"), config)
    without_image = convert_entry(entry_for("simple.docx"), config)

    assert "INCOMPLETE" in with_image.output.read_text(encoding="utf-8")
    assert "INCOMPLETE" not in without_image.output.read_text(encoding="utf-8")


def test_csv_sidecar_ends_with_a_table_block(config, entry_for):
    result = convert_entry(entry_for("reference_table.csv"), config)
    data = json.loads(provenance_path(result.output).read_text(encoding="utf-8"))

    assert data["blocks"][-1]["kind"] == "table"


def test_docx_with_xml_comment_nodes_in_body_converts(config, entry_for):
    """Regulation-style DOCX bodies carry `<!--Topic ...-->` comment nodes and processing
    instructions between paragraphs. They must be ignored, not crash the walk."""
    result = convert_entry(entry_for("xml_comment.docx"), config)
    assert result.output is not None, result.message
    data = json.loads(provenance_path(result.output).read_text(encoding="utf-8"))
    kinds = [b["kind"] for b in data["blocks"]]
    assert kinds.count("heading") == 2
    assert kinds.count("paragraph") >= 2
    text = result.output.read_text(encoding="utf-8")
    assert "Topic unique" not in text
    assert "Topic header" not in text
    assert "inline marker" not in text


def test_strip_non_element_nodes_covers_every_wml_part(config):
    """The invariant, checked directly: after stripping, no WordprocessingML part of the
    package holds a comment or processing-instruction node, and the count reported equals
    what the fixture planted (two body comments, one body PI, one inline, one header)."""
    from docx import Document
    from docx.opc.part import XmlPart
    from lxml import etree

    from pipeline.converters.office import _WML_CONTENT_TYPE_PREFIX, _strip_non_element_nodes

    docx_obj = Document(str(FIXTURES / "xml_comment.docx"))
    assert _strip_non_element_nodes(docx_obj) == 5
    for part in docx_obj.part.package.iter_parts():
        if isinstance(part, XmlPart) and part.content_type.startswith(_WML_CONTENT_TYPE_PREFIX):
            assert not list(part.element.iter(etree.Comment, etree.ProcessingInstruction)), (
                part.partname
            )
    assert _strip_non_element_nodes(docx_obj) == 0


def test_docx_with_dangling_relationships_converts_and_reports_them(config, entry_for):
    """An image relationship whose target is a directory or a missing file must not stop
    the conversion (Word ignores it; python-docx alone cannot). The real image survives,
    and the dangling references are recorded as evidence: the document points at content
    the package does not contain."""
    entry = entry_for("dangling_rels.docx")
    result = convert_entry(entry, config)
    assert result.output is not None, result.message
    data = json.loads(provenance_path(result.output).read_text(encoding="utf-8"))
    kinds = [b["kind"] for b in data["blocks"]]
    assert kinds.count("picture") == 1
    assert kinds.count("heading") == 1
    assert entry["conversion"]["images"] == 1
    assert entry["conversion"]["dangling_relationships"] == 2
    assert "INCOMPLETE" in result.output.read_text(encoding="utf-8")
