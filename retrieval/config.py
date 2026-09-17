"""Configuration for Stage 2 (brief §7.1): one frozen dataclass, loaded from the
environment / `.env`, failing loudly (exit 2, naming the key) rather than guessing.

Deliberately separate from `pipeline.config`: the two packages share nothing but the `kb/`
contract, and Stage 2 must not fatten or complicate Stage 1's configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Keys required unconditionally. `DATABASE_URL_INDEX` is required only for `kb index`,
#: and `KB_URL_BASE` only for `kb serve`; both are checked lazily via the properties below
#: rather than at load time, so `kb --help` and other commands that need neither still work
#: without a fully-populated `.env`.
_ALWAYS_REQUIRED = ("KB_PATH",)


class ConfigError(RuntimeError):
    """Configuration is missing or unusable. The message names the offending key."""


def load_dotenv(path: Path | str | None = None) -> None:
    """Load `KEY=VALUE` lines from a .env file without overriding the real environment.

    Stdlib-only, matching `pipeline.config.load_dotenv`: `python-dotenv` is not on the
    approved dependency list.
    """
    path = Path(path) if path is not None else REPO_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _require(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise ConfigError(
            f"Missing required setting: {name}. Set it in .env (copy .env.example) or "
            "export it in the environment."
        )
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _split_tokens(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(t.strip() for t in raw.split(",") if t.strip())


@dataclass(frozen=True)
class Config:
    """Resolved Stage 2 settings for one run."""

    kb_path: Path
    database_url: str | None
    database_url_index: str | None
    ollama_base_url: str
    embed_model: str
    embed_dim: int
    llm_base_url: str
    llm_model: str
    kb_url_base: str | None
    kb_bind: str
    kb_public_host: str
    kb_tokens: tuple[str, ...]
    fetch_max_chars: int
    chunk_target: int
    chunk_max: int

    @property
    def kb_dir(self) -> Path:
        return self.kb_path / "kb"

    def require_database_url_index(self) -> str:
        if not self.database_url_index:
            raise ConfigError(
                "Missing required setting: DATABASE_URL_INDEX (needed for `kb index`). "
                "Set it in .env (copy .env.example)."
            )
        return self.database_url_index

    def require_database_url(self) -> str:
        if not self.database_url:
            raise ConfigError(
                "Missing required setting: DATABASE_URL (needed for `kb serve`/`kb "
                "search`). Set it in .env (copy .env.example)."
            )
        return self.database_url

    def require_kb_url_base(self) -> str:
        if not self.kb_url_base:
            raise ConfigError(
                "Missing required setting: KB_URL_BASE (needed for `kb serve`: citation "
                "urls have to point somewhere). Set it in .env (copy .env.example)."
            )
        return self.kb_url_base


def load(*, env_file: Path | str | None = None) -> Config:
    """Build a `Config` from the environment."""
    load_dotenv(env_file)

    kb_path = Path(
        os.environ.get("KB_PATH", str(REPO_ROOT.parent / "dgx-knowledge"))
    ).expanduser().resolve()

    kb_url_base = os.environ.get("KB_URL_BASE") or None
    if kb_url_base is not None:
        kb_url_base = kb_url_base.rstrip("/")

    return Config(
        kb_path=kb_path,
        database_url=os.environ.get("DATABASE_URL") or None,
        database_url_index=os.environ.get("DATABASE_URL_INDEX") or None,
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        embed_model=os.environ.get("EMBED_MODEL", "nomic-embed-text"),
        embed_dim=_env_int("EMBED_DIM", 768),
        llm_base_url=os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1"),
        llm_model=os.environ.get("LLM_MODEL", "granite4:3b"),
        kb_url_base=kb_url_base,
        kb_bind=os.environ.get("KB_BIND", "127.0.0.1:8765"),
        kb_public_host=os.environ.get("KB_PUBLIC_HOST", "localhost"),
        kb_tokens=_split_tokens(os.environ.get("KB_TOKENS")),
        fetch_max_chars=_env_int("FETCH_MAX_CHARS", 200_000),
        chunk_target=_env_int("CHUNK_TARGET", 1200),
        chunk_max=_env_int("CHUNK_MAX", 2500),
    )
