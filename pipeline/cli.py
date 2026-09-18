"""CLI: `inventory`, `triage`, `convert`, `report`.

Every command is idempotent and safe to re-run. Every command takes `--source-dir`, falling
back to `SOURCE_DIR` in the environment, and fails with an actionable message if neither is
set rather than defaulting to anything inside the repo.

`SOURCE_DIR` is read-only. Nothing here writes, moves, renames, or deletes under it.
"""

from __future__ import annotations

import fnmatch
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import typer

from . import StopAndAsk
from . import config as config_module
from . import manifest as manifest_module
from . import answerability as answerability_module
from . import report as report_module
from .config import SUPPORTED_EXTENSIONS, Config, ConfigError
from .convert import SourceDigestMismatch, convert_entry
from .manifest import STATUS_MISSING, STATUS_PRESENT
from .triage import triage_pdf, triage_text_native

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Stage 1: documents -> markdown, with a text-layer coverage report.",
)

SOURCE_DIR_OPTION = typer.Option(
    None,
    "--source-dir",
    help="Directory holding the source documents. Defaults to SOURCE_DIR from .env.",
)
ONLY_OPTION = typer.Option(
    None,
    "--only",
    help="Restrict to entries whose file name or slug contains this text or matches this "
    "glob. Case-insensitive.",
)

EXIT_STOP_AND_ASK = 2


def _load_config(source_dir: Path | None) -> Config:
    try:
        return config_module.load(source_dir)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def _resolved_source(config: Config) -> Path:
    try:
        return config.source_dir()
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def _selected(manifest: dict[str, Any], only: str | None) -> list[dict[str, Any]]:
    """Entries whose source file name or slug matches `only`.

    Case-insensitive, and a match is either a substring or a glob, so `--only dfarspgi`,
    `--only DFARSPGI`, `--only DFARSPGI.docx` and `--only '*.docx'` all do what they look
    like they do. `fnmatch.fnmatch` alone was neither: it follows the OS's case rule (macOS
    and Linux: sensitive) and it is a whole-string glob, so `--only DFARSPGI` silently
    matched nothing.
    """
    entries = manifest.get("documents", [])
    if not only:
        return entries
    needle = only.lower()

    def matches(value: str) -> bool:
        value = value.lower()
        return needle in value or fnmatch.fnmatchcase(value, needle)

    return [
        entry
        for entry in entries
        if matches(entry.get("source_file", "")) or matches(entry.get("slug", ""))
    ]


def _is_hidden(path: Path, root: Path) -> bool:
    """Skip dotfiles and anything inside a dot-directory, including macOS .DS_Store."""
    return any(part.startswith(".") for part in path.relative_to(root).parts)


@app.command()
def inventory(
    source_dir: Optional[Path] = SOURCE_DIR_OPTION,
) -> None:
    """Scan SOURCE_DIR and upsert corpus.yaml entries. Copies nothing, writes nothing there."""
    config = _load_config(source_dir)
    root = _resolved_source(config)
    manifest = manifest_module.load(config.manifest_path)

    outcomes: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    seen: set[str] = set()

    for path in sorted(root.rglob("*")):
        if not path.is_file() or _is_hidden(path, root):
            continue
        if manifest_module.format_for(path) is None:
            skipped[path.suffix.lower() or "(no extension)"] += 1
            continue
        entry, outcome = manifest_module.upsert(manifest, path, root)
        outcomes[outcome] += 1
        seen.add(entry["source_file"])

    for entry in manifest.get("documents", []):
        if entry["source_file"] not in seen and entry.get("status") == STATUS_PRESENT:
            manifest_module.mark_missing(entry)
            outcomes["missing"] += 1

    manifest["skipped_extensions"] = dict(sorted(skipped.items()))
    manifest_module.save(manifest, config.manifest_path)

    typer.echo(f"Source directory : {root}")
    typer.echo(f"Manifest         : {config.manifest_path}")
    for outcome in ("added", "changed", "unchanged", "restored", "missing"):
        if outcomes[outcome]:
            typer.echo(f"  {outcome:<10}: {outcomes[outcome]}")
    if not outcomes:
        typer.echo(
            "  no supported documents found "
            f"(supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))})"
        )

    if skipped:
        typer.echo("\nSkipped, unsupported extension:")
        for extension, count in sorted(skipped.items()):
            typer.echo(f"  {extension:<12}: {count}")


@app.command()
def triage(
    source_dir: Optional[Path] = SOURCE_DIR_OPTION,
    only: Optional[str] = ONLY_OPTION,
) -> None:
    """Measure text-layer coverage per document and write the metrics into corpus.yaml."""
    config = _load_config(source_dir)
    root = _resolved_source(config)
    manifest = manifest_module.load(config.manifest_path)
    entries = _selected(manifest, only)

    if not entries:
        typer.echo("Nothing to triage. Run `pipeline inventory` first.")
        return

    for entry in entries:
        source_file = entry["source_file"]
        path = root / source_file
        if not path.is_file():
            manifest_module.mark_missing(entry)
            typer.echo(f"  MISSING   {source_file}")
            continue

        entry["status"] = STATUS_PRESENT
        if entry["source_format"] == "pdf":
            result = triage_pdf(path, config)
        else:
            result = triage_text_native(path, config)
        entry["triage"] = result.to_dict()

        detail = (
            f"pages={result.page_count} median={result.chars_per_page_median} "
            f"alpha={result.alpha_ratio}"
            if result.page_count
            else "no page metrics (text-native format)"
        )
        typer.echo(f"  {result.text_class:<9} {source_file}  [{detail}]")

    manifest_module.save(manifest, config.manifest_path)
    typer.echo(f"\nMetrics written to {config.manifest_path}")


@app.command()
def convert(
    source_dir: Optional[Path] = SOURCE_DIR_OPTION,
    only: Optional[str] = ONLY_OPTION,
    force: bool = typer.Option(False, "--force", help="Re-convert even if unchanged."),
) -> None:
    """Convert SOURCE_DIR documents into kb/ markdown."""
    config = _load_config(source_dir)
    _resolved_source(config)
    manifest = manifest_module.load(config.manifest_path)
    entries = _selected(manifest, only)

    if not entries:
        typer.echo("Nothing to convert. Run `pipeline inventory` first.")
        return

    stop_and_ask: list[str] = []
    unavailable: list[str] = []
    errors: list[str] = []

    for entry in entries:
        if entry.get("status") == STATUS_MISSING:
            typer.echo(f"  MISSING        {entry['source_file']}")
            continue
        try:
            result = convert_entry(entry, config, force=force)
            entry.pop("conversion_error", None)
        except SourceDigestMismatch as exc:
            typer.secho(f"  DIGEST MISMATCH {entry['source_file']}", fg=typer.colors.RED)
            typer.secho(f"    {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
        except NotImplementedError as exc:
            unavailable.append(f"{entry['source_file']}: {exc}")
            typer.echo(f"  UNAVAILABLE    {entry['source_file']}")
            continue
        except StopAndAsk as exc:
            stop_and_ask.append(f"{entry['source_file']}: {exc}")
            typer.echo(f"  STOP-AND-ASK   {entry['source_file']}")
            continue
        except Exception as exc:  # noqa: BLE001 - one document must not end the run
            # A converter failure is a fact about one document. It is recorded on that
            # entry, reported at the end, and the run carries on, so a fourteen-file corpus
            # does not stop at file eight. The exit code still says something failed.
            message = f"{type(exc).__name__}: {exc}"
            entry["conversion_error"] = message
            errors.append(f"{entry['source_file']}: {message}")
            typer.secho(f"  ERROR          {entry['source_file']}", fg=typer.colors.RED)
            typer.secho(f"    {message}", fg=typer.colors.RED, err=True)
            continue

        if result.status == "stop_and_ask":
            stop_and_ask.append(f"{result.source_file}: {result.message}")
            typer.echo(f"  STOP-AND-ASK   {result.source_file}")
        else:
            typer.echo(f"  {result.status:<14} {result.source_file}")

    manifest_module.save(manifest, config.manifest_path)

    if unavailable:
        typer.echo("\nNot converted -- the requested converter is unavailable:")
        for item in unavailable:
            typer.echo(f"  * {item}")

    if stop_and_ask:
        typer.secho("\nSTOP AND ASK", fg=typer.colors.YELLOW, bold=True)
        for item in stop_and_ask:
            typer.secho(f"  * {item}", fg=typer.colors.YELLOW)

    if errors:
        typer.secho("\nFAILED TO CONVERT (recorded in corpus.yaml as conversion_error)", fg=typer.colors.RED, bold=True)
        for item in errors:
            typer.secho(f"  * {item}", fg=typer.colors.RED)
        raise typer.Exit(1)
        raise typer.Exit(EXIT_STOP_AND_ASK)


@app.command()
def report(
    source_dir: Optional[Path] = SOURCE_DIR_OPTION,
    output_format: str = typer.Option(
        "table", "--format", help="table or json.", metavar="table|json"
    ),
) -> None:
    """Print the coverage and conversion report.

    Reads only the manifest, so it works without SOURCE_DIR being set or reachable.
    """
    if output_format not in ("table", "json"):
        typer.secho("--format must be 'table' or 'json'", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(source_dir)
    manifest = manifest_module.load(config.manifest_path)
    if not manifest.get("documents"):
        typer.secho(
            f"No documents in {config.manifest_path}. Run `pipeline inventory` first.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(1)

    built = report_module.build(manifest, config)
    if output_format == "json":
        typer.echo(report_module.render_json(built))
    else:
        typer.echo(report_module.render_table(built))

    if built["stop_and_ask"] and output_format == "table":
        sys.exit(EXIT_STOP_AND_ASK)


@app.command()
def answerability(
    cases: Path = typer.Option(
        ..., "--cases", help="YAML file of questions with known answers.", metavar="PATH"
    ),
    source_dir: Optional[Path] = SOURCE_DIR_OPTION,
) -> None:
    """Check that converted markdown can still answer a fixed set of known questions.

    Goldens catch change. They cannot catch output that is byte-stable and useless, which is
    what every silent loss in this pipeline has looked like: a grid flattened into prose, a
    callout stranded from its field. This asks the converted files real questions instead.

    Reads only `kb/`, so it needs neither the source documents nor SOURCE_DIR. Exits non-zero
    on any failure, so it can gate a release.
    """
    config = _load_config(source_dir)
    try:
        loaded = answerability_module.load_cases(cases)
    except answerability_module.SpecError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    results = answerability_module.run(loaded, config.kb_dir)
    typer.echo(answerability_module.render(results))
    if any(not result.passed for result in results):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
