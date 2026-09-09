"""Determinism and idempotency.

Converting the same input twice must produce byte-identical output, and re-running any
command must be a no-op. This is not a nicety: without it there is no way to tell a real
change in a `kb/` file from noise, and the sha256 that links a converted file back to a
source living outside the repo stops meaning anything.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pipeline import manifest as manifest_module
from pipeline.cli import app
from pipeline.convert import convert_entry

runner = CliRunner()


@pytest.fixture
def kb_path(tmp_path, monkeypatch):
    path = tmp_path / "dgx-knowledge"
    monkeypatch.setenv("KB_PATH", str(path))
    return path


def run_cli(*args, source_dir: Path) -> str:
    result = runner.invoke(app, [*args, "--source-dir", str(source_dir)])
    assert result.exit_code in (0, 2), result.output
    return result.output


# ------------------------------------------------------------------------------ conversion


def test_converting_twice_produces_identical_bytes(config, entry_for):
    first = convert_fixture_text("reference_table.csv", config, entry_for)
    second = convert_fixture_text("reference_table.csv", config, entry_for)
    assert first == second


def convert_fixture_text(name: str, config, entry_for) -> str:
    entry = entry_for(name)
    result = convert_entry(entry, config, force=True)
    return result.output.read_text(encoding="utf-8")


def test_second_conversion_is_a_no_op(config, entry_for):
    entry = entry_for("reference_table.csv")

    first = convert_entry(entry, config)
    second = convert_entry(entry, config)

    assert first.status == "written"
    assert second.status == "unchanged"


def test_changed_source_bytes_are_a_loud_error_not_a_silent_reconvert(config, entry_for):
    """The digest is the only link back to a source outside the repo, so drift must shout."""
    from pipeline.convert import SourceDigestMismatch

    entry = entry_for("reference_table.csv")
    entry["sha256"] = "0" * 64

    with pytest.raises(SourceDigestMismatch, match="inventory"):
        convert_entry(entry, config)


# -------------------------------------------------------------------------------- manifest


def test_manifest_save_is_deterministic(tmp_path):
    manifest = manifest_module.empty()
    for name in ("z.pdf", "a.pdf", "m/b.pdf"):
        entry = manifest_module.new_entry(name, "pdf")
        entry["sha256"] = "a" * 64
        manifest["documents"].append(entry)

    first = tmp_path / "one.yaml"
    second = tmp_path / "two.yaml"
    manifest_module.save(manifest, first)
    manifest_module.save(yaml.safe_load(first.read_text()), second)

    assert first.read_bytes() == second.read_bytes()


def test_manifest_entries_are_sorted_by_source_file(tmp_path):
    manifest = manifest_module.empty()
    for name in ("z.pdf", "a.pdf"):
        manifest["documents"].append(manifest_module.new_entry(name, "pdf"))

    path = tmp_path / "corpus.yaml"
    manifest_module.save(manifest, path)

    saved = yaml.safe_load(path.read_text())
    assert [entry["source_file"] for entry in saved["documents"]] == ["a.pdf", "z.pdf"]


def test_manifest_never_stores_an_absolute_path(fixtures_dir):
    relative = manifest_module.relative_source_path(
        fixtures_dir / "reference_table.csv", fixtures_dir
    )
    assert relative == "reference_table.csv"


def test_path_outside_the_source_root_is_refused(fixtures_dir, tmp_path):
    stray = tmp_path / "elsewhere.pdf"
    stray.write_bytes(b"%PDF-1.7\n")

    with pytest.raises(ValueError, match="not inside the source root"):
        manifest_module.relative_source_path(stray, fixtures_dir)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("travel.pdf", "travel"),
        ("rfsuny/travel_handbook.pdf", "rfsuny__travel-handbook"),
        ("RF SUNY/Travel (2024).pdf", "rf-suny__travel-2024"),
        ("a/b/c.docx", "a__b__c"),
    ],
)
def test_slugs_are_stable_and_preserve_structure(path, expected):
    """Two documents with the same filename in different directories must not collide."""
    assert manifest_module.slug_for(path) == expected


def test_changed_sha256_clears_stale_metrics(tmp_path, fixtures_dir):
    manifest = manifest_module.empty()
    entry, outcome = manifest_module.upsert(
        manifest, fixtures_dir / "reference_table.csv", fixtures_dir
    )
    assert outcome == "added"
    entry["triage"] = {"text_class": "clean"}
    entry["sha256"] = "0" * 64

    _, outcome = manifest_module.upsert(
        manifest, fixtures_dir / "reference_table.csv", fixtures_dir
    )

    assert outcome == "changed"
    assert entry["triage"] is None


# ----------------------------------------------------------------------------- end-to-end


def test_inventory_then_triage_then_report_is_repeatable(kb_path, fixtures_dir):
    run_cli("inventory", source_dir=fixtures_dir)
    run_cli("triage", source_dir=fixtures_dir)
    first = (kb_path / "corpus.yaml").read_bytes()
    first_report = run_cli("report", source_dir=fixtures_dir)

    run_cli("inventory", source_dir=fixtures_dir)
    run_cli("triage", source_dir=fixtures_dir)

    assert (kb_path / "corpus.yaml").read_bytes() == first
    assert run_cli("report", source_dir=fixtures_dir) == first_report


def test_inventory_rerun_reports_no_changes(kb_path, fixtures_dir):
    run_cli("inventory", source_dir=fixtures_dir)
    output = run_cli("inventory", source_dir=fixtures_dir)

    assert "added" not in output
    assert "unchanged" in output


def test_inventory_records_every_supported_fixture(kb_path, fixtures_dir):
    run_cli("inventory", source_dir=fixtures_dir)

    manifest = yaml.safe_load((kb_path / "corpus.yaml").read_text())
    recorded = {entry["source_file"] for entry in manifest["documents"]}

    assert recorded == {path.name for path in fixtures_dir.iterdir() if not path.name.startswith(".")}


def test_a_vanished_source_is_marked_missing_not_deleted(kb_path, tmp_path, fixtures_dir):
    import shutil

    source = tmp_path / "sources"
    source.mkdir()
    shutil.copy(fixtures_dir / "reference_table.csv", source / "table.csv")
    run_cli("inventory", source_dir=source)

    (source / "table.csv").unlink()
    output = run_cli("inventory", source_dir=source)

    manifest = yaml.safe_load((kb_path / "corpus.yaml").read_text())
    assert len(manifest["documents"]) == 1
    assert manifest["documents"][0]["status"] == "missing"
    assert "missing" in output


def test_unsupported_extensions_are_counted_not_ignored(kb_path, tmp_path, fixtures_dir):
    import shutil

    source = tmp_path / "sources"
    source.mkdir()
    shutil.copy(fixtures_dir / "reference_table.csv", source / "table.csv")
    (source / "notes.rtf").write_text("x")
    (source / "sheet.xlsx").write_text("x")

    output = run_cli("inventory", source_dir=source)

    assert ".rtf" in output
    assert ".xlsx" in output


def test_missing_source_dir_fails_with_an_actionable_message(kb_path, monkeypatch):
    monkeypatch.delenv("SOURCE_DIR", raising=False)
    result = runner.invoke(app, ["inventory"])

    assert result.exit_code == 1
    assert "SOURCE_DIR" in result.output
    assert "--source-dir" in result.output


def test_source_dir_is_never_written_to(kb_path, tmp_path, fixtures_dir):
    """SOURCE_DIR is read-only. Nothing may appear in it, including intermediates."""
    import shutil

    source = tmp_path / "sources"
    source.mkdir()
    for name in ("reference_table.csv", "born_digital.pdf", "mixed.pdf"):
        shutil.copy(fixtures_dir / name, source / name)
    before = {path.name for path in source.rglob("*")}

    run_cli("inventory", source_dir=source)
    run_cli("triage", source_dir=source)
    run_cli("convert", source_dir=source)
    run_cli("report", source_dir=source)

    assert {path.name for path in source.rglob("*")} == before


# -------------------------------------------------------------------------------- fixtures


def test_fixtures_regenerate_byte_identically(tmp_path):
    """The generator is committed alongside its output, so it must reproduce it exactly."""
    spec = importlib.util.spec_from_file_location(
        "_make_fixtures", Path(__file__).resolve().parent / "make_fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    committed = Path(__file__).resolve().parent / "fixtures"
    module.FIXTURES = tmp_path
    module.main()

    for name in module.GENERATORS:
        assert (tmp_path / name).read_bytes() == (committed / name).read_bytes(), name
