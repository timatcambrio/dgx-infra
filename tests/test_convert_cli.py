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
    assert (kb / "kb" / "reference-table.md").is_file()
    assert (kb / "kb" / "born-digital.md").is_file()
    assert not (kb / "kb" / "simple.md").exists()

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
            {"source_file": "DFARSPGI.docx", "slug": "dfarspgi"},
            {"source_file": "Ch 05_b.pdf", "slug": "ch-05-b"},
            {"source_file": "notes.csv", "slug": "notes"},
        ]
    }

    def names(only):
        return [e["slug"] for e in _selected(manifest, only)]

    assert names("DFARSPGI") == ["dfarspgi"]
    assert names("dfarspgi") == ["dfarspgi"]
    assert names("DFARSPGI.docx") == ["dfarspgi"]
    assert names("*.DOCX") == ["dfarspgi"]
    assert names("05") == ["ch-05-b"]
    assert names(None) == ["dfarspgi", "ch-05-b", "notes"]
    assert names("nomatch") == []


def test_prune_removes_missing_entries_and_their_outputs_only_with_yes(workspace):
    kb, source = workspace
    for command in ("inventory", "triage", "convert"):
        result = runner.invoke(app, [command, "--source-dir", str(source)])
        assert result.exit_code == 0, result.output
    assert (kb / "kb" / "simple.md").is_file()
    assert (kb / "kb" / "simple.provenance.json").is_file()

    (source / "simple.docx").unlink()
    assert runner.invoke(app, ["inventory", "--source-dir", str(source)]).exit_code == 0

    dry = runner.invoke(app, ["prune", "--source-dir", str(source)])
    assert dry.exit_code == 0, dry.output
    assert "Would remove" in dry.output and "simple.docx" in dry.output
    assert (kb / "kb" / "simple.md").is_file(), "dry run must not delete"

    wet = runner.invoke(app, ["prune", "--source-dir", str(source), "--yes"])
    assert wet.exit_code == 0, wet.output
    assert not (kb / "kb" / "simple.md").exists()
    assert not (kb / "kb" / "simple.provenance.json").exists()
    assert (kb / "kb" / "reference-table.md").is_file()
    assert (kb / "kb" / "born-digital.md").is_file()

    manifest = yaml.safe_load((kb / "corpus.yaml").read_text(encoding="utf-8"))
    names = {d["source_file"] for d in manifest["documents"]}
    assert names == {"reference_table.csv", "born_digital.pdf"}

    again = runner.invoke(app, ["prune", "--source-dir", str(source), "--yes"])
    assert again.exit_code == 0 and "Nothing to prune" in again.output
