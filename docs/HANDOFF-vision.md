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

**The VLM provider is still Bert's decision and has not been made.** `docs/RUNBOOK.md §6`
(status: undecided, "Do not invent a provider name, a price or an accuracy figure"),
`docs/HANDOFF-product.md` ("待 Bert 拍") and `docs/HANDOFF-lead.md` ("Escalate early") all
agree. There is no `VLM_API_KEY` and no local model (ollama is installed but has zero pulled
models). BRIEF.md forbids mocked model calls in the acceptance path.

Consequently:

* `attributes.json` from the CLI is currently an explicit `{"status": "UNMEASURED"}` marker
  naming the three missing config vars. It is **not** a result and must not be read as one.
* E3-T2's real acceptance command (`python -m packages.vision.cli ... && cat attributes.json`)
  has **not** produced a measured attribute set. Accuracy (E3-T3: category ≥ 0.95,
  colour ≥ 0.90, pattern ≥ 0.85, material ≥ 0.80) is **UNMEASURED**, and also blocked on
  E0-T1's 40 real photos / 20 hand masks.
* No ADR naming a provider, price or licence was written — doing so would invent the very
  figures RUNBOOK §6 forbids. `docs/DEPS.md` needs no new row: `httpx` 0.27.2 was already
  listed, and its existing reason already says "the client the eval harness will use for
  real provider calls".
* The `stub` extractor is a tests/CI fixture only (`model_version="stub:not-a-model"`, all
  confidences 0.0). Selecting it in the acceptance path would be a BRIEF violation.

## Escalation — the one decision needed from Bert

Pick the attributes VLM provider. That fixes `SNAPLIST_VLM_BASE_URL`,
`SNAPLIST_VLM_MODEL` and `VLM_API_KEY`, unlocks a real round-trip, and lets the CV role write
ADR-006 (provider, price, licence) plus the DEPS row. Until then E3-T2/T3 cannot be measured
and E7 (listing copy generator, which eats attributes) stays blocked on the critical path.
Cheapest local alternative: pull a small vision model into ollama and point
`SNAPLIST_VLM_BASE_URL` at its OpenAI-compatible endpoint — no vendor, no key, but accuracy
would still need E0-T1's golden set to be measured.

## Exact next commands

Bert chose the hosted-key route on 2026-11-04. Put the key in the repo-root `.env` — it is
gitignored (`.gitignore:19`), so it never reaches the repo or the chat. `http._config_from_env()`
loads it lazily with `find_dotenv(usecwd=True)`, `override=False` (an exported var still wins).

```bash
# 1. Bert's decision, then create /Users/bujin/Documents/Projects/SnapList/.env:
#    SNAPLIST_VLM_BASE_URL=https://<chosen-provider>/v1
#    SNAPLIST_VLM_MODEL=<model id>
#    VLM_API_KEY=<key>
#    (or export the same three in the shell)

# 2. Real E3-T2 acceptance (prints provider round-trip seconds):
python -m packages.vision.cli --in <photo> --out /tmp/sn3 && cat /tmp/sn3/attributes.json

# 3. Verify everything else stays green:
make test      # 72 node + 292 pytest, green on 2026-11-04
make lint      # ruff clean
```

## Verification actually observed on 2026-11-04

```
$ make test
1..72 / # pass 72 / # fail 0           (node)
293 passed, 1 warning in 14.77s         (pytest)

$ python -m ruff check .
All checks passed!
```

Note: pushing required a command-scoped credential override. macOS keychain hands git the
`bertwang32-alt` account, which cannot see the private `bujinwang/snaplist` repo, so GitHub
returns the misleading `Repository not found`; `gh` is logged in as `bujinwang`. No global
config was changed.

Mutation check (both killed the tests, file restored afterwards): disabling the evidence
gate made `test_unevidenced_brand_and_material_dropped` and
`test_bare_attributes_object_is_accepted` fail; changing the retry budget from 2 to 3 made
`test_two_failures_raise_low_confidence` fail.
