# SnapList — repo-level commands. GNU Make 3.81 compatible (macOS stock make):
# no .ONESHELL, no 4.x-only functions. Every recipe line is its own shell, so each line
# that needs the venv calls the venv interpreter by absolute path instead of activating.

SHELL := /bin/bash
ROOT  := $(shell pwd)

UV   ?= uv
PY   ?= $(ROOT)/.venv/bin/python
# pnpm is installed under ~/.local/bin, which non-login shells do not always have on PATH.
PNPM ?= $(shell command -v pnpm 2>/dev/null || echo $(HOME)/.local/bin/pnpm)

.DEFAULT_GOAL := help

.PHONY: help install test lint fmt evals evals-strict dev dev-web dev-mobile contract clean

help:
	@echo "SnapList targets:"
	@echo "  make install    uv sync + pnpm install  (no credentials required)"
	@echo "  make test       pnpm -w test, then pytest"
	@echo "  make lint       ruff check + tsc --noEmit in every TS package"
	@echo "  make evals      report only; prints UNMEASURED until E1..E4, always exit 0"
	@echo "  make evals-strict  release gate; deliberately fails until E0-T5 exists"
	@echo "  make dev        FastAPI on :8000 (uvicorn --reload)"
	@echo "  make dev-web    static marketing placeholder on :3000"
	@echo "  make dev-mobile expo dev server"
	@echo "  make contract   print the frozen ItemAttributes field list"
	@echo "  make clean      remove .venv, node_modules, dist, caches"

install:
	$(UV) sync
	$(PNPM) install

# JS first: packages/shared/build must exist before the python parity test can read it.
test:
	$(PNPM) -w test
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .
	$(PNPM) -w lint

fmt:
	$(PY) -m ruff format .

evals:
	$(PY) -m evals.run --suite vision --allow-inconclusive

# Release gate (docs/PLAN.md E0-T5). Deliberately fails until E0-T5 is implemented: a release
# must not be cut on a pipeline nobody has measured. `make evals` exits 0 but prints
# `GATE NOT EVALUATED` — never evidence for a release, and this target is what says so.
evals-strict:
	$(PY) -m evals.run --suite vision --gate

dev:
	$(PY) -m uvicorn apps.api.main:app --reload --port 8000

dev-web:
	$(PNPM) --filter web dev

dev-mobile:
	$(PNPM) --filter mobile start

contract:
	$(PY) -c "import apps.api.schemas as s; print(sorted(s.ItemAttributes.model_fields))"

clean:
	rm -rf .venv node_modules dist .pytest_cache .ruff_cache packages/shared/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
