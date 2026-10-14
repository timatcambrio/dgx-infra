"""Legacy PowerPoint (`.ppt`) and OpenDocument (`.odp`) decks, via LibreOffice.

They take the route legacy Word files take: `soffice` as a subprocess turns them into a
`.pptx` inside `WORK_DIR`, and the `.pptx` converter does the rest. LibreOffice is not a
test dependency, so `soffice` is replaced here by a stand-in that writes the fixture deck
where LibreOffice would. What is under test is the routing, where the intermediate file
goes, and what happens when LibreOffice is missing, not LibreOffice's own conversion.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import config as config_module
from pipeline import manifest as manifest_module
from pipeline.convert import STATUS_WRITTEN, convert_entry
from pipeline.converters import office
from pipeline.frontmatter import parse_file, validate
from retrieval.cite import build_citation

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def source_root(tmp_path) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    return root


@pytest.fixture
def legacy_config(tmp_path, source_root, monkeypatch):
    monkeypatch.setenv("KB_PATH", str(tmp_path / "kb-repo"))
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    return config_module.load(source_dir=source_root)


@pytest.fixture
def fake_soffice(monkeypatch):
    """Stand in for LibreOffice: write the fixture deck where `--outdir` says."""
    calls: list[list[str]] = []
    real_run = subprocess.run

    def run(args, **kwargs):
        if not args or args[0] != "soffice":
            return real_run(args, **kwargs)  # only LibreOffice is stood in for
        calls.append(list(args))
        outdir = Path(args[args.index("--outdir") + 1])
        target = args[args.index("--convert-to") + 1]
        source = Path(args[-1])
        shutil.copy(FIXTURES / "deck.pptx", outdir / f"{source.stem}.{target}")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(office, "find_soffice", lambda: "soffice")
    monkeypatch.setattr(office.subprocess, "run", run)
    return calls


def _entry(source_root: Path, name: str) -> dict:
    path = source_root / name
    path.write_bytes(b"legacy deck bytes for " + name.encode())
    entry = manifest_module.new_entry(name, manifest_module.format_for(path))
    entry["sha256"] = manifest_module.sha256_of(path)
    entry["triage"] = {"text_class": "clean", "chars_per_page_mean": None}
    return entry


@pytest.mark.parametrize(("name", "source_format"), [("deck.ppt", "ppt"), ("deck.odp", "odp")])
def test_legacy_deck_extensions_are_inventoried(name, source_format):
    """Before this, inventory counted `.ppt` and `.odp` files as skipped."""
    assert manifest_module.format_for(Path(name)) == source_format


@pytest.mark.parametrize("name", ["deck.ppt", "deck.odp"])
def test_legacy_deck_converts_through_libreoffice(name, source_root, legacy_config, fake_soffice):
    entry = _entry(source_root, name)

    result = convert_entry(entry, legacy_config)

    assert result.status == STATUS_WRITTEN, result.message
    meta = parse_file(result.output)
    assert validate(meta) == []
    assert meta["source_format"] == entry["source_format"]
    assert meta["converter"] == "libreoffice+python-pptx (slides)"
    text = result.output.read_text(encoding="utf-8")
    assert "## Slide 2: Travel Rates" in text
    assert "deck:p006:b002" in text


def test_the_intermediate_pptx_is_written_to_work_dir_not_the_source(
    source_root, legacy_config, fake_soffice
):
    """`SOURCE_DIR` is read-only; LibreOffice's output belongs in the disposable `WORK_DIR`."""
    convert_entry(_entry(source_root, "deck.ppt"), legacy_config)

    (args,) = fake_soffice
    assert args[args.index("--convert-to") + 1] == "pptx"
    outdir = Path(args[args.index("--outdir") + 1])
    assert outdir.is_relative_to(legacy_config.work_dir)
    assert sorted(p.name for p in source_root.iterdir()) == ["deck.ppt"]


def test_missing_libreoffice_names_the_fix(tmp_path, monkeypatch):
    """No silent fallback: the run says LibreOffice is needed and for which files."""
    monkeypatch.setattr(office.shutil, "which", lambda _name: None)
    monkeypatch.setattr(office, "MAC_SOFFICE", tmp_path / "no-libreoffice-here")

    with pytest.raises(office.SofficeMissingError) as excinfo:
        office.find_soffice()

    assert ".ppt" in str(excinfo.value)
    assert ".odp" in str(excinfo.value)


@pytest.mark.parametrize("source_format", ["ppt", "odp"])
def test_citation_names_slides_for_legacy_decks(source_format):
    citation = build_citation(
        title="Deck",
        heading_path="Slide 2",
        source_file=f"deck.{source_format}",
        source_format=source_format,
        page_first=2,
        page_last=2,
        doc_date="UNCONFIRMED",
        first_block_id="deck:p002:b001",
        last_block_id="deck:p002:b003",
    )
    assert ", slides 2–2," in citation
