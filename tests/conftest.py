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
def entry_for():
    """Factory building the manifest entry `inventory` would produce for one fixture."""
    from pipeline import manifest as manifest_module

    def build(fixture_name: str, source_root: Path = FIXTURES) -> dict:
        entry = manifest_module.new_entry(
            fixture_name, manifest_module.format_for(Path(fixture_name))
        )
        entry["sha256"] = manifest_module.sha256_of(source_root / fixture_name)
        entry["triage"] = {"text_class": "clean", "chars_per_page_mean": None}
        return entry

    return build
