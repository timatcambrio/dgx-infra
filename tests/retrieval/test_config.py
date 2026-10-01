"""`retrieval.config` (brief §7.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from retrieval import config as config_module
from retrieval.config import ConfigError


def test_defaults_with_no_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    assert cfg.embed_model == "nomic-embed-text"
    assert cfg.embed_dim == 768
    assert cfg.kb_bind == "127.0.0.1:8765"
    assert cfg.chunk_target == 1200
    assert cfg.chunk_max == 2500
    assert cfg.fetch_max_chars == 200_000
    assert cfg.kb_tokens == ()
    assert cfg.database_url is None
    assert cfg.database_url_index is None
    assert cfg.kb_url_base is None


def test_kb_url_base_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KB_URL_BASE", "https://kb.internal.example/kb/")
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    assert cfg.kb_url_base == "https://kb.internal.example/kb"


def test_kb_tokens_comma_separated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KB_TOKENS", "alice-token, bob-token,carol-token")
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    assert cfg.kb_tokens == ("alice-token", "bob-token", "carol-token")


def test_kb_tokens_empty_string_is_no_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KB_TOKENS", "")
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    assert cfg.kb_tokens == ()


def test_kb_tokens_file_defaults_beside_dot_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The autouse fixture in conftest.py sets KB_TOKENS_FILE for isolation, so this is
    the one place the real default is asserted."""
    monkeypatch.delenv("KB_TOKENS_FILE", raising=False)
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    assert cfg.kb_tokens_file == config_module.DEFAULT_TOKENS_FILE
    assert cfg.kb_tokens_file.name == "tokens.json"


def test_kb_tokens_file_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Set-but-empty means "no token store", which is how the legacy KB_TOKENS-only path
    is selected on purpose rather than by accident."""
    monkeypatch.setenv("KB_TOKENS_FILE", "")
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    assert cfg.kb_tokens_file is None


def test_kb_tokens_file_is_resolved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KB_TOKENS_FILE", "tokens.json")
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    assert cfg.kb_tokens_file is not None and cfg.kb_tokens_file.is_absolute()


def test_kb_token_cache_seconds_default_and_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from retrieval import tokens as tokens_module

    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    assert cfg.kb_token_cache_seconds == tokens_module.DEFAULT_CACHE_SECONDS
    monkeypatch.setenv("KB_TOKEN_CACHE_SECONDS", "0.5")
    assert config_module.load(env_file=tmp_path / "nonexistent.env").kb_token_cache_seconds == 0.5


def test_kb_token_cache_seconds_must_be_a_number(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KB_TOKEN_CACHE_SECONDS", "soon")
    with pytest.raises(ConfigError, match="KB_TOKEN_CACHE_SECONDS must be a number"):
        config_module.load(env_file=tmp_path / "nonexistent.env")


def test_require_database_url_index_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    with pytest.raises(ConfigError, match="DATABASE_URL_INDEX"):
        cfg.require_database_url_index()


def test_require_database_url_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        cfg.require_database_url()


def test_require_kb_url_base_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    with pytest.raises(ConfigError, match="KB_URL_BASE"):
        cfg.require_kb_url_base()


def test_database_url_index_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL_INDEX", "postgresql://kb_index:kb@localhost:5432/kb")
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    assert cfg.require_database_url_index() == "postgresql://kb_index:kb@localhost:5432/kb"


def test_embed_dim_must_be_int(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EMBED_DIM", "not-a-number")
    with pytest.raises(ConfigError, match="EMBED_DIM"):
        config_module.load(env_file=tmp_path / "nonexistent.env")


def test_kb_path_defaults_next_to_repo(tmp_path: Path) -> None:
    cfg = config_module.load(env_file=tmp_path / "nonexistent.env")
    assert cfg.kb_path.name == "dgx-knowledge"
    assert cfg.kb_dir == cfg.kb_path / "kb"


def test_env_file_is_loaded(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EMBED_MODEL=custom-model\nEMBED_DIM=42\n", encoding="utf-8")
    cfg = config_module.load(env_file=env_file)
    assert cfg.embed_model == "custom-model"
    assert cfg.embed_dim == 42


def test_real_environment_wins_over_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EMBED_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("EMBED_MODEL", "from-real-env")
    cfg = config_module.load(env_file=env_file)
    assert cfg.embed_model == "from-real-env"
