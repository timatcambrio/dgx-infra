"""`pipeline convert` as a run over many documents: one document failing must be reported
and must not stop the others."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pipeline import manifest as manifest_module
from pipeline.cli import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
runner = CliRunner()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    kb = tmp_path / "dgx-knowledge"
    monkeypatch.setenv("KB_PATH", str(kb))
    source = tmp_path / "docs"
    source.mkdir()
    for name in ("reference_table.csv", "simple.docx", "born_digital.pdf"):
        shutil.copy(FIXTURES / name, source / name)
    return kb, source


def test_one_failing_document_is_reported_and_the_rest_still_convert(workspace, monkeypatch):
    kb, source = workspace
    for command in ("inventory", "triage"):
        result = runner.invoke(app, [command, "--source-dir", str(source)])
        assert result.exit_code == 0, result.output

    import pipeline.cli as cli_module

    real_convert_entry = cli_module.convert_entry

    def exploding_convert_entry(entry, config, *, force=False):
        if entry["source_file"] == "simple.docx":
            raise RuntimeError("synthetic converter failure")
        return real_convert_entry(entry, config, force=force)

    monkeypatch.setattr(cli_module, "convert_entry", exploding_convert_entry)

    result = runner.invoke(app, ["convert", "--source-dir", str(source)])

    assert result.exit_code == 1, result.output
    assert "ERROR" in result.output and "simple.docx" in result.output
    assert "synthetic converter failure" in result.output
    assert (kb / "kb" / "reference-table-csv.md").is_file()
    assert (kb / "kb" / "born-digital-pdf.md").is_file()
    assert not (kb / "kb" / "simple-docx.md").exists()

    manifest = yaml.safe_load((kb / "corpus.yaml").read_text(encoding="utf-8"))
    by_file = {d["source_file"]: d for d in manifest["documents"]}
    assert by_file["simple.docx"]["conversion_error"].startswith("RuntimeError: synthetic")
    assert "conversion_error" not in by_file["reference_table.csv"]


def test_a_clean_run_clears_a_previous_conversion_error(workspace, monkeypatch):
    kb, source = workspace
    for command in ("inventory", "triage"):
        assert runner.invoke(app, [command, "--source-dir", str(source)]).exit_code == 0

    manifest_path = kb / "corpus.yaml"
    manifest = manifest_module.load(manifest_path)
    for entry in manifest["documents"]:
        if entry["source_file"] == "simple.docx":
            entry["conversion_error"] = "RuntimeError: stale"
    manifest_module.save(manifest, manifest_path)

    result = runner.invoke(app, ["convert", "--source-dir", str(source)])
    assert result.exit_code == 0, result.output
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    by_file = {d["source_file"]: d for d in manifest["documents"]}
    assert "conversion_error" not in by_file["simple.docx"]


def test_only_filter_is_case_insensitive_substring_or_glob():
    from pipeline.cli import _selected

    manifest = {
        "documents": [
            {"source_file": "DFARSPGI.docx", "slug": "dfarspgi-docx"},
            {"source_file": "Ch 05_b.pdf", "slug": "ch-05-b-pdf"},
            {"source_file": "notes.csv", "slug": "notes-csv"},
        ]
    }

    def names(only):
        return [e["slug"] for e in _selected(manifest, only)]

    assert names("DFARSPGI") == ["dfarspgi-docx"]
    assert names("dfarspgi") == ["dfarspgi-docx"]
    assert names("DFARSPGI.docx") == ["dfarspgi-docx"]
    assert names("*.DOCX") == ["dfarspgi-docx"]
    assert names("05") == ["ch-05-b-pdf"]
    assert names(None) == ["dfarspgi-docx", "ch-05-b-pdf", "notes-csv"]
    assert names("nomatch") == []


def test_prune_removes_missing_entries_and_their_outputs_only_with_yes(workspace):
    kb, source = workspace
    for command in ("inventory", "triage", "convert"):
        result = runner.invoke(app, [command, "--source-dir", str(source)])
        assert result.exit_code == 0, result.output
    assert (kb / "kb" / "simple-docx.md").is_file()
    assert (kb / "kb" / "simple-docx.provenance.json").is_file()

    (source / "simple.docx").unlink()
    assert runner.invoke(app, ["inventory", "--source-dir", str(source)]).exit_code == 0

    dry = runner.invoke(app, ["prune", "--source-dir", str(source)])
    assert dry.exit_code == 0, dry.output
    assert "Would remove" in dry.output and "simple.docx" in dry.output
    assert (kb / "kb" / "simple-docx.md").is_file(), "dry run must not delete"

    wet = runner.invoke(app, ["prune", "--source-dir", str(source), "--yes"])
    assert wet.exit_code == 0, wet.output
    assert not (kb / "kb" / "simple-docx.md").exists()
    assert not (kb / "kb" / "simple-docx.provenance.json").exists()
    assert (kb / "kb" / "reference-table-csv.md").is_file()
    assert (kb / "kb" / "born-digital-pdf.md").is_file()

    manifest = yaml.safe_load((kb / "corpus.yaml").read_text(encoding="utf-8"))
    names = {d["source_file"] for d in manifest["documents"]}
    assert names == {"reference_table.csv", "born_digital.pdf"}

    again = runner.invoke(app, ["prune", "--source-dir", str(source), "--yes"])
    assert again.exit_code == 0 and "Nothing to prune" in again.output


def test_google_shortcuts_are_reported_with_how_to_export_them(workspace):
    """A synced Google Drive folder holds `.gslides`/`.gdoc` shortcuts with no content.

    Counted only as an unsupported extension, a whole folder of decks looked ignored for no
    reason; the report says what to do instead.
    """
    _, source = workspace
    (source / "review.gslides").write_text('{"doc_id": "x"}', encoding="utf-8")
    (source / "memo.gdoc").write_text('{"doc_id": "y"}', encoding="utf-8")

    result = runner.invoke(app, ["inventory", "--source-dir", str(source)])

    assert result.exit_code == 0, result.output
    assert "download it from Google Slides as .pptx" in result.output
    assert "download it from Google Docs as .docx" in result.output


def _run(*commands, source):
    for command in commands:
        result = runner.invoke(app, [command, "--source-dir", str(source)])
        assert result.exit_code == 0, result.output
    return result


def test_same_stem_sources_of_different_formats_get_separate_kb_files(tmp_path, monkeypatch):
    """A deck saved next to its PDF export is the common case. Both were slug `deck`, both
    wrote `kb/deck.md` and its sidecar, and the second conversion silently replaced the
    first: one of the two documents was simply not in the knowledge base."""
    kb = tmp_path / "dgx-knowledge"
    monkeypatch.setenv("KB_PATH", str(kb))
    source = tmp_path / "docs"
    source.mkdir()
    shutil.copy(FIXTURES / "deck.pptx", source / "deck.pptx")
    shutil.copy(FIXTURES / "born_digital.pdf", source / "deck.pdf")

    _run("inventory", "triage", "convert", source=source)

    manifest = yaml.safe_load((kb / "corpus.yaml").read_text(encoding="utf-8"))
    slugs = {d["source_file"]: d["slug"] for d in manifest["documents"]}
    assert slugs == {"deck.pdf": "deck-pdf", "deck.pptx": "deck-pptx"}
    for slug, source_file in (("deck-pdf", "deck.pdf"), ("deck-pptx", "deck.pptx")):
        text = (kb / "kb" / f"{slug}.md").read_text(encoding="utf-8")
        assert f"\nsource_file: {source_file}\n" in text
        assert f"dgx:block={slug}:p001:b001" in text
        sidecar = (kb / "kb" / f"{slug}.provenance.json").read_text(encoding="utf-8")
        assert f'"source_file": "{source_file}"' in sidecar
    assert not (kb / "kb" / "deck.md").exists()


def _age_to_old_slugs(kb):
    """Rewrite a converted workspace as the slug scheme before the extension left it:
    `simple.docx` -> slug `simple`, `kb/simple.md`, `kb/simple.provenance.json`."""
    manifest_path = kb / "corpus.yaml"
    manifest = manifest_module.load(manifest_path)
    for entry in manifest["documents"]:
        old = entry["slug"].rsplit("-", 1)[0]
        entry["slug"] = old
        conversion = entry["conversion"]
        for key, suffix in (("output", ".md"), ("provenance", ".provenance.json")):
            if key in conversion:
                new_path = kb / conversion[key]
                old_path = kb / "kb" / f"{old}{suffix}"
                new_path.rename(old_path)
                conversion[key] = f"kb/{old}{suffix}"
    manifest_module.save(manifest, manifest_path)


def test_reinventory_of_an_old_corpus_renames_and_leaves_no_orphaned_kb_files(workspace):
    """An existing corpus.yaml kept its old slugs (`upsert` only filled a missing one), so
    the new names would never have taken effect; and once they did, the old kb/ files would
    have stayed behind, still indexed by `kb index`, serving every passage twice."""
    kb, source = workspace
    _run("inventory", "triage", "convert", source=source)
    _age_to_old_slugs(kb)
    assert (kb / "kb" / "simple.md").is_file()

    inventoried = _run("inventory", source=source)
    assert "renamed   : 3" in inventoried.output
    converted = _run("convert", source=source)
    assert "removed old kb/simple.md" in converted.output

    names = sorted(p.name for p in (kb / "kb").iterdir())
    assert names == [
        "born-digital-pdf.md",
        "born-digital-pdf.provenance.json",
        "reference-table-csv.md",
        "reference-table-csv.provenance.json",
        "simple-docx.md",
        "simple-docx.provenance.json",
    ]
    manifest = yaml.safe_load((kb / "corpus.yaml").read_text(encoding="utf-8"))
    by_file = {d["source_file"]: d for d in manifest["documents"]}
    assert by_file["simple.docx"]["slug"] == "simple-docx"
    assert by_file["simple.docx"]["conversion"]["output"] == "kb/simple-docx.md"


def test_an_old_kb_file_now_naming_another_source_is_not_removed(workspace):
    """The old name is only the entry's to delete while the file still says it came from
    that source; anything else at that path belongs to someone else."""
    kb, source = workspace
    _run("inventory", "triage", "convert", source=source)
    _age_to_old_slugs(kb)
    foreign = "---\nsource_file: elsewhere/simple.docx\n---\n\nNot ours.\n"
    (kb / "kb" / "simple.md").write_text(foreign, encoding="utf-8")

    _run("inventory", "convert", source=source)

    assert (kb / "kb" / "simple.md").read_text(encoding="utf-8") == foreign
    assert (kb / "kb" / "simple-docx.md").is_file()
    assert not (kb / "kb" / "simple.provenance.json").exists()
