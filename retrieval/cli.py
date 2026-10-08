"""`kb` — the Stage 2 CLI: `index`, `search`, `serve`, `eval`, `catalog`.

`catalog` is not implemented yet: it exists so `kb --help` shows the full shape, and exits
2 saying so.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

from . import config as config_module
from . import tokens as tokens_module
from .config import Config, ConfigError
from .tokens import TokenFileError

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Stage 2: index kb/ into Postgres and serve it to AI assistants over MCP.",
    # The rich traceback's locals panel prints connection parameters, password included.
    pretty_exceptions_show_locals=False,
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


def _not_implemented(name: str) -> None:
    typer.secho(
        f"`kb {name}` is not implemented yet.", fg=typer.colors.YELLOW, err=True
    )
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


def _run_db(coro, *, what: str, dsn: str | None):
    """`asyncio.run` for commands that talk to Postgres, turning the three failures a user
    can fix into one line each: the server is not reachable, the credentials/database are
    wrong, or the schema was never applied. Anything else propagates unchanged."""
    import asyncpg  # noqa: PLC0415 - behind the `serve` extra

    try:
        return asyncio.run(coro)
    except asyncpg.UndefinedTableError as exc:
        # The fresh-deployment case. `make compose-up` + `make compose-index` -- the two
        # commands the quickstart gives -- used to end here in a raw traceback saying
        # `relation "index_meta" does not exist` and nothing about the step that was
        # missing, because the compose path had no `--init` of its own.
        typer.echo(
            f"{what}: the schema is not applied yet ({exc}). Apply it once, then index:\n"
            "  make compose-index ARGS=--init     # the compose stack\n"
            "  uv run kb index --init             # a host venv",
            err=True,
        )
        raise typer.Exit(code=2)
    except (ConnectionRefusedError, OSError) as exc:
        where = _dsn_host_port(dsn)
        typer.echo(
            f"{what}: cannot reach Postgres at {where} ({exc.__class__.__name__}). "
            "Is it running? For the developer path:\n"
            "  docker compose -f compose/docker-compose.yml --profile dev up -d db",
            err=True,
        )
        raise typer.Exit(code=2)
    except (asyncpg.InvalidPasswordError, asyncpg.InvalidCatalogNameError,
            asyncpg.InvalidAuthorizationSpecificationError) as exc:
        typer.echo(
            f"{what}: Postgres at {_dsn_host_port(dsn)} refused the login: {exc}. "
            "Check DATABASE_URL / DATABASE_URL_INDEX in .env against compose/init-db.sh.",
            err=True,
        )
        raise typer.Exit(code=2)


def _dsn_host_port(dsn: str | None) -> str:
    from urllib.parse import urlsplit  # noqa: PLC0415

    if not dsn:
        return "(unset)"
    parts = urlsplit(dsn)
    return f"{parts.hostname or 'localhost'}:{parts.port or 5432}"


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
        result = _run_db(
            index_module.run_index(cfg, force=force, reindex_all=reindex_all),
            what="kb index",
            dsn=cfg.database_url_index,
        )
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
        _run_db(_init(), what="kb index --init", dsn=cfg.database_url_index)
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
    text_class: Optional[str] = typer.Option(None, "--text-class"),
    leg: Optional[str] = typer.Option(None, "--leg", help="lexical|vector|fused"),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Search the index and print one hit per line, with its citation."""
    cfg = _load_config(env_file)
    leg_name = leg or "fused"
    if leg_name not in ("lexical", "vector", "fused"):
        typer.secho(
            f"--leg must be lexical|vector|fused, got {leg_name!r}", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

    # Imported lazily: see the note on `index`'s import above.
    from . import cite as cite_module
    from . import ids as ids_module
    from . import search as search_module
    from .embed import embed_query

    filters = {}
    if slug:
        filters["slug"] = slug
    if text_class:
        filters["text_class"] = text_class

    async def _run() -> None:
        import asyncpg
        from pgvector.asyncpg import register_vector

        dsn = cfg.require_database_url()
        conn = await asyncpg.connect(dsn)
        try:
            await register_vector(conn)

            def embed_fn(q: str) -> list[float]:
                return embed_query(
                    q,
                    base_url=cfg.ollama_base_url,
                    model=cfg.embed_model,
                    embed_dim=cfg.embed_dim,
                )

            hits = await search_module.search(
                conn,
                query,
                k=k,
                filters=filters or None,
                embed_query_fn=embed_fn,
                leg=leg_name,
            )
            for h in hits:
                pages = (
                    f"{h.page_first}–{h.page_last}" if h.page_first is not None else "n/a"
                )
                sec_id = ids_module.sec_id(h.slug, h.section_index)
                typer.echo(
                    f"{h.score:.4f}  {sec_id}  pages {pages}  {h.heading_path}  |  {h.snippet}"
                )
                source_file = await cite_module.fetch_source_file(conn, h.slug)
                first_id, last_id = await cite_module.fetch_block_ids(
                    conn, h.slug, h.block_first, h.block_last
                )
                citation = cite_module.build_citation(
                    title=h.title,
                    heading_path=h.heading_path,
                    source_file=source_file,
                    page_first=h.page_first,
                    page_last=h.page_last,
                    doc_date=h.doc_date,
                    first_block_id=first_id,
                    last_block_id=last_id,
                )
                typer.echo(f"    {citation}")
        finally:
            await conn.close()

    _run_db(_run(), what="kb search", dsn=cfg.database_url)


@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio|http"),
    allow_anonymous: bool = typer.Option(
        False,
        "--allow-anonymous",
        help=(
            "Start `--transport http` even with KB_TOKENS empty (dev only). Every "
            "request to /mcp* is then accepted with no authentication."
        ),
    ),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Run the MCP server."""
    cfg = _load_config(env_file)

    if transport not in ("stdio", "http", "streamable-http"):
        typer.secho(
            f"--transport must be stdio|http, got {transport!r}", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

    try:
        cfg.require_database_url()
        cfg.require_kb_url_base()
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc

    # Imported lazily: see the note on `index`'s import above — asyncpg/mcp live behind
    # the `serve` extra.
    from . import server as server_module

    if transport == "stdio":
        mcp_server = server_module.build_server(cfg)
        mcp_server.run(transport="stdio")
        return

    # --transport http|streamable-http: refuse an
    # unauthenticated HTTP server unless the operator explicitly opts in. The question is
    # now "can anything authenticate", answered by the store across both sources (the
    # token file and legacy KB_TOKENS), not "is KB_TOKENS non-empty".
    from . import auth as auth_module

    store = auth_module.build_store(cfg, allow_anonymous=allow_anonymous)
    try:
        has_credential = store.has_any_credential()
    except TokenFileError as exc:
        # A broken token file must not start a server that authenticates no one. Once the
        # server is up a later bad edit keeps the last good copy instead (tokens.py).
        typer.secho(f"kb serve --transport http: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if not has_credential:
        where = cfg.kb_tokens_file or "(KB_TOKENS_FILE unset)"
        if not allow_anonymous:
            typer.secho(
                "kb serve --transport http: no usable credential. Refusing to start an "
                "unauthenticated HTTP server. Issue one with `kb token issue <user>` "
                f"(store: {where}), or pass --allow-anonymous to start anyway (dev only).",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        typer.secho(
            "kb serve --transport http: no usable credential and --allow-anonymous was "
            "passed. Every request to /mcp* will be accepted with NO authentication. Do "
            "not run this against a network you do not fully control.",
            fg=typer.colors.RED,
            err=True,
        )
    elif cfg.kb_tokens:
        typer.secho(
            "kb serve --transport http: KB_TOKENS is set. Those tokens work, but they "
            "are plaintext in .env, name no owner, and cannot be revoked without a "
            "restart that drops every user. Prefer `kb token issue <user>`.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    import uvicorn

    # The same store instance the pre-flight check used, so there is exactly one copy
    # of the cache and no chance of the two disagreeing.
    http_app = server_module.build_http_app(cfg, store=store)
    uvicorn.run(http_app, host=cfg.kb_bind_host, port=cfg.kb_bind_port, log_level="info")


@app.command(name="eval")
def eval_(
    cases: Optional[Path] = typer.Option(
        None, "--cases", help="Defaults to eval/retrieval.yaml."
    ),
    k: int = typer.Option(5, "--k"),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Run the retrieval regression eval and print the hit-rate table."""
    cfg = _load_config(env_file)

    from . import eval as eval_module
    from .embed import embed_query

    cases_path = cases or eval_module.DEFAULT_CASES_PATH
    case_list = eval_module.load_cases(cases_path)

    async def _run() -> eval_module.EvalResult:
        import asyncpg
        from pgvector.asyncpg import register_vector

        dsn = cfg.require_database_url()
        conn = await asyncpg.connect(dsn)
        try:
            await register_vector(conn)

            def embed_fn(q: str) -> list[float]:
                return embed_query(
                    q,
                    base_url=cfg.ollama_base_url,
                    model=cfg.embed_model,
                    embed_dim=cfg.embed_dim,
                )

            return await eval_module.run_eval(conn, case_list, k=k, embed_query_fn=embed_fn)
        finally:
            await conn.close()

    result = _run_db(_run(), what="kb eval", dsn=cfg.database_url)
    for line in result.table_lines:
        typer.echo(line)
    typer.echo("")
    for leg_name in eval_module.LEGS:
        typer.echo(f"{leg_name} hit@{k}: {result.rate(leg_name):.0%}")


@app.command()
def catalog(
    summarize: bool = typer.Option(False, "--summarize"),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Document catalog / summaries. Not implemented yet."""
    _load_config(env_file)
    _not_implemented("catalog")


# --------------------------------------------------------------------------------------
# `kb token` — issuing and revoking the HTTP transport's bearer credentials.
#
# The v1 issuance surface, for an operator on the DGX. The proposal's unified web app
# replaces this for non-technical users; the file format underneath is the same either
# way, so that later work is a new front end over this, not a migration.
#
# These commands run wherever the token file lives, NOT inside the kb-mcp container: the
# container bind-mounts the file read-only and re-reads it as it changes (`tokens.py`), so
# nothing here needs the server running and nothing restarts it.
# --------------------------------------------------------------------------------------

token_app = typer.Typer(
    no_args_is_help=True,
    help="Issue, list and revoke the bearer tokens `kb serve --transport http` accepts.",
)
app.add_typer(token_app, name="token")


def _tokens_path(cfg: Config) -> Path:
    if cfg.kb_tokens_file is None:
        typer.secho(
            "KB_TOKENS_FILE is set to an empty value, so there is no token store to "
            "modify. Unset it to use the default, or point it at a path.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)
    return cfg.kb_tokens_file


def _token_error(exc: TokenFileError) -> "typer.Exit":
    typer.secho(str(exc), fg=typer.colors.RED, err=True)
    return typer.Exit(EXIT_CONFIG_ERROR)


@token_app.command("issue")
def token_issue(
    user: str = typer.Argument(..., help="Who this token identifies — an email or username."),
    expires_in_days: Optional[int] = typer.Option(
        None,
        "--expires-in-days",
        help=(
            "Stop accepting it after this many days. Use it for a rotation overlap "
            "window: issue the replacement, set an expiry on the old one, and neither a "
            "restart nor a flag day is involved."
        ),
    ),
    note: Optional[str] = typer.Option(None, "--note", help="Free text, e.g. the machine."),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Generate a token for one person and print it ONCE."""
    cfg = _load_config(env_file)
    path = _tokens_path(cfg)

    expires = None
    if expires_in_days is not None:
        if expires_in_days <= 0:
            typer.secho("--expires-in-days must be positive.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        expires = (
            datetime.now(timezone.utc).replace(microsecond=0)
            + timedelta(days=expires_in_days)
        ).isoformat()

    try:
        secret, record = tokens_module.issue(path, user, expires=expires, note=note)
    except TokenFileError as exc:
        raise _token_error(exc) from exc

    # The secret is not stored, so this is the only time it can be shown. Said plainly,
    # because the recovery ("issue another, revoke this one") is cheap but not obvious.
    typer.echo(secret)
    typer.secho(
        f"\nIssued {record.id} to {record.user}"
        + (f", expires {record.expires}" if record.expires else "")
        + f". Stored (hashed) in {path}.\n"
        "The token above is shown once and is not recoverable — send it to its owner now. "
        "If it is lost, issue another and revoke this one. It takes effect within "
        f"{cfg.kb_token_cache_seconds:g}s; no restart.",
        fg=typer.colors.YELLOW,
        err=True,
    )


@token_app.command("list")
def token_list(
    all_: bool = typer.Option(
        False, "--all", help="Include revoked and expired tokens (default: active only)."
    ),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Every token in the store: id, owner, status. Never the secrets."""
    cfg = _load_config(env_file)
    path = _tokens_path(cfg)
    try:
        records = tokens_module.read(path)
    except TokenFileError as exc:
        raise _token_error(exc) from exc

    shown = [r for r in records if all_ or r.status == "active"]
    if not shown:
        typer.echo(
            f"No {'' if all_ else 'active '}tokens in {path}. "
            "Issue one with `kb token issue <user>`."
        )
    else:
        width = max(len(r.user) for r in shown)
        for record in shown:
            line = f"{record.id}  {record.user:<{width}}  {record.status:<8}  issued {record.issued}"
            if record.expires:
                line += f"  expires {record.expires}"
            if record.revoked:
                line += f"  revoked {record.revoked}"
            if record.note:
                line += f"  # {record.note}"
            typer.echo(line)

    if cfg.kb_tokens:
        # Not in the file and not listed above, but they do authenticate — say so, or the
        # list reads as the complete set of who has access, which it would not be.
        typer.secho(
            f"\nAlso accepted: {len(cfg.kb_tokens)} token(s) from KB_TOKENS in .env, which "
            "name no owner and cannot be revoked here — only by editing .env and "
            "restarting the server, which drops every user.",
            fg=typer.colors.YELLOW,
            err=True,
        )


@token_app.command("revoke")
def token_revoke(
    token_id: str = typer.Argument(..., help="The id from `kb token list`."),
    env_file: Optional[Path] = ENV_FILE_OPTION,
) -> None:
    """Withdraw one token, now. Nobody else is affected and nothing restarts."""
    cfg = _load_config(env_file)
    path = _tokens_path(cfg)
    try:
        record = tokens_module.revoke(path, token_id)
    except TokenFileError as exc:
        raise _token_error(exc) from exc
    typer.echo(
        f"Revoked {record.id} ({record.user}) at {record.revoked}. "
        f"In force within {cfg.kb_token_cache_seconds:g}s; no restart, no other user affected."
    )
