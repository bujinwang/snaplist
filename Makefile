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

.PHONY: help install test lint fmt evals evals-strict golden golden-provisional masks-provisional \
        seg-iou fetch-models dev dev-web dev-mobile contract clean

MODEL_DIR ?= $(if $(SNAPLIST_MODEL_DIR),$(SNAPLIST_MODEL_DIR),$(HOME)/.cache/snaplist/models)
U2NET_SHA256 := 8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491
U2NET_URL := https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx

help:
	@echo "SnapList targets:"
	@echo "  make install    uv sync + pnpm install  (no credentials required)"
	@echo "  make test       pnpm -w test, then pytest"
	@echo "  make lint       ruff check + tsc --noEmit in every TS package"
	@echo "  make evals      report only; prints UNMEASURED until E1..E4, always exit 0"
	@echo "  make evals-strict  release gate; deliberately fails until E0-T5 exists"
	@echo "  make golden     validate the E0-T1 golden set; exit 0 only when it is complete"
	@echo "  make golden-provisional  structure check of the web-sourced stand-in set"
	@echo "  make masks-provisional   regenerate its masks (background-colour arithmetic)"
	@echo "  make seg-iou    segment the provisional set and report IoU (evals/reports/seglab.md)"
	@echo "  make fetch-models  download the u2net weights (176 MB) into $(MODEL_DIR)"
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

# Golden-set check (docs/PLAN.md E0-T1). Exits 2 while the manifest does not exist — the set
# is empty until real photos are added, and an empty set must not look like a passing one.
golden:
	$(PY) -m evals.tools.validate_golden

# The provisional set (evals/golden-provisional/README.md). Web-sourced photos, algorithmic
# masks, structure checked only — the composition rules belong to the golden set, and saying
# so is cheaper than pretending a substitute set meets them.
golden-provisional:
	$(PY) -m evals.tools.validate_golden --golden-dir evals/golden-provisional --profile provisional

# Regenerate the provisional masks from the photos (numpy + PIL only, ~1 s per photo).
masks-provisional:
	$(PY) -m evals.tools.provisional_masks --golden-dir evals/golden-provisional --overwrite

# E1-T4, first half: segment every photo in the provisional set and report IoU. It reports,
# it does not gate — the denominator is algorithmic masks, so the number is a smoke signal.
seg-iou:
	$(PY) -m evals.tools.seg_iou --golden-dir evals/golden-provisional

# Model weights (docs/PLAN.md E1-T2). Deliberately NOT part of `install`: 176 MB and a
# network call must be an explicit act, not a side effect of a clone — and segment() refuses
# to download them itself, because a pipeline stage that fetches weights on first call is a
# network call inside a zero-network contract. Idempotent: a verified file is left alone.
fetch-models:
	@mkdir -p "$(MODEL_DIR)"
	@if [ -f "$(MODEL_DIR)/u2net.onnx" ] && [ "$$(shasum -a 256 "$(MODEL_DIR)/u2net.onnx" | cut -d' ' -f1)" = "$(U2NET_SHA256)" ]; then \
		echo "u2net.onnx already present and verified: $(MODEL_DIR)/u2net.onnx"; \
	else \
		echo "downloading u2net.onnx -> $(MODEL_DIR) (176 MB, once)"; \
		curl -fL --progress-bar -o "$(MODEL_DIR)/u2net.onnx.part" "$(U2NET_URL)" || { rm -f "$(MODEL_DIR)/u2net.onnx.part"; exit 1; }; \
		got=$$(shasum -a 256 "$(MODEL_DIR)/u2net.onnx.part" | cut -d' ' -f1); \
		if [ "$$got" != "$(U2NET_SHA256)" ]; then \
			echo "sha256 mismatch: got $$got, want $(U2NET_SHA256) — discarding the download"; \
			rm -f "$(MODEL_DIR)/u2net.onnx.part"; exit 1; \
		fi; \
		mv "$(MODEL_DIR)/u2net.onnx.part" "$(MODEL_DIR)/u2net.onnx"; \
		echo "verified sha256 $$got"; \
	fi

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
