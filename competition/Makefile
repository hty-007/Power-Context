.DEFAULT_GOAL := help
.PHONY: help install test check-baseline docker-build docker-test test-csv test-pi test-pdf test-rerank test-incremental test-count-trigger test-embedding test-lifecycle test-evidence test-hybrid

PRODUCT ?= $(abspath ..)
PYTHON := $(CURDIR)/.venv/bin/python
export POWERCONTEXT_CANDIDATE := $(PRODUCT)
export POWERCONTEXT_PUBLIC := $(CURDIR)
export PYTHONPATH := $(PRODUCT)/src:$(CURDIR)
export PYTHONDONTWRITEBYTECODE := 1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD := 1
export PYTHONHASHSEED := 0
export OMP_NUM_THREADS := 1
export OPENBLAS_NUM_THREADS := 1
export TZ := UTC

help:
	@printf '%s\n' 'make install  Install the locked Python 3.12 self-test environment' 'make test  Run every published code-task test' 'make test-csv / test-pi / test-pdf / test-rerank / test-incremental' 'make test-count-trigger / test-embedding / test-lifecycle / test-evidence / test-hybrid' 'make check-baseline  Inspect local frozen-baseline ancestry' 'make docker-build / docker-test  Build and run the shared offline self-test container' 'Set PRODUCT to another product checkout when needed.'

install:
	uv venv --python 3.12 .venv
	uv pip sync --python "$(PYTHON)" requirements.txt

check-baseline:
	"$(PYTHON)" -m selftest_support.check_baseline

test:
	$(MAKE) -k test-csv test-pi test-pdf test-rerank test-incremental test-count-trigger test-embedding test-lifecycle test-evidence test-hybrid

test-csv:
	"$(PYTHON)" -m pytest -q -c pytest.ini --confcutdir=. challenges/source-01-table-evidence/acceptance

test-pi:
	"$(PYTHON)" -m pytest -q -c pytest.ini --confcutdir=. challenges/source-02-pi-history/acceptance

test-pdf:
	"$(PYTHON)" -m pytest -q -c pytest.ini --confcutdir=. challenges/source-03-pdf-retrieval/acceptance

test-rerank:
	"$(PYTHON)" -m pytest -q -c pytest.ini --confcutdir=. challenges/retrieval-01-reranking/acceptance

test-incremental:
	"$(PYTHON)" -m pytest -q -c pytest.ini --confcutdir=. challenges/context-02-incremental-pack/acceptance

test-count-trigger:
	"$(PYTHON)" -m pytest -q -c pytest.ini --confcutdir=. challenges/processing-01-count-trigger/acceptance

test-embedding:
	"$(PYTHON)" -m pytest -q -c pytest.ini --confcutdir=. challenges/memory-02-embedding-batches/acceptance

test-lifecycle:
	"$(PYTHON)" -m pytest -q -c pytest.ini --confcutdir=. challenges/memory-01-source-lifecycle/acceptance

test-evidence:
	"$(PYTHON)" -m pytest -q -c pytest.ini --confcutdir=. challenges/context-01-evidence-selection/acceptance

test-hybrid:
	"$(PYTHON)" -m pytest -q -c pytest.ini --confcutdir=. challenges/retrieval-02-hybrid-search/acceptance

docker-build:
	docker compose -f compose.yaml build selftest

docker-test:
	docker compose -f compose.yaml run --rm selftest
