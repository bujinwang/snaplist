# HANDOFF — vision (CV role)

Run date: 2026-11-04. Repo: `/Users/bujin/Documents/Projects/SnapList`.

## What shipped in this batch

**E3-T1 (registry) — landed and verified.** `packages/vision/attrs.py` now resolves
extractors by name. PLAN.md E3-T1 acceptance command runs verbatim:

```
$ python -c "from packages.vision.attrs import get_extractor; print(get_extractor('stub').name)"
stub
```

`available_extractors()` returns `("http", "stub")`. Provider is chosen by configuration
(`SNAPLIST_ATTRS_PROVIDER`), not by a code change in `apps/api` or `packages/vision/cli.py` —
the CLI calls `get_extractor()` with **no argument**. `attrs.py` imports no provider SDK;
`attrs_adapters/http.py` imports `httpx` lazily inside `extract()`. A subprocess test proves
`httpx` is absent from `sys.modules` after importing the package and resolving `http`.

**E3-T2 (wire machinery) — landed, real provider call NOT run.** `attrs_adapters/base.py`
holds the provider-independent half: `extra="forbid"` schema enforcement, exactly one repair
retry, evidence gating for `brand`/`material`, confidence defaults of 0.0 (never a guess),
and `needs_review` for sub-threshold fields. Failure after retry raises
`AttributeExtractionFailed(reason_code="low_confidence")`, never `internal` (CONTRACTS §4).

**E1–E2 pipeline** from the previous batch still runs end-to-end. Re-verified after the CLI
change on all 93 web-set images: `OK=93 FAIL=0`, mean 1.54 s/image (1.31–2.27 s), u2net on
CPU. `attributes.json` is emitted as the UNMEASURED marker in every one of those runs.

## What is broken / UNMEASURED — read this before claiming E3 is done

**The provider decision IS made: DeepSeek V4.1 Flash, Bert's call 2026-11-04.** See
`docs/ADR-006-attributes-vlm-provider.md` (official rate card, terms, sources), the hosted-service
row in `docs/DEPS.md`, the updated `docs/RUNBOOK.md §6`, and `.env.example` for the exact config.
API model id is **`deepseek-flash`** (not `deepseek-v4.1-flash`), base URL
`https://api.deepseek.com` with no `/v1`.

**But no `VLM_API_KEY` is present, so no real provider call has run.** There is no local model
(ollama installed, zero pulled). BRIEF.md forbids mocked model calls in the acceptance path.

Consequently:

* `attributes.json` from the CLI is currently an explicit `{"status": "UNMEASURED"}` marker
  naming the missing config. It is **not** a result and must not be read as one.
* E3-T2's real acceptance command has **not** produced a measured attribute set. Accuracy
  (E3-T3: category ≥ 0.95, colour ≥ 0.90, pattern ≥ 0.85, material ≥ 0.80) is
  **UNMEASURED**, and also blocked on E0-T1's 40 real photos / 20 hand masks.
* `cost_usd` stays `None`. Real `prompt_tokens` / `completion_tokens` are summed across the
  repair retry into `StageMeta.notes`; turning them into dollars needs a measured call.
* **OPEN legal items before seller photos are sent** (ADR-006, owner: legal): DeepSeek's privacy
  policy says personal data is processed/stored in the PRC and the Open Platform Terms are
  PRC-governed; a third-party tracker reports no DPA. `prompts/08-legal.md` §7 requires a
  subprocessor table with region, §5 a data map. These are [COUNSEL] items, not conclusions.

## DeepSeek-specific wiring (all in config, no vendor name in Python)

`SNAPLIST_VLM_EXTRA_BODY` carries `{"thinking":{"type":"disabled"}}`,
`{"response_format":{"type":"json_object"}}` and `max_tokens`. Thinking is ON by default and
would emit chain-of-thought while ignoring `temperature`; JSON mode requires the literal word
"json" and an example in the prompt (both added to `base.SYSTEM_PROMPT`). DeepSeek JSON mode can
replace unparseable output with whitespace — `_message_content()` raises `ValueError` so that
blank reply consumes exactly one repair retry. HTTP status/transport errors now raise
`AttributeExtractionFailed(reason_code="low_confidence")` instead of escaping as generic
exceptions that the worker would mislabel `internal` (CONTRACTS §4). Image MIME is sniffed
(JPEG/PNG/GIF/WebP — exactly DeepSeek's supported set).
* The `stub` extractor is a tests/CI fixture only (`model_version="stub:not-a-model"`, all
  confidences 0.0). Selecting it in the acceptance path would be a BRIEF violation.

## Escalation — what is still needed

The provider decision is done (ADR-006). Two things remain:

1. **A real `VLM_API_KEY`.** Without it no provider call has run, E3-T2's acceptance is
   UNMEASURED, and E7 (listing copy, which eats attributes) stays blocked on the critical path.
2. **Legal sign-off on the OPEN items in ADR-006** before any seller photo is sent. The privacy
   policy places personal-data processing/storage in the PRC and the terms are PRC-governed;
   `prompts/08-legal.md` §7 needs a subprocessor table with region. This is the one place where
   "cheapest model" is not the only axis — do not treat it as settled.

## Exact next commands

Put the key in the repo-root `.env` — gitignored (`.gitignore:19`), so it never reaches the
repo or the chat. Copy the values from `.env.example`; only `VLM_API_KEY` needs filling in.
`http._config_from_env()` loads it lazily with `find_dotenv(usecwd=True)`, `override=False`.

```bash
# 1. /Users/bujin/Documents/Projects/SnapList/.env
#    SNAPLIST_ATTRS_PROVIDER=http
#    SNAPLIST_VLM_BASE_URL=https://api.deepseek.com
#    SNAPLIST_VLM_MODEL=deepseek-flash
#    VLM_API_KEY=<key>
#    SNAPLIST_VLM_EXTRA_BODY={"thinking":{"type":"disabled"},
#      "response_format":{"type":"json_object"},"max_tokens":1024}

# 2. Real E3-T2 acceptance (prints provider round-trip seconds and token counts):
python -m packages.vision.cli --in <photo> --out /tmp/sn3 && cat /tmp/sn3/attributes.json

# 3. Verify everything else stays green:
make test      # 72 node + 301 pytest, green on 2026-11-04
make lint      # ruff clean
```

## Verification actually observed on 2026-11-04

```
$ make test
1..72 / # pass 72 / # fail 0           (node)
301 passed, 1 warning in 13.14s         (pytest)

$ python -m ruff check .
All checks passed!

$ python -m packages.vision.cli --in <web photo> --out /tmp/sn_ds --category top
OK backend=u2net device=cpu ... attributes -> attributes.json (UNMEASURED)
```

Note: pushing required a command-scoped credential override. macOS keychain hands git the
`bertwang32-alt` account, which cannot see the private `bujinwang/snaplist` repo, so GitHub
returns the misleading `Repository not found`; `gh` is logged in as `bujinwang`. No global
config was changed.

Mutation checks (all killed their target test, files restored afterwards): disabling the
evidence gate; changing the retry budget 2→3; moving `complete()` outside the try (killed
`test_complete_raising_value_error_still_gets_one_repair`); accepting blank content (killed
`test_message_content_rejects_blank_and_malformed_bodies`); dropping `extra_body` from the
payload (killed `test_extra_body_is_merged_into_the_actual_payload`). Two earlier mutations
survived the first pass and were fixed by adding those tests — worth remembering that a green
suite is not proof of coverage.
