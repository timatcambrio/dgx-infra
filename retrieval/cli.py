"""`kb` — the Stage 2 CLI: `index`, `search`, `serve`, `eval`, `catalog`.

S0 (this milestone) only wires up `kb index --init`: applying the schema and creating the
read-only role. The other subcommands exist so `kb --help` shows the full shape, and exit 2
naming the milestone that adds them.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer

from . import config as config_module
from .config import Config, ConfigError

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Stage 2: index kb/ into Postgres and serve it to AI assistants over MCP.",
)

EXIT_NOT_IMPLEMENTED = 2
EXIT_CONFIG_ERROR = 2

ENV_FILE_OPTION = typer.Option(
    None, "--env-file", help="Path to a .env file. Defaults to the repo's .env."
)


def _load_config(env_file: Optional[Path]) -> Config:
    try:
        return config_module.load(env_file=env_file)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc


def _not_implemented(name: str, milestone: str) -> None:
    typer.secho(
        f"`kb {name}` is not implemented until {milestone}.", fg=typer.colors.YELLOW, err=True
    )
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


@app.command()
def index(
    init: bool = typer.Option(
        False, "--init", help="Apply the schema and roles, then exit (no indexing yet)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Reindex every document, even if its kb_sha256 is unchanged."
    ),
    reindex_all: bool = typer.Option(
        False,
        "--reindex-all",
        help="Truncate the content tables first (needed after EMBED_MODEL/EMBED_DIM changes).",
    ),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Walk kb/ and load it into Postgres."""
    cfg = _load_config(env_file)

    if init:
        _run_init(cfg)
        return

    # Imported lazily: asyncpg/pgvector/httpx live behind the `serve` extra, and a bare
    # `uv sync` must not need them merely to import retrieval.cli.
    from . import index as index_module

    try:
        result = asyncio.run(index_module.run_index(cfg, force=force, reindex_all=reindex_all))
    except index_module.IndexConfigError as exc:
        typer.echo(f"kb index: {exc}", err=True)
        raise typer.Exit(code=2)

    typer.echo(result.summary_line)
    for f, msg in result.errors:
        typer.secho(f"  error: {f}: {msg}", fg=typer.colors.RED, err=True)
    if result.errors:
        raise typer.Exit(code=1)


def _run_init(cfg: Config) -> None:
    async def _init() -> None:
        import asyncpg

        from . import db as db_module

        index_dsn = cfg.require_database_url_index()
        conn = await asyncpg.connect(index_dsn)
        try:
            await db_module.init_schema(
                conn,
                embed_model=cfg.embed_model,
                embed_dim=cfg.embed_dim,
                read_dsn=cfg.database_url,
            )
        finally:
            await conn.close()

    from . import db as db_module

    try:
        asyncio.run(_init())
    except db_module.ReadRoleMissing as exc:
        typer.echo(f"kb index --init: {exc}", err=True)
        raise typer.Exit(code=2)
    typer.echo(
        f"schema applied ({cfg.embed_model}, dim={cfg.embed_dim})"
        + (", read role granted SELECT" if cfg.database_url else "")
    )


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    k: int = typer.Option(8, "--k"),
    slug: Optional[str] = typer.Option(None, "--slug"),
    leg: Optional[str] = typer.Option(None, "--leg", help="lexical|vector|fused"),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Search the index. Not implemented until S2."""
    _load_config(env_file)
    _not_implemented("search", "S2")


@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio|streamable-http"),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Run the MCP server. Not implemented until S3 (stdio) / S4 (http)."""
    _load_config(env_file)
    _not_implemented("serve", "S3")


@app.command(name="eval")
def eval_(
    cases: Optional[Path] = typer.Option(None, "--cases"),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Run the retrieval regression eval. Not implemented until S2."""
    _load_config(env_file)
    _not_implemented("eval", "S2")


@app.command()
def catalog(
    summarize: bool = typer.Option(False, "--summarize"),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Document catalog / summaries. Not implemented until S5."""
    _load_config(env_file)
    _not_implemented("catalog", "S5")
