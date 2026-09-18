# Document conversion.
#
# Needs `uv` on PATH and nothing else: it provisions Python 3.12 and every package itself.
# Run `uv sync` once first. See README "Setup".

UV ?= uv
PYTHON := $(UV) run python

.DEFAULT_GOAL := help

.PHONY: help sync check test gates license-gate model-gate fixtures \
        inventory triage prune convert report report-json answerability profile clean-work \
        index search serve serve-http eval-retrieval compose-up compose-down \
        compose-index compose-env-check fixtures-retrieval

help:  ## Show this help
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) \
		| sed 's/:.*##/\t/' \
		| awk -F'\t' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

sync:  ## Install the locked dependency set into .venv
	$(UV) sync

check: gates test  ## Everything CI should run: both policy gates, then the tests

test:  ## Run the test suite (tests/retrieval/ included automatically via testpaths)
	$(UV) run pytest

gates: license-gate model-gate  ## Run both policy gates

license-gate:  ## Fail on copyleft imported as a library (subprocess use is fine)
	$(PYTHON) scripts/license_gate.py

model-gate:  ## Fail on a model that is not licence- and provenance-cleared
	$(PYTHON) scripts/model_gate.py

fixtures:  ## Regenerate the committed test fixtures (should be a no-op)
	$(PYTHON) tests/make_fixtures.py

inventory:  ## Scan SOURCE_DIR and populate corpus.yaml
	$(UV) run pipeline inventory

triage:  ## Measure text-layer coverage and write metrics into corpus.yaml
	$(UV) run pipeline triage

prune:  ## Drop MISSING manifest entries and their kb/ output. Dry run; ARGS=--yes applies
	$(UV) run pipeline prune $(ARGS)

convert:  ## Convert SOURCE_DIR documents into kb/ markdown
	$(UV) run pipeline convert

report:  ## Print the coverage report
	$(UV) run pipeline report

report-json:  ## Print the coverage report as JSON
	$(UV) run pipeline report --format json

profile:  ## Print the evidence profile for SOURCE_DIR (counts only, no document text)
	$(PYTHON) scripts/profile_corpus.py

answerability:  ## Ask the converted kb/ a fixed set of questions with known answers
	$(UV) run pipeline answerability --cases tests/answerability/fixtures.yaml

clean-work:  ## Delete WORK_DIR. Costs time to rebuild, never information.
	$(PYTHON) -c "import shutil; from pipeline.config import load; \
		d = load().work_dir; shutil.rmtree(d, ignore_errors=True); print('removed', d)"

# --- Stage 2: index kb/ into Postgres and serve it over MCP (needs `uv sync --extra serve`)

index:  ## Index kb/ into Postgres. Pass flags with ARGS, e.g. make index ARGS=--init
	$(UV) run kb index $(ARGS)

search:  ## Search the index: make search Q="a query"
	$(UV) run kb search "$(Q)"

serve:  ## Run the MCP server over stdio (developer path)
	$(UV) run kb serve --transport stdio

serve-http:  ## Run the MCP server over HTTP from the host venv (needs KB_TOKENS set, or ARGS=--allow-anonymous)
	$(UV) run kb serve --transport http $(ARGS)

eval-retrieval:  ## Run the retrieval regression eval
	$(UV) run kb eval

# --env-file .env: docker compose otherwise resolves the *implicit* .env relative to the
# Compose file's own directory (compose/.env), not the repo root where `cp .env.example
# .env` puts it. --project-directory would also fix that, but it additionally changes
# where *relative bind-mount sources* (./init-db.sh, ./Caddyfile, ...) resolve from, which
# must stay relative to compose/ -- so --env-file alone is the correct fix here.
COMPOSE := docker compose -f compose/docker-compose.yml --env-file .env

# The prod stack bind-mounts $KB_PATH. Compose resolves a relative source against compose/,
# not the repo root, so a relative KB_PATH would silently mount the wrong (nonexistent)
# directory. Check before invoking Compose rather than after a container fails to start.
compose-env-check:
	@test -f .env || { echo "no .env: run 'cp .env.example .env' and set KB_PATH, KB_TOKENS, KB_PUBLIC_HOST, KB_URL_BASE"; exit 2; }
	@grep -qE '^KB_PATH=/' .env || { echo "KB_PATH in .env must be an ABSOLUTE path for the compose stack"; exit 2; }

compose-up: compose-env-check  ## Bring up the full stack (db, ollama, kb-mcp, kb-static, caddy) behind TLS on :443
	$(COMPOSE) --profile prod up -d --build

compose-down:  ## Tear down the prod compose stack (keeps volumes -- add ARGS=-v to also remove them)
	$(COMPOSE) --profile prod down $(ARGS)

compose-index: compose-env-check  ## Index kb/ from inside the compose network (one-off container)
	$(COMPOSE) run --rm kb-mcp kb index

fixtures-retrieval:  ## Regenerate tests/retrieval/fixtures/kb/ (should be a no-op)
	$(PYTHON) tests/retrieval/make_fixtures.py
