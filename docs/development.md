# Development

For working on the code. Running the service is covered in [Administration](administration.md).

```bash
make check    # the test suite plus both policy gates. What CI runs.
make help     # every target
```

Without Postgres and the embedding server reachable, the `kb` tests skip rather than fail.
Start them first (`docker compose -f compose/docker-compose.yml --profile dev up -d db
ollama`) to run the whole suite.

## Running `kb` locally

To run `kb` against a host venv instead of the container stack:

```bash
uv sync --extra serve                      # installs kb's dependencies; plain `uv sync` does not
cp .env.example .env                       # then fill in the `kb` keys
docker compose -f compose/docker-compose.yml --profile dev up -d db ollama
uv run kb index --init                     # applies the database schema
uv run kb index                            # walks kb/, embeds it, loads it into Postgres
uv run kb serve --transport stdio          # runs the MCP server over stdio
```

The `kb` keys are described in
[What the `kb` keys in `.env` mean](administration.md#what-the-kb-keys-in-env-mean).

`kb --help` lists every subcommand (`index`, `search`, `serve`, `eval`, `catalog`); `catalog`
currently exits with "not implemented yet".

## License policy

The rule depends on how a dependency is called, whatever its licence string says. Copyleft
invoked as a separate program is fine, and LibreOffice is the one case of it. Copyleft
imported as a library is not. PyMuPDF and pymupdf4llm are AGPL and import-only, so they are
banned outright, including for a quick check.

`make license-gate` walks installed package metadata and fails if a GPL or AGPL package is
imported anywhere under `pipeline/`. False positives go in
`scripts/license_allowlist.yaml` with a written reason.

## Model policy

Models have to be permissively licensed and have non-Chinese base-weight provenance, judged
on the base weights rather than on whoever released them.

`make model-gate` checks two places, because they fail differently: model caches, and
weights shipped inside an installed wheel. The second case occurs in practice: the `docling`
meta-package installs `rapidocr`, whose wheel contains about 30MB of Baidu PaddleOCR weights
as ordinary files that never touch a cache. So this project depends on `docling-slim` with
named extras, never on `docling`.

`models.yaml` is the allowlist and it is empty on purpose. Nothing here should ever download
a model, so an empty allowlist plus a cache scan asserts that and fails the moment it
stops being true. Models a future conversion path would fetch are recorded under
`pending_review` with what is known about each.

## LibreOffice (subprocess only)

Legacy `.doc` and `.dot` conversion shells out to the `soffice` binary:

```bash
soffice --headless --convert-to docx --outdir "$WORK_DIR/doc2docx/" <file>
```

If `soffice` is missing the pipeline fails with an error naming it. There is no fallback to a
copyleft Python library.

> Pin the major version and match it across machines. LibreOffice's `.doc` import filter is
> not byte-stable between releases, and byte-stable output is a hard requirement here. The
> pin is not decided yet, so this path is currently unexercised.

## Platform constraint

`pyproject.toml` requires every locked dependency to have an installable wheel on both
linux-x86_64 and darwin-x86_64.

PyTorch ships no macOS x86_64 wheel after 2.2.2, and without the
constraint `uv lock` produces a lockfile that resolves cleanly and then will not install. If
`uv lock` starts failing, a dependency has dropped one of those platforms. Decide that
deliberately instead of dropping support by accident.

Because the two platforms can resolve different versions, byte-identical output across
platforms is not guaranteed. The requirement is per machine: the same input converted twice
on the same machine must be byte-identical, which `tests/test_determinism.py` enforces.
