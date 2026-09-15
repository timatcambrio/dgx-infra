# Stage 1 document conversion.
#
# Assumes the conda env is active (`conda activate dgx-infra`), which provides python 3.12
# and uv; uv owns every package from there. See README "Prerequisites".

UV ?= uv
PYTHON := $(UV) run python

.DEFAULT_GOAL := help

.PHONY: help sync check test gates license-gate model-gate fixtures \
        inventory triage convert report report-json answerability clean-work

help:  ## Show this help
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) \
		| sed 's/:.*##/\t/' \
		| awk -F'\t' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

sync:  ## Install the locked dependency set into .venv
	$(UV) sync

check: gates test  ## Everything CI should run: both policy gates, then the tests

test:  ## Run the test suite
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

convert:  ## Convert SOURCE_DIR documents into kb/ markdown
	$(UV) run pipeline convert

report:  ## Print the coverage report
	$(UV) run pipeline report

report-json:  ## Print the coverage report as JSON
	$(UV) run pipeline report --format json

answerability:  ## Ask the converted kb/ a fixed set of questions with known answers
	$(UV) run pipeline answerability --cases tests/answerability/fixtures.yaml

clean-work:  ## Delete WORK_DIR. Costs time to rebuild, never information.
	$(PYTHON) -c "import shutil; from pipeline.config import load; \
		d = load().work_dir; shutil.rmtree(d, ignore_errors=True); print('removed', d)"
