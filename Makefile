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

.PHONY: help install test lint fmt evals evals-strict evals-report evals-vision \
        golden golden-provisional masks-provisional seg-iou fetch-models fetch-sdxl \
        install-sdxl dev dev-web dev-mobile contract clean

MODEL_DIR ?= $(if $(SNAPLIST_MODEL_DIR),$(SNAPLIST_MODEL_DIR),$(HOME)/.cache/snaplist/models)
U2NET_SHA256 := 8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491
U2NET_URL := https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx

help:
	@echo "SnapList targets:"
	@echo "  make install    uv sync + pnpm install  (no credentials; no torch, no SDXL)"
	@echo "                  note: uv sync prunes, so this UNINSTALLS the sdxl group if present"
	@echo "  make install-sdxl  uv sync --group sdxl + fetch-sdxl (0.72 GB + 6.9 GB, only for mode=sdxl_inpaint)"
	@echo "  make test       pnpm -w test, then pytest (telemetry suite now included)"
	@echo "  make lint       ruff check + tsc --noEmit in every TS package"
	@echo "  make evals      E0-T5 telemetry gate (dev/CI): --gate --allow-inconclusive; exits 0 or 1"
	@echo "  make evals-strict  E0-T5 telemetry release gate: --gate, never allows inconclusive"
	@echo "  make evals-report  telemetry report only, always exit 0 (no gate)"
	@echo "  make evals-vision  the E0-T2 vision harness (evals.run --suite vision)"
	@echo "  make golden     validate golden-set file/count structure; exit 0 only when those checks pass (mask provenance NOT verified)"
	@echo "  make golden-provisional  structure check of the web-sourced stand-in set"
	@echo "  make masks-provisional   regenerate its masks (background-colour arithmetic)"
	@echo "  make seg-iou    segment the provisional set and report IoU (evals/reports/seglab.md)"
	@echo "  make fetch-models  download the u2net weights (176 MB) into $(MODEL_DIR)"
	@echo "  make fetch-sdxl download the SDXL inpainting weights (6.9 GB, fp16) for mode=sdxl_inpaint"
	@echo "  make dev        FastAPI on :8000 (uvicorn --reload)"
	@echo "  make dev-web    static marketing placeholder on :3000"
	@echo "  make dev-mobile expo dev server"
	@echo "  make contract   print the frozen ItemAttributes field list"
	@echo "  make clean      remove .venv, node_modules, dist, caches"

# Lean on purpose: the generative stack lives in the `sdxl` dependency group and is NOT
# installed here. 0.72 GB of site-packages that only `mode=SDXL_INPAINT` needs must be an
# explicit act — see the comment above `[dependency-groups]` in pyproject.toml for the
# regression that making it mandatory caused.
install:
	$(UV) sync
	$(PNPM) install

# Everything `mode=SDXL_INPAINT` needs: the dependency group, then the pinned weights.
# Two separate downloads because they are two separate things to decide about.
install-sdxl:
	$(UV) sync --group sdxl
	$(PY) -c "from packages.vision.prettify import fetch_sdxl_weights as f; f()"

# JS first: packages/shared/build must exist before the python parity test can read it.
test:
	$(PNPM) -w test
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .
	$(PNPM) -w lint

fmt:
	$(PY) -m ruff format .

# E0-T5 telemetry gate (docs/PLAN.md E0-T5 acceptance). `make evals` is the dev / CI gate: it
# passes `--allow-inconclusive`, so an empty or unmeasured database (3a: source absent) is
# downgraded to exit 0 with a banner instead of blocking development. `make evals-strict` is the
# release gate and omits that flag, so an unmeasured pipeline cannot ship. R4 (docs/QA.md §10)
# closed the wiring gap that E0-T5 itself could not touch under its allowed-paths whitelist.
# `exec` makes the recipe process BE the telemetry gate, so the gate's real exit code is what
# the process returns. NOTE: GNU Make 3.81 (macOS stock) collapses ANY non-zero recipe exit to 2,
# so on this make the wrapper can only distinguish pass (0) vs fail (2); the precise FAIL (1) /
# INCONCLUSIBLE (2) / DATA_UNAVAILABLE (3) distinction is available by running
# `python -m evals.telemetry --since 7d --gate [--allow-inconclusive]` directly (it returns the
# correct code). On newer make, `exec` propagates the code exactly.
evals:
	exec $(PY) -m evals.telemetry --since 7d --gate --allow-inconclusive

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

# SDXL inpainting weights (docs/PLAN.md E2-T2 round 2). 6.9 GB, and for the same reason a
# separate target from fetch-models: nobody should pull 7 GB because they typed the target
# for the 176 MB segmentation file. Same refusal contract — `prettify(mode=SDXL_INPAINT)`
# raises with this target named rather than downloading — and the same "never a side effect
# of `make install`" rule. The version, revision, per-file byte counts and licence live in
# packages/vision/prettify.py (SDXL_REQUIRED_FILES) and docs/DEPS.md; the checks are there,
# not here, because the loader has to be able to make the same judgement.
fetch-sdxl:
	$(PY) -c "from packages.vision.prettify import fetch_sdxl_weights as f; f()"

# Release gate (docs/PLAN.md E0-T5). Runs the telemetry gate WITHOUT `--allow-inconclusive`, so
# an empty or unmeasured database (source absent, 3a) exits 3 — a release must not be cut on a
# pipeline nobody has measured. `make evals` (dev / CI) downgrades that to 0 with a banner.
evals-strict:
	exec $(PY) -m evals.telemetry --since 7d --gate

# Telemetry report only (docs/PLAN.md E0-T5 acceptance, third mode). Never gates; always exit 0.
evals-report:
	exec $(PY) -m evals.telemetry --since 7d

# The E0-T2 vision harness, preserved after `make evals` was repurposed to the telemetry gate.
# Invoked directly by the E0-T2 / E1-T4 / E2-T3 / E3-T3 acceptance commands.
evals-vision:
	$(PY) -m evals.run --suite vision

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
