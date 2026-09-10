"""Shared test fixtures.

Every test gets an isolated `KB_PATH` and `WORK_DIR` under `tmp_path`, and an environment
scrubbed of the pipeline's own variables. Without the scrub a developer's `.env` would leak
into the suite and a passing run would mean nothing on another machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import config as config_module

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = Path(__file__).resolve().parent / "golden"

PIPELINE_ENV_VARS = (
    "SOURCE_DIR",
    "KB_PATH",
    "WORK_DIR",
    "MIN_CHARS_PER_PAGE",
    "MIN_ALPHA_RATIO",
    "MAX_LOW_PAGE_FRACTION",
    "CSV_MAX_ROWS",
    "CSV_MAX_COLS",
    "DOCLING_OCR_ENGINE",
)


#: Captured before the autouse fixture below stubs it out, so tests that are specifically
#: about .env parsing can put the real implementation back.
REAL_LOAD_DOTENV = config_module.load_dotenv


@pytest.fixture
def real_dotenv(monkeypatch):
    """Restore genuine .env loading for tests that exercise it."""
    monkeypatch.setattr(config_module, "load_dotenv", REAL_LOAD_DOTENV)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Remove pipeline variables so a local .env cannot influence a test result."""
    for name in PIPELINE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # config.load() would otherwise read the repo's real .env file.
    monkeypatch.setattr(config_module, "load_dotenv", lambda path=None: None)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def golden_dir() -> Path:
    return GOLDEN


@pytest.fixture
def config(tmp_path, monkeypatch):
    """A config pointing at the committed fixtures, writing into `tmp_path`."""
    monkeypatch.setenv("KB_PATH", str(tmp_path / "kb-repo"))
    return config_module.load(source_dir=FIXTURES)


@pytest.fixture
def entry_for(config):
    """Factory building the manifest entry `inventory` + `triage` would produce.

    PDFs get real triage rather than a stubbed `clean`, because conversion output depends on
    it — `low_pages` drives the missing-page note and `chars_per_page_mean` becomes
    `text_coverage`. A stub here would make the golden files describe something the pipeline
    never actually produces.
    """
    from pipeline import manifest as manifest_module
    from pipeline.triage import triage_pdf

    def build(fixture_name: str, source_root: Path = FIXTURES) -> dict:
        source_format = manifest_module.format_for(Path(fixture_name))
        entry = manifest_module.new_entry(fixture_name, source_format)
        entry["sha256"] = manifest_module.sha256_of(source_root / fixture_name)
        if source_format == "pdf":
            entry["triage"] = triage_pdf(source_root / fixture_name, config).to_dict()
        else:
            entry["triage"] = {"text_class": "clean", "chars_per_page_mean": None}
        return entry

    return build
