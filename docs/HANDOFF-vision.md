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

**E3-T2 (wire machinery) — landed, real provider call RUN on 2026-11-04.** `attrs_adapters/base.py`
holds the provider-independent half: `extra="forbid"` schema enforcement, exactly one repair
retry, evidence gating for `brand`/`material`, confidence defaults of 0.0 (never a guess),
and `needs_review` for sub-threshold fields. Failure after retry raises
`AttributeExtractionFailed(reason_code="low_confidence")`, never `internal` (CONTRACTS §4).
The real acceptance path produced a `measured` record against `deepseek-flash` (golden-web
`w472.jpg`): category=top/subcategory=t-shirt/colour=white/pattern=solid, `material` dropped
(no evidence), `brand`/`era`/`size_label`/`measurements` nulled into `needs_review`, round-trip
**1.67s**, 1025 prompt + 192 completion tokens. See the measured section below.

**E1–E2 pipeline** from the previous batch still runs end-to-end. Re-verified after the CLI
change on all 93 web-set images: `OK=93 FAIL=0`, mean 1.54 s/image (1.31–2.27 s), u2net on
CPU. `attributes.json` is emitted as the UNMEASURED marker in every one of those runs.

## What is broken / UNMEASURED — read this before claiming E3 is done

**The provider decision IS made: DeepSeek V4.1 Flash, Bert's call 2026-11-04.** See
`docs/ADR-006-attributes-vlm-provider.md` (official rate card, terms, sources), the hosted-service
row in `docs/DEPS.md`, the updated `docs/RUNBOOK.md §6`, and `.env.example` for the exact config.
API model id is **`deepseek-flash`** (not `deepseek-v4.1-flash`), base URL
`https://api.deepseek.com` with no `/v1`.

**A `VLM_API_KEY` is now present (Bert pasted it 2026-11-04) and the real provider call HAS run.**
There is no local model (ollama installed, zero pulled). BRIEF.md forbids mocked model calls in
the acceptance path, and none were used — this is a real round-trip.

Consequently:

* `attributes.json` from the CLI is now a `{"status": "measured", "provider": "http"}` record
  (see the measured run below). It IS a real result.
* E3-T2's real acceptance command **has** produced a measured attribute set, and E3-T3
  accuracy **HAS now been measured on this machine** (2026-11-08). `evals/reports/vision.md`
  (a gitignored local report) records: `attr category accuracy` **0.9250** (≥ 0.95, **NOT met**),
  `attr colour accuracy` **0.6500** (≥ 0.90, **NOT met**), `attr pattern accuracy` **0.9750**
  (≥ 0.85, met), `attr material accuracy` **0.2750** (≥ 0.80, **NOT met**), and
  `dedupe false-merge rate` **0.0000** (≤ 0.05, met).
  **Three of the four attribute targets are NOT met.** `material` is `[]` for most catalogue
  photos under the current evidence-only
  policy, so ≥ 0.80 is unreachable without a product decision; the policy was **not** relaxed
  to chase the number. These are LOCAL, UNCOMMITTED numbers from the local golden set — on a
  clean clone they fall back to `UNMEASURED`. Still `UNMEASURED`: rectify SSIM, the prettify
  hallucinated-text rubric, and `cost per item`.
* **SUPERSEDED 2026-11-12 — see "E1-T4" at the end of this file.** This bullet used to say the
  20 masks were machine-made (18 `algorithmic:matting` + 2 `ai:google-ai-mode-nano-banana`) and
  that E1-T4's hand-mask DoD was therefore not met. All 20 now carry
  `ai-assisted:birefnet+point-reviewed`, the point-review register is 20/20 with the
  edit-distance threshold SET (PRs #15/#16/#17), and E1-T4's report is generated:
  **mean IoU 0.9357 over n=20 against a ≥ 0.92 threshold**. Half of those photos are synthetic
  (Bert ruling 3) — the number describes synthetic inputs as well as real ones.

**ADR-010 status note — SUPERSEDED 2026-11-12 (kept for provenance).** As written on
2026-11-08 the vocabulary was classified by `evals/mask_provenance.py` into hand-drawn,
`ai-assisted:<model>+point-reviewed` and machine-made, and all 20 masks were machine-made, so
the register stood at `0/20` reviewed with the threshold `UNSET` and **U1 UNMET**. That is no
longer the state: the register is now **20/20 reviewed** with `min_changed_pixels=0`,
`max_changed_pixels=75000`, `max_changed_ratio=0.10` (PR #17), so **U1 is MET** and the
hand-mask gate is closed in the relaxed-DoD sense. What has NOT changed: `validate_point_reviews`
exit 0 still means only "register structurally valid", and ADR-010 still does not pre-adjudicate
the IoU semantics — that remains a follow-up decision.
* `cost_usd` stays `None` in the live output by design; the rate of truth is ADR-006, not code.
  Real `prompt_tokens`/`completion_tokens` (1025/192 on the measured call) are surfaced in
  `attributes.json`; at ADR-006's off-peak rate that is ≈ $0.00027/item (~110× under the
  BRIEF ≤ $0.03/item ceiling), peak ≈ $0.00054/item.
* **OPEN legal items before seller photos are sent** (ADR-006, owner: legal): DeepSeek's privacy
  policy says personal data is processed/stored in the PRC and the Open Platform Terms are
  PRC-governed; a third-party tracker reports no DPA. `prompts/08-legal.md` §7 requires a
  subprocessor table with region, §5 a data map. These are [COUNSEL] items, not conclusions.
* **Rate-limit note (production batching):** an earlier attempt in this session hit a DeepSeek
  HTTP 429 ("frequency limit exceeded"); the retry succeeded within seconds. The adapter maps a
  429 to `low_confidence` (no automatic backoff). A production batch of 93+ images will likely
  trip the limit — add retry-with-backoff (honouring `Retry-After`) before bulk runs.

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

The provider decision is done (ADR-006) and a real `VLM_API_KEY` is now present, so E3-T2's
acceptance has produced a measured record. Current status and what remains, as of 2026-11-08:

1. **Legal sign-off on the OPEN items in ADR-006** before any seller photo is sent. The privacy
   policy places personal-data processing/storage in the PRC and the terms are PRC-governed;
   `prompts/08-legal.md` §7 needs a subprocessor table with region. This is the one place where
   "cheapest model" is not the only axis — do not treat it as settled.
2. **E3-T3 accuracy report** HAS been measured on this machine (2026-11-08) — it is no longer
   UNMEASURED here. `evals/reports/vision.md` records category 0.9250 (≥ 0.95, NOT met), colour
   0.6500 (≥ 0.90, NOT met), pattern 0.9750 (≥ 0.85, met), material 0.2750 (≥ 0.80, NOT met)
   and dedupe false-merge 0.0000 (≤ 0.05, met).
   **Three of the four attribute targets are NOT met.** `material` is unreachable under the
   current evidence-only policy, which was NOT relaxed to chase the number. These are
   LOCAL, UNCOMMITTED numbers from the local golden set; on a clean clone they fall back to `UNMEASURED`.
3. **The 20 reference masks are no longer missing** (this item read "0/20; 18
   `algorithmic:matting` + 2 `ai:google-ai-mode-nano-banana`" until 2026-11-12). They are
   `ai-assisted:birefnet+point-reviewed`, 20/20 in the register, with two documented quality
   limits (`hard-05` partial, `hard-01` low-confidence silhouette). E1-T4's report now exists
   (`evals/reports/seglab.md`, gitignored, regenerated by the command in the E1-T4 section).
4. Still `UNMEASURED`: rectify SSIM, the prettify hallucinated-text rubric, and cost per item.

## Exact next commands

**Dated record (2026-11-04): the key-setup instructions below describe the state before the
provider key was filled in. A non-empty `VLM_API_KEY` is already configured in the local `.env`
as of 2026-11-08, so these are not current setup steps. They are kept verbatim for provenance.**

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

Measured run (real `VLM_API_KEY`, golden-web `w472.jpg`, 2026-11-04):

```
$ python -m packages.vision.cli --in ~/.cache/snaplist/golden-web/photos/w472.jpg --out /tmp/sn3 --category top
attrs provider round-trip: 1.670s (attempts=1)
OK backend=u2net device=cpu category=top mask_coverage=0.4368 prettify=pass_through total=1.18s
  cutout     -> /tmp/sn3/cutout.png
  prettified -> /tmp/sn3/prettified.png
  attributes -> /tmp/sn3/attributes.json (measured)

$ python -m json.tool /tmp/sn3/attributes.json
{ "status": "measured", "provider": "http",
  "attributes": { "category":"top","subcategory":"t-shirt","colors":["white"],"pattern":"solid",
                  "material":[],"fit":"regular","condition":"good",
                  "brand":null,"era":null,"size_label":null,"measurements":null,
                  "keywords":["t-shirt","short sleeve","crew neck","plain","basic"],
                  "confidence": {"category":0.95,"subcategory":0.9,"colors":0.95,"pattern":0.9,
                                 "fit":0.6,"keywords":0.8, <rest 0.0>},
                  "needs_review":["brand","condition","defects","era","fit","material",
                                  "measurements","size_label"],
                  "model_version":"http:deepseek-flash" },
  "round_trip_seconds":1.67, "cost_usd":null,
  "prompt_tokens":1025, "completion_tokens":192 }
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

---

# Round 2 — E3-T4, `find_duplicate()` + the 20-pair false-merge fixture (2026-11-04)

Ticket: `docs/PLAN.md` E3-T4 (vision, P1). Acceptance: false-merge rate on the 20-pair fixture
≤ 0.05, threshold documented in the module docstring with the reason.

**Two corrections happened in this round, both on review.** The first cut was single-layer — a
perceptual score with a threshold. That was wrong: `apps/api/requests.py::CreateItemRequest.source_sha256`
had declared the dedupe key before this stage existed, so dedupe is **two layers over one key**,
and building a second key would have been a contract fork. The second correction was ordering:
the digest comparison has to run **before any decoding**, because "same bytes" is a fact about
bytes, not pixels, and because a flat, textureless upload has no structural direction and would
otherwise never be de-duplicated at all. Both are in what shipped below. The single-layer
numbers are still valid **for the perceptual layer** — it was the design that was wrong, not the
arithmetic.

If you are reviewing a copy of `dedupe.py` where `grep -nE "sha256"` hits only a comment, that
copy is stale: the shipped file is `md5 aeea6d16350dd5d6dfb2382dc7c4adea` and it imports
`hashlib` and calls `hashlib.sha256` in `_digest()`.

## Files changed (and nothing else)

| file | what |
|---|---|
| `packages/vision/dedupe.py` | **rewritten** — stub gone, two-layer `find_duplicate()` + `python -m packages.vision.dedupe` |
| `packages/vision/tests/test_dedupe.py` | **new** — 32 tests |
| `evals/fixtures/dedupe/**` | **new** — `manifest.json`, `README.md`, `CREDITS.md`, `build_fixtures.py`, `base/` (12), `exact/` (3), `variant/` (7); 0.96 MB |
| `docs/HANDOFF-vision.md` | this section (appended; Round 1 untouched) |
| `packages/vision/tests/test_imports.py` | **scope exception, 7 lines — see below** |

### The one file I touched outside E3-T4's file list

`packages/vision/tests/test_imports.py::test_stubs_raise_stage_not_implemented` asserted
`find_duplicate("user", b"")` raises `StageNotImplemented`. It now raises `ValueError`, so the
suite went red. That file already carries the precedent one line up:

> `# segment left this list when E1-T2 landed: an empty-bytes call is now a ValueError (input validation)`

So I moved the assertion to `vton.get_adapter` (still a stub) and added the matching comment.
Two lines of code plus a comment. Revert it if you prefer — but `make test` cannot then be green
while `find_duplicate` validates its input, and validating is right
(`test_dedupe.py::test_empty_query_bytes_raise`).

## Two layers, one key

`source_sha256` (64 lowercase hex, nullable, client-supplied). No second key was invented.

1. **Exact layer** — `sha256(image)` equals the candidate's digest → `similarity = 1.0`, no
   threshold consulted, no false-merge risk. **It is step 0: it runs before anything is decoded.**
   That ordering is a requirement, not an optimisation — an upload Pillow cannot read is still the
   same upload if the bytes match, so a decode failure must not be able to destroy a byte-level
   duplicate; and a plain re-upload then never touches Pillow at all, so it is also the cheap path.
   `find_duplicate` does two passes over `candidates`: pass 0 hashes, pass 1 decodes and scores.
   Where candidate digests come from: **the candidate file's own bytes** (`candidates` are paths,
   so the digest is `sha256` of the file on disk).
   That equals the client's `source_sha256` for that item **iff ingest stores the uploaded bytes
   verbatim** — true of today's path in `apps/api/requests.py`, and the seam to re-check when
   E5-T4 direct upload lands. If ingest ever re-encodes before storing, this layer silently
   degrades into the perceptual one **with no error**; that sentence is in the module docstring
   because it is the kind of failure nobody notices. Storing `source_sha256` on the item row and
   hashing the upload once would be better, but it needs a column and a signature this ticket
   may not add — `find_duplicate`'s parameters are frozen by `test_public_signatures_are_stable`.
2. **Perceptual layer** — same photograph re-encoded / rescaled / re-framed / re-exposed. This is
   the layer the thresholds and the false-merge rate describe, because this is the layer that can
   be wrong. Two gates, both required: a 16×16×3 mean-centred structure cosine maximised over a
   symmetric 5×5 zoom pyramid, and a soft-binned hue×saturation histogram intersection.

Precedence: **exact beats perceptual** (identical bytes are stronger evidence than a score), and
the loop returns on the first digest hit even if an earlier candidate already passed the gates.
Within a layer the earliest candidate wins, so nothing depends on dict order.

**`similarity == 1.0` is ambiguous and `DedupeResult` cannot disambiguate it.** A 1.0 means
*either* "same bytes" (digest layer, certain) *or* "the structural score topped out" (perceptual
layer, thresholded) — and the second is reachable: under mutation M7 below, the three byte-identical
pairs still come back at exactly 1.0 with the digest layer disabled, which is the proof. The struct
keeps its two fields (adding `kind` would change a shape this ticket may not change), so a caller
that needs to know **must compare `sha256` itself**. The docstring carries that snippet; the CLI
prints a `layer` column for humans.

No model, no new dependency (`hashlib` is stdlib, `docs/DEPS.md` untouched), no network — an
autouse socket trap proves that in every test. **CLIP was rejected on four grounds**: it answers
"same kind of garment", which is the wrong question (two different tan breeches *are* the same
kind); it needs a ~350 MB checkpoint on first call (a network call inside a zero-network stage)
or a dependency this round may not add; it is not deterministic across BLAS builds and this
number sits in a gate; and there is no torch on this machine.

The honest framing: **v1 catches the same photograph re-uploaded** — camera-roll re-send,
screenshot, re-export — not "a second photo of the same shirt". Stated in the docstring, the
fixture README and here.

The exact layer is not just plumbing: a **flat, textureless frame** (care-label close-up, plain
backdrop, grey flat-lay) has no structural direction, so the perceptual layer answers "no match"
for two frames that look identical. Without the digest layer those uploads would never be
de-duplicated at all — that is the real hole it closes, and
`test_flat_frame_with_one_small_mark_is_not_a_match` keeps it from becoming a similarity
heuristic wearing a hash.

### Thresholds, with the measurements (perceptual layer only)

`STRUCTURE_MIN = 0.90`, `COLOUR_MIN = 0.55`. Exact pairs are excluded by construction — they
short-circuit at the digest.

| | structure | colour |
|---|---|---|
| weakest near duplicate (`near-05`: 90 % crop, 3 % shift, +6 % brightness) | 0.915 | 0.965 |
| weakest near duplicate on colour (`near-04`: −12 % brightness, JPEG q55) | 0.9999 | 0.946 |
| strongest distinct pair (`top-01` jersey vs `top-02` tee) | 0.602 | 0.109 |

Structure 0.90 is 0.30 above the strongest false pair and only 0.015 above the weakest duplicate.
Deliberate asymmetry: missing a duplicate costs one row the seller deletes; a false merge deletes
a listing. Colour 0.55 is 0.40 below the weakest duplicate and below **five of the ten** distinct
pairs (0.11–0.49); the other five are colour-similar (0.72–0.85) and are stopped by structure —
the two gates catch different failures.

The 0.55 came from a hue-rotation ladder on `base/bottom-01.jpg` scored through the module:
5° → 0.889, 15° → 0.630, 20° → 0.503, 25° → 0.315, 60° → 0.006. The gate tolerates ~17° of
colour cast and rejects a different colourway. **That ladder is why `_colour_histogram` uses soft
bins**: with hard bins the same 5° shift scored **0.347**, i.e. the gate would have vetoed an
honest re-upload from a phone with a different white balance. Found by measuring, not by reading
the code; `test_small_colour_cast_does_not_block_a_real_duplicate` now pins it.

## Deliverable B — the fixture, honestly

20 pairs = **3 `kind: exact` / 7 `kind: near` / 10 `distinct`**. 12 base + 3 exact + 7 variant
images, **0.96 MB**, long edge ≤ 800 px, licences chained through
`evals/fixtures/dedupe/CREDITS.md` → `evals/golden-provisional/CREDITS.md`.

* **`exact` pairs are byte-identical copies** (two filenames, one payload — what a re-upload looks
  like to the blob store), so the digest layer is exercised and the test suite asserts
  `sha256(a) == sha256(b)`. Three, not seventeen: an exact pair is a digest equality, so the
  fourth proves nothing the first did not.
* **`near` pairs are same-source transforms** (scale + re-encode ×2, white pad, crop + pad,
  brightness + re-encode, crop + brightness, pad + re-encode). Byte-different, asserted by
  `test_near_pairs_are_not_byte_identical` — a `near` pair that shipped identical bytes would
  leave the threshold untested.
* **`distinct` pairs are ten different physical items** (distinct accession numbers), chosen hard:
  six tan breeches from one museum series on one white sweep, plus dark-footwear-same-layout,
  two-pairs-of-shoes-same-angle, white-top-vs-white-top, tan-jacket-vs-cream-breeches. Composition
  bias stated: 6 of 10 negatives are breeches because 6 of the 17 sources are.
* **Separate caveats, as they should be.** Exact: 1.0 is the layer's definition, not a measured
  capability — no caveat needed and none given. Near: recall is an upper bound (same-source
  transforms) and the threshold is in-sample. Both sentences are in the README and the docstring.
* **The ten negatives are not ten independent trials, and quoting `0/10` as if they were would be
  wrong** (QA's point). The whole set is built from 12 distinct source photos, and six of the ten
  negatives reuse `bottom-01..06` — one garment series shot in one session. So the effective sample
  size for "a false merge does not happen" is closer to **a dozen images than ten draws**, and the
  confidence interval around `0/10` is wider than the fraction suggests. Do not cite it as
  "0 false merges in 10 independent trials"; cite it as "0 false merges over 10 pairs drawn from
  12 photos, one of which contributes six".
* With a denominator of 10 the `≤ 0.05` gate is effectively **zero-tolerance**: one false merge
  reads 0.10.
* Regeneration is deterministic and **prunes stale images** — a `.jpg` no recipe claims is
  deleted, because stale debris still counts against the size budget and still looks like part of
  the set. Re-running left `manifest.json` byte-identical.

## Deliverable C — acceptance command, real output

```console
$ python -m packages.vision.dedupe --fixture evals/fixtures/dedupe
pair       label      kind   verdict    layer  structure  colour  note
exact-01   duplicate  exact  duplicate  exact      exact       -  ok
exact-02   duplicate  exact  duplicate  exact      exact       -  ok
exact-03   duplicate  exact  duplicate  exact      exact       -  ok
near-01    duplicate  near   duplicate  near      1.0000  0.9770  ok
near-02    duplicate  near   duplicate  near      0.9856  0.9892  ok
near-03    duplicate  near   duplicate  near      0.9714  0.9530  ok
near-04    duplicate  near   duplicate  near      0.9999  0.9463  ok
near-05    duplicate  near   duplicate  near      0.9150  0.9650  ok
near-06    duplicate  near   duplicate  near      0.9712  0.9793  ok
near-07    duplicate  near   duplicate  near      0.9999  0.9844  ok
dist-01    distinct   None   distinct   -         0.4672  0.8398  ok (distinct)
dist-02    distinct   None   distinct   -         0.1645  0.8475  ok (distinct)
dist-03    distinct   None   distinct   -         0.5920  0.4940  ok (distinct)
dist-04    distinct   None   distinct   -         0.1064  0.8026  ok (distinct)
dist-05    distinct   None   distinct   -         0.5131  0.8205  ok (distinct)
dist-06    distinct   None   distinct   -         0.3918  0.4574  ok (distinct)
dist-07    distinct   None   distinct   -         0.5095  0.4248  ok (distinct)
dist-08    distinct   None   distinct   -         0.5724  0.4801  ok (distinct)
dist-09    distinct   None   distinct   -         0.6023  0.1088  ok (distinct)
dist-10    distinct   None   distinct   -         0.4127  0.7160  ok (distinct)

exact: n=3 detected=3 recall=1.00 (a 1.0 here is the layer's definition, not a measured capability)
near : n=7 detected=7 recall=1.00 (upper bound: positives are same-source transforms)
digest_decided=3 thresholds: structure>=0.9 colour>=0.55 gate: false_merge_rate<=0.05
n_pairs=20 n_distinct=10 false_merge_rate=0.000
```

**false_merge_rate = 0.000 over 10 distinct pairs** (0 false merges). Near-recall 1.00 with the
weakest positive at 0.915 — see the asymmetry note.

**Cost: tens of milliseconds per call, CPU, and the earlier "13 ms per candidate" figure was wrong.**
That number came from a mean-of-20 divided by the candidate count, which amortised the query
fingerprint into a per-candidate figure — a methodological error, not machine noise. Two honest
measurements of the same warm loop disagree by ~2×, so only the order of magnitude is defensible:

| measurement | 1 candidate | 3 candidates |
|---|---|---|
| this repo, `perf_counter`, n=50 warm, medians | 19.0 ms (p10 18.7 / p90 19.5) | 36.6 ms (p10 35.9 / p90 37.7) |
| QA's independent run, same machine + venv, medians | 30.7 ms (p10 27.5 / p90 47.5) | 74.6 ms (p10 65.4 / p90 82.0) |

Marginal cost is roughly **9 ms per extra candidate** on my run; the rest is the query fingerprint,
paid once. Do not quote a point estimate from either row — a caller-side budget should assume
`O(tens of ms per call, growing linearly in candidate count)` and measure on its own hardware. The
digest layer adds one `sha256` per candidate, which is negligible beside the decode.

The `layer` column is there so an operator can tell a free, certain digest hit from a thresholded
one. `digest_decided=3` is printed instead of "unmeasured", because those three pairs *were* decided
— reporting them as unmeasured would be the opposite lie. `exact_recall` and the `layer` column
are **reporting**: they say which mechanism answered, and the gate does not read them (see the M7
entry below, which is the reason).

## Deliverable D — tests

32 tests in `packages/vision/tests/test_dedupe.py`. Beyond the fixture gate (which prints
`n_pairs`/`n_distinct`/`n_exact`/`n_near`), the layer split is pinned in both directions:

* `test_exact_pairs_are_byte_identical_and_matched_by_digest` — same sha, `similarity` is
  `EXACT_SIMILARITY`;
* `test_near_pairs_are_not_byte_identical` — no `near` pair may be a byte copy;
* **`test_exact_layer_does_not_depend_on_the_perceptual_thresholds`** — monkeypatches
  `STRUCTURE_MIN`/`COLOUR_MIN` to 1.01 and asserts exact pairs still match **and** a near pair now
  misses (so the patch is provably live);
* `test_exact_match_wins_over_a_perceptual_match` — an identical candidate later in the list beats
  a passing-scored earlier one;
* `test_undecodable_query_is_still_exact_matched_by_its_digest` — an upload Pillow cannot read is
  matched by its bytes, proving the digest pass really is ahead of the decode (the sibling test
  `test_undecodable_query_raises_when_no_candidate_matches` keeps the `ValueError` for the case
  where there is no digest hit to hide behind);
* `test_identical_flat_frame_is_an_exact_match` / **`test_flat_frame_with_one_small_mark_is_not_a_match`**
  — a pure-colour PNG matches itself byte for byte, and the same frame with one small dark mark
  added does **not** match. That pair of tests is what says this layer is a digest and not a
  similarity heuristic in disguise;
* `test_report_verdicts_agree_with_find_duplicate` — the printed table cannot drift from the API;
  both call one `_score_candidate()`;
* plus: manifest consistency (labels, `kind`, files exist, a real `why`, licence, no transform on a
  distinct pair); size budget ≤ 6 MB, ≤ 800 px **and no unclaimed image on disk**; padded duplicate
  detected; hard-negative breeches not merged; colour veto on a hue-shifted same-frame copy; 5°
  colour cast still matches; missing candidate skipped; corrupt candidate skipped while a good
  neighbour still matches; all-corrupt → `similarity is None`; no candidates → `None`; empty query
  → `ValueError`; undecodable query → `ValueError` even when a candidate is byte-identical;
  determinism; symmetry; best candidate wins (`test_best_candidate_wins`) and a real tie keeps
  the earliest (`test_ties_keep_the_earliest_candidate`, two files, identical pixels, different
  bytes — see the audit note below); greyscale copy still matches
  (gate abstains). The old `test_uniform_frame_matches_nothing` is gone on purpose: "a uniform frame
  matches nothing" is only true of the *perceptual* layer, and the exact layer's whole point is that
  a flat upload re-sent byte-identically **does** match.

## Deliverable E — mutation testing (seven mutations, seven kills)

Run on the shipped `dedupe.py`, then restored; `md5` before = after =
`aeea6d16350dd5d6dfb2382dc7c4adea`. (The `553d8999…` quoted in review was the **pre-redesign**
single-layer file; this one has `hashlib` imported and `_digest()` called from `find_duplicate`.
`md5 -q packages/vision/dedupe.py` reproduces it.)

| # | mutation | killed by |
|---|---|---|
| M1 | always merge (`STRUCTURE_MIN` −1.0, `COLOUR_MIN` 0.0) | `test_fixture_false_merge_rate_is_under_gate`, `test_hard_negative_breeches_are_not_merged`, `test_colour_veto_…`, `test_flat_frame_with_one_small_mark_is_not_a_match` |
| M2 | never merge (`STRUCTURE_MIN` 1.01) | `test_same_source_transform_is_a_duplicate` + 6 more |
| M3 | drop the colour constraint (`COLOUR_MIN` 0.0) | `test_colour_veto_blocks_a_same_frame_different_colourway` |
| M4 | drop the zoom pyramid (`ZOOM_LEVELS` `(1.0,)`) | `test_same_source_transform_is_a_duplicate` (padded pair → 0.814) |
| M5 | hard histogram bins instead of interpolation | `test_small_colour_cast_does_not_block_a_real_duplicate` |
| M6 | achromatic frame rejects instead of abstaining | `test_achromatic_frames_are_decided_on_structure_alone` |
| M7 | **short-circuit the sha layer** (`if candidate_digest == query_digest` → `if False`, both call sites) | `test_exact_layer_does_not_depend_on_the_perceptual_thresholds`, `test_identical_flat_frame_is_an_exact_match`, `test_undecodable_query_is_still_exact_matched_by_its_digest`, `test_report_attributes_exact_pairs_to_the_digest_layer`. **Not** the fixture gate — see above |
| M8 | **ties resolve to the last candidate** (`>` → `>=` in the match selection) | `test_ties_keep_the_earliest_candidate` — and only that one |
| M9 | **`similarity` reverts to "best score seen"** (the pre-audit behaviour) | `test_similarity_always_describes_the_matched_candidate` |
| M10 | **candidate types go back to `isinstance(x, str)`** (so a `Path` is skipped, not used) | `test_pathlike_candidates_are_accepted`, `test_non_path_candidate_raises_type_error` |

### What QA's audit pulled, all fixed in the same tree

Every item below was found by **QA (严过关)** in audit, not by me, and the two behavioural ones
(`similarity` describing a different pair, and `Path` candidates failing open) were real defects in
what I handed over — one of them silently disabled dedupe.

1. **The tie test was a tautology.** `test_best_candidate_wins_and_ties_keep_the_first` passed the
   same path twice, so "ties keep the earliest" was true for *any* implementation. Replaced by
   `test_ties_keep_the_earliest_candidate`: two different files, identical pixels, **different
   bytes** (PNG `compress_level` 1 vs 9), so the digest layer cannot short-circuit and both
   candidates score exactly the same. It asserts the earliest wins, that reversing the input
   reverses the answer, and that each candidate matches on its own (so it is a tie, not two
   rejections). **M8 turns exactly this test red and nothing else** — which is the proof it now has
   teeth. The non-tie half was split out into `test_best_candidate_wins`.
2. **A decorative assertion** at the end of `test_only_corrupt_candidates_are_unmeasured_not_zero`
   (`assert result.similarity is not 0.0`, already implied by the line above) is gone; the real
   assertion is `is None`, with a comment on why UNMEASURED must be distinguishable from a measured
   zero.
3. **`similarity` could describe a different pair from `match_item_id`.** `best_similarity` was the
   max over *all* measurable candidates while `match_item_id` was the max over *matching* ones, so a
   caller logging both could get one candidate's id next to another candidate's score. QA reproduced
   it (a hue-rotated copy at 0.9869 rejected, a padded copy at 0.9850 accepted, and the pair came
   back as `padded12.jpg` + `0.9869`). Fixed: with a match, `similarity` is **that candidate's** own
   score; without one, it stays the closest miss, which is the only useful thing left to report.
   Pinned by `test_similarity_always_describes_the_matched_candidate`, whose premise asserts the
   rejected candidate really does score higher (otherwise the test would prove nothing), and killed
   by M9.
4. **A `pathlib.Path` candidate silently disabled dedupe.** `isinstance(candidate, str)` skipped it
   with a warning and returned `(None, None)` — a real duplicate reported as a new item, and the one
   failure mode this stage exists to prevent. Fixed: candidates are normalised through `os.fspath`,
   so `Path` works and either comes back as its string; anything that is not a path at all raises
   `TypeError` **before** any candidate is examined. That is the same standard the query bytes get
   (`ValueError`), and it removes the asymmetry QA named: a broken caller must be loud, a missing
   file may be quiet. Pinned by `test_pathlike_candidates_are_accepted` and
   `test_non_path_candidate_raises_type_error`, killed by M10.
5. **The cost figure was wrong** — see the corrected table in Deliverable C. QA could not reproduce
   13 ms/candidate; the number had amortised a fixed cost into a per-candidate figure. My own
   re-measurement disagrees with QA's by ~2×, so the handoff now publishes both and claims only the
   order of magnitude.
6. **`COLOUR_MIN` has no teeth on the fixture — the docstring no longer claims otherwise.** See the
   "not observable" paragraph in the module docstring: with `COLOUR_MIN = 0.0` the false-merge rate
   stays 0.000, because the strongest distinct *structure* score is 0.6023 against a 0.90 gate. M3
   below turns exactly **one** test red and it is the synthetic one. The colour gate is real and it
   is load-bearing against a recoloured upload; it is simply not what the twenty-pair numbers
   measure. I chose to correct the claim rather than add a synthetic 21st fixture pair, because
   changing the fixture would have invalidated the QA snapshot mid-audit (and the same evidence
   already exists as a unit test).
7. **Counting drift between the fixture and its four documents** (`4 exact / 6 near` in the module
   docstring, the README and the test docstring after the fixture became `3 / 7`), plus the
   files-changed table still saying `exact/` (4), `variant/` (6), 1.00 MB and 25 tests. All
   corrected to `3 / 7 / 10`, 0.96 MB, 32 tests; `grep -nE "4 exact|6 near"` is now clean across
   `dedupe.py`, `README.md` and `test_dedupe.py`. This is the failure mode the freeze exists to
   prevent: a number that is right in one place and stale in another is not a number.

### M7 needed the gate assertion corrected — the fixture cannot see the digest layer

The lead caught a genuine contradiction in the previous draft of this section, and it was not a
wording slip. Two statements were both there: "M7 reddens the fixture gate" and "with the digest
layer disabled the three byte-identical pairs still return 1.0". If detection is unchanged, the
gate can only be red because a **label** moved — i.e. the exact pairs were still matched, just
attributed to the perceptual layer.

Measured, and now pinned:

* `false_merge_rate` and every match/no-match verdict are **identical with and without the digest
  layer** on this fixture. The perceptual layer matches byte-identical files at 1.0 on its own.
* So `test_fixture_false_merge_rate_is_under_gate` now asserts **detection only** — the rate, the
  counts, the printed summary line. `exact_recall` and the `layer` column were removed from it and
  moved to `test_report_attributes_exact_pairs_to_the_digest_layer`, which is a reporting test and
  says so in its docstring.
* Consequence, stated plainly: **the fixture cannot detect the digest layer being removed. Only the
  unit tests can** (`test_exact_layer_does_not_depend_on_the_perceptual_thresholds`,
  `test_undecodable_query_is_still_exact_matched_by_its_digest`,
  `test_identical_flat_frame_is_an_exact_match`). M7 reddens those three **plus** the reporting
  test — not the gate. A gate that goes red only when a label changes is a gate that will stay green
  through a real regression.

This is the same ruler as the colour gate (item 3 of the audit notes below): **the fixture measures
one mechanism, the structure threshold, and unit tests measure the rest.** The handoff says so
instead of letting the table imply otherwise.

### M3's kill was partly self-referential — fixed

QA caught the same failure in miniature: `test_colour_veto_…` used to end with
`assert COLOUR_MIN > 0.0`, so the mutation "set `COLOUR_MIN = 0.0`" made that test fail **by
definition**, without the module ever merging two colourways. A killer that reads the constant it
is supposed to defend is not evidence about behaviour.

Fixed by splitting the two claims:

* `test_colour_veto_blocks_a_same_frame_different_colourway` is now **behaviour only** — it asserts
  the premise (structure ≥ `STRUCTURE_MIN`, i.e. structure alone would merge) and the outcome
  (`match_item_id is None`). Under M3 it fails because the module really does return a match.
* `test_the_gates_are_not_disabled` holds the constants in `(0, 1]` and says in its docstring that
  it must not be counted as the reason a mutation was caught.

So M3 (`COLOUR_MIN = 0.0`) now reddens **three** tests, and it is worth naming all three because
only the first two are about the colour gate:

1. `test_colour_veto_blocks_a_same_frame_different_colourway` — **behaviour**: the module returns a
   match for two colourways. This is the kill that means something.
2. `test_the_gates_are_not_disabled` — a constant self-check, kept separate on purpose and labelled
   as such; it would fire for any disabling mutation.
3. `test_similarity_always_describes_the_matched_candidate` — **collateral**: its *premise* is that
   the hue-rotated copy is rejected, and with the gate off that premise stops holding. A premise
   failing is the test behaving correctly, but it is not extra evidence about the colour gate, so it
   must not be counted as such.

Counting all three as "the colour gate is well tested" would be the same error as counting only the
self-check; the handoff names them instead of leaning on the number.

**M2 and M7 are the two halves of the same claim, and they need each other.**

* Under **M2** ("never merge") the exact-layer tests stay **green** — a digest hit does not care
  what the thresholds say. (`test_exact_match_wins_over_a_perceptual_match` does fail under M2, but
  because its *premise* — that the earlier candidate passes the gates — stops holding, which is the
  test behaving correctly.)
* Under **M7** ("no sha layer") the perceptual layer happens to catch the three byte-identical
  fixture pairs anyway, at exactly `similarity == 1.0` — which is (a) why
  `test_exact_pairs_are_byte_identical_and_matched_by_digest` alone would have been a weak test and
  (b) the empirical proof that a 1.0 can come from either layer, i.e. the ambiguity the docstring
  warns about. What goes red instead is what only the digest layer can do: the thresholds-1.01
  test, the undecodable-upload test, the flat-frame test, and the reporting test (the label column).
  The fixture gate stays **green**, and that is the honest result — see the M7 section above.

Together they show the layer is both independent of the thresholds **and** doing work the
perceptual layer cannot do. Neither mutation alone would have shown that.

M5 survived the first pass in the single-layer round: the fixture cannot see it, because every
duplicate there scores ≥ 0.94 colour. It was killed only after adding the 5°-cast test. Same lesson
as Round 1 — a green suite is not coverage.

**M3 is the same story in the other direction, and it was QA who found it.** Deleting the colour
gate entirely leaves `false_merge_rate` at 0.000 and the fixture gate green; the only red that is
about the gate's *behaviour* is the synthetic `test_colour_veto_…` (the other two reds under M3 are
a second behavioural assertion and a constant self-check — see the M3 list above). So the
twenty-pair fixture measures the structure gate and *only* the structure gate. That is now stated
in the module docstring instead of being implied away by "the two gates catch different failures".

**A gate has to be read in both directions, and both are now on record.** M7 (digest layer removed,
detection unchanged) must leave the fixture gate **green** — if it went red, the gate would be
reacting to a report label rather than to a merge. QA's Q8 (both thresholds to 0.0, so everything
merges) must make it **red** — four tests, including the fixture gate. A gate that only reacted to
labels proves nothing; one that only reacted to degradation would let the report drift away from
the behaviour. Checking one direction and not the other is how a gate becomes decorative.

## `make test` / `make lint`, real tails

```console
$ make test
# pass 72   (node)
333 passed, 1 warning in 55.94s          (pytest; baseline 301, +32 here)

$ make lint
All checks passed!                       (ruff)
packages/shared lint: Done
apps/mobile lint: Done
```

## What I still do not believe / what is not proven

1. **The exact layer is only as good as the storage assumption.** It compares `sha256(file on
   disk)` with `sha256(upload)`. If ingest re-encodes, normalises EXIF, or transcodes, this layer
   silently becomes a second copy of the perceptual layer and the 1.0 it reports is fiction. Not
   observable from here: the upload path (E5-T4) does not exist yet. **This is the first thing to
   check when it lands.**
2. **Near-recall 1.00 is not a product claim.** Every positive is a same-source transform, so it
   says "the arithmetic survives re-encoding and re-framing", nothing more. No WhatsApp-shotted
   pair exists in the fixture; that a real one would be caught is an inference.
3. **The perceptual threshold is in-sample, no held-out set.** 0.90 was chosen while looking at
   these 20 pairs, and ten negatives are a sample max: the population's strongest false pair could
   be above 0.602. 0.30 of headroom makes that unlikely, not impossible.
4. **Everything is measured against museum-catalogue photos** — white sweeps, one garment,
   centred, evenly lit. A phone photo on a bed has no such background, the background stops helping,
   and the structural score becomes noisier. Re-measuring on E0-T1's 40 real photos is the single
   biggest open item and it is **not done**.
5. **Mirrored copies are missed** (0.844 on a flipped `bottom-01`). Deliberate — a mirror is a
   second capture — but a gap if the client ever auto-flips.
6. **Aspect distortion is tolerated, not tested**: a 60 % horizontal squash still scores 1.0000.
   Right for the product; it also means the module cannot distinguish "squashed duplicate" from
   "different photo that happens to align", and nothing in the fixture probes that.
7. **A flat frame that was re-encoded is still missed.** The digest layer fixes the *byte-identical*
   flat upload, which is the common one; the perceptual layer still cannot compare two textureless
   frames (no structural direction), so `care-label.jpg` re-saved at q70 and sent again will not
   match. Closing that needs a texture/edge-energy signal, not a lower threshold — lowering it
   would merge every white-sweep garment in the catalogue.
8. **The three exact pairs only prove the pipe is connected.** The fixture says nothing about *how
   many* real re-uploads are byte-identical, so the dedupe hit rate on real data is unknown.
9. **No `evals/reports/dedupe.md`.** The stub docstring said "reported in evals/reports/", but
   E3-T4's file list does not include `evals/reports/**`, so the number lives in the acceptance
   output and here. Say the word and it is a two-line change.

Nothing was committed (`git status`: `M docs/HANDOFF-vision.md`, `M packages/vision/dedupe.py`,
`M packages/vision/tests/test_imports.py`, `?? evals/fixtures/`, `?? packages/vision/tests/test_dedupe.py`).
No ADR: `DedupeResult`'s fields and the function signature are exactly what the stub declared —
the second layer is internal policy, not a wire shape, and `docs/CONTRACTS.md` is untouched. The
one contract *fact* this round consumed (`source_sha256`) already existed; nothing was added to it.

---

# E2-T2 round 1 — `prettify()` foundation (no model, no download)

Ticket: `docs/PLAN.md` E2-T2, **first of two rounds**. Round 1 ships the whole shape of the
stage executed for real — white balance, wrinkle mask with a coverage cap and a boundary
exclusion, logo/text protection from two sources, and a deterministic non-generative fill.
Round 2 (SDXL inpaint, torch, ~10 GB of weights) is **not** in this build and refuses with the
fix inside the error. Nothing was downloaded, `import torch` still raises
`ModuleNotFoundError`, and no file was committed.

## What changed

| file | why |
|---|---|
| `packages/vision/prettify.py` | the four real steps, `wrinkle_mask_for()` (the audit), `PrettifyBackendUnavailable`, the SDXL refusal, `sdxl_weights_dir()` / `fetch_hint()` |
| `packages/vision/types.py` | **one** new enum value: `PrettifyMode.CLASSICAL` (see "why PASS_THROUGH could not be reused") |
| `packages/vision/tests/test_prettify.py` | new, 28 tests |
| `packages/vision/cli.py` | `--prettify {pass_through,classical,sdxl_inpaint}` (default `classical`), per-stage seconds by backend, alpha resampled onto the flat-lay canvas |
| `evals/metrics/__init__.py`, `evals/metrics/prettify.py` | new: SSIM (numpy, no scikit-image) + the mask audit, `run()` / `summarise()` / `render()` |
| `evals/run.py` | `measurements()` split into `_measure_segmentation()` / `_measure_prettify()`; the prettify row became a real number, the rubric row stays UNMEASURED with its reason |
| `evals/fixtures/prettify/**` | new: 9 cases, `build_fixtures.py`, `README.md`, `CREDITS.md` |
| `evals/golden-provisional/CREDITS.md` | **appended** (attribution chain for the derived fixtures) |
| `packages/vision/tests/test_imports.py` | signature freeze updated: `prettify` gained `protect` — see "out of the declared file list" |
| `evals/tests/test_harness.py` | one assertion — see "out of the declared file list" |

### Why `PrettifyMode.CLASSICAL` had to exist

`PASS_THROUGH` is defined by `test_pass_through_is_byte_identical` as *bytes out == bytes in*.
The non-generative pipeline changes pixels, so reporting it as `pass_through` would put "no
cleanup was applied" in the report about a cleaned image — the exact dishonesty BRIEF.md
forbids, in the one field the cost report reads. `CLASSICAL` is the honest label:
model-free, deterministic. Nothing is reported as `pass_through` unless it truly is.

The other place that treats `PrettifyMode` as a closed set is `cli.py`, which now builds its
`--prettify` choices from the enum (so a future value cannot be silently unreachable), plus
`test_imports.py::test_eval_relevant_types_are_present` which only asserts `PASS_THROUGH in
tuple(PrettifyMode)`. No other code switches exhaustively on it — `segment.py`'s
`[b.value for b in SegmentBackend ...]` is `SegmentBackend`, not this enum.

## `python -m evals.run --suite vision`, real output

```console
| metric | target | measured | epic |
|---|---|---|---|
| segmentation mean IoU | >= 0.92 | 0.9416 (n=17, provisional) | E1 |
| rectify SSIM (inside mask) | >= 0.85 | UNMEASURED | E2 |
| prettify SSIM (inside mask) | >= 0.80 | 0.9927 (n=9, fixtures/prettify) | E2 |
| prettify hallucinated-text rubric | >= 4 / 5 | UNMEASURED | E2 |
...
- **prettify SSIM (inside mask)** — fixtures/prettify, n=9: mode classical · backend
  local_shadow_attenuation_v1 on cpu · wall clock local_shadow_attenuation_v1 1.23s total /
  0.137s mean (n=9); SSIM compares the prettify output against its own input inside the
  garment mask, so 1.0 = nothing changed: this is a destruction budget, not a cleaning score
  (median 0.9938, worst 0.9884, mean |dL| inside the mask 5.311 levels); protect-box overlap
  0 px over 5 logo cases; mask coverage max 0.1252 against a cap of 0.25
- prettify hallucinated-text rubric: UNMEASURED — the manual 1-5 rubric is E2-T3; no human has
  scored anything yet, and a machine cannot stand in for it
```

`prettify_mode` is printed (`mode classical`), and the wall clock is per backend, as E2-T2's
acceptance requires. Rectify's row is still `UNMEASURED`: E2-T1's SSIM is measured by
`prompts/02-vision.md`, and wiring it is not this ticket's file list.

## `python -m evals.metrics.prettify` — per case

```console
| id | kind | SSIM (destruction) | mean |ΔL| in mask | mask cov | cap | protect overlap px | crease hit | changed px | seconds |
| logo-01-tee-chest | logo | 0.9915 | 3.40 | 0.094 | 0.25 | 0 | 0.090 | 29827 | 0.182 |
| logo-02-breeches-waistband | logo | 0.9938 | 4.32 | 0.066 | 0.25 | 0 | 0.053 | 15288 | 0.122 |
| logo-03-sneaker-side | logo | 0.9931 | 5.30 | 0.044 | 0.25 | 0 | 0.036 | 9808 | 0.087 |
| logo-04-belt-stamp | logo | 0.9950 | 7.74 | 0.108 | 0.25 | 0 | 0.092 | 25223 | 0.088 |
| logo-05-jacket-emblem | logo | 0.9884 | 6.55 | 0.073 | 0.25 | 0 | 0.085 | 20071 | 0.151 |
| crease-01-breeches-leg | crease | 0.9942 | 3.63 | 0.082 | 0.25 | n/a | 0.090 | 18957 | 0.120 |
| crease-02-tee-body | crease | 0.9947 | 4.50 | 0.072 | 0.25 | n/a | 0.067 | 18060 | 0.121 |
| crease-03-tennis-shoe | crease | 0.9892 | 8.66 | 0.125 | 0.25 | n/a | 0.123 | 22726 | 0.093 |
| crease-04-breeches-back | crease | 0.9941 | 3.70 | 0.072 | 0.25 | n/a | 0.079 | 19560 | 0.129 |

mean SSIM (n=9) 0.9927 · median/worst/best 0.9938/0.9884/0.9950
mean |ΔL| inside the mask 5.311 levels · mask coverage max 0.1252 (cap 0.25)
cap bound on 0 fixtures · protection overlap 0 px over 5 logo cases
mean crease hit 0.0795 · wall clock 1.09 s total / 0.121 s mean
```

Read the SSIM column the way `evals/metrics/prettify.py`'s docstring says to: **it is a
destruction budget.** A pass-through scores a perfect 1.0, so `0.9927 ≥ 0.80` is trivially
satisfied — that number cannot distinguish "a gentle correction" from "nothing happened". The
`mean |ΔL| in mask` column is what stops that ambiguity: 3.4–8.7 levels of luminance change
inside the mask is a real, if deliberately mild, correction. **Nothing here measures whether a
crease is gone.** That is E2-T3's manual rubric.

The mask numbers, which are the ones with teeth:

* **protect overlap = 0 px on all 5 logo cases**, measured from the same mask the fill
  consumed, `pytest`-asserted per case by `test_protection_region_is_never_invaded`.
* **coverage 0.044–0.125 against a 0.25 cap** — the cap never bound on a real photograph,
  because the score floor was already stricter. The cap binds on the synthetic over-creased
  garment in the test suite, which is the only way to show the rail works.
* **crease hit 0.036–0.123**: on the `crease-*` fixtures, 4–12 % of the garment interior inside
  the drawn crease box is selected; on the `logo-*` fixtures the band under the label is
  0.036–0.092, which is what makes the protection subtraction load bearing. Low by design —
  see "what is not proven" below.

## Fixtures — `evals/fixtures/prettify/`

| | |
|---|---|
| cases | 9 = **5 logo** + **4 crease** |
| files | 18 PNGs + `manifest.json` + `build_fixtures.py` + `README.md` + `CREDITS.md` (22 files) |
| size | **1.10 MB** total (limit 4 MB); long edge **480 px** (limit 800) |
| sources | 9 of the 17 `evals/golden-provisional` photos: `top-02`, `bottom-03`, `shoe-02`, `acc-03`, `outer-01`, `bottom-01`, `top-01`, `shoe-04`, `bottom-06` — CC0 / PD / CC BY, all attributed |
| truth | `protect_box` on the 5 logo cases, `crease_box` on all 9 |

Two design decisions worth flagging, because they are what makes the fixtures able to fail:

1. **A fold is re-centred to cross every label**, and the fold field is applied *after* the
   label, so the crease is visible on the print too. Without that, the protection subtraction
   is a no-op on a flatly lit photo and `test_protection_region_is_never_invaded` would stay
   green with the subtraction deleted. `test_protection_subtraction_is_load_bearing` asserts
   the premise (the candidate mask enters all 5 boxes).
2. **Every flat-lay carries a ~3° warm camera cast** (`CAMERA_CAST = (1.035, 1.0, 0.96)`).
   Without it the backdrop is *exactly* `#F2F1EE` — which is what `rectify()` composites — and
   the white-balance step would be measured as an identity on every fixture.

## Mutations (6, all killed, run only in `/tmp` copies)

`prettify.py` md5 **before and after the whole run: `9efd6438fec35ca8daca34b97761d3ac`** — the
real file was never written. Each mutation was applied to a fresh copy of the tree, the
prettify tests were run there, then the copy was restored from pristine and its md5 compared
to the pristine md5 (all `True`).

| # | mutation | md5 (mutant) | red test(s) |
|---|---|---|---|
| M1 | protection subtraction removed (`final = cleaned & interior`) | `3ed6ea2c80e491f363e3007cd62d3edf` | `test_protection_region_is_never_invaded` |
| M2 | `WRINKLE_COVERAGE_CAP = 1.0` | `421562b5f8dc41935749a422e766cbf6` | `test_coverage_cap_binds_on_an_over_creased_garment` |
| M3 | white balance made an identity (`rgb * 1.0`) | `9d4d559a1fd11011334bd9b01a659f82` | `test_white_balance_pulls_a_known_cast_back_to_neutral`, `test_outside_the_mask_only_the_white_balance_gain_moves` |
| M4 | boundary band removed (`interior = garment`) | `0c7b21b3ff01ed6bbbffe0de99f31838` | `test_mask_never_enters_the_boundary_band` |
| M5 | automatic logo detector disabled (`PROTECT_SCORE_MIN = 10.0`) | `1d405a1e92408024b200f9c051774704` | `test_automatic_detector_fires_on_the_synthetic_ink` |
| M6 | fill disabled (`FILL_STRENGTH = 0.0`) | `58a5cc8b3636270d42c1dc43300d1d86` | `test_fill_does_not_change_pixels_outside_the_feathered_mask`, `test_fill_actually_attenuates_local_contrast_inside_the_mask` |

**One honest note about the mutation run itself.** M2's first attempt *survived* — because my
`replace("WRINKLE_COVERAGE_CAP = 0.25", ...)` matched the **docstring** line
(`` **Coverage cap `WRINKLE_COVERAGE_CAP = 0.25`** ``) before the constant, so the constant was
never touched and the mutant was a no-op that "passed". A surviving mutant can mean "weak test"
or "mutation never applied", and only printing the mutated line distinguishes them — which is
why the runner now prints it. Recorded here rather than quietly re-run: that is precisely the
failure mode mutation testing exists to expose, and I hit it in the tooling first.

M1 kills only one test. That is worth saying plainly: the "never overlap the logo" claim has
**one** behavioural killer plus the fixture premise test, so a reviewer who deletes the
subtraction gets one red, not a wall of red. The premise test
(`test_protection_subtraction_is_load_bearing`) does not go red under M1 — it asserts the
candidate mask, not the final one — so it must not be counted as a second kill.

## `make test` / `make lint`, real tails

```console
$ make test
# pass 72                     (node; baseline 72)
449 passed, 1 warning in 30.55s          (pytest; baseline 421, +28 here)

$ make lint
All checks passed!                       (ruff)
packages/shared lint: Done
apps/mobile lint: Done
```

`ruff format --check .` reports 35 files would be reformatted — the repo is not format-clean
and `make lint` does not run it, so I left the new files hand-formatted like their neighbours
rather than reformatting the tree.

## Out of the declared file list — two files, both deliberate

1. **`packages/vision/tests/test_imports.py`** — `test_public_signatures_are_stable` pins the
   exact parameter list, and the ticket requires a `protect` keyword. The frozen list gained
   `protect`, plus an assertion that its default is `None`. `prettify`'s existing three
   parameters are unchanged, so every current caller compiles and behaves identically.
2. **`evals/tests/test_harness.py`** — `test_no_masked_set_means_unmeasured_with_a_reason`
   asserted `measured == {}`. With a second stage measurable off its own shipped fixtures that
   assertion now encodes "only one stage can ever be measured", which is the opposite of what
   the test exists for. It now asserts `"segmentation mean IoU" not in measured` — same claim,
   about the right row — with the reason in a comment.

Neither change moves a threshold, and both are narrower than the alternative (silently
renaming a parameter, or leaving a test that would fail the moment a second stage landed).

## Still not standing / not proven

1. **SSIM ≥ 0.80 is not a quality gate here, and the report says so.** A pass-through scores
   1.0; the classical fill scores 0.9927. The number therefore certifies "we barely damaged the
   garment", nothing more. If round 2 replaces the fill with an inpaint, this row will start
   measuring something real — and it will also start being breakable.
2. **No crease-removal measurement exists.** `crease hit` says the mask selected pixels inside a
   drawn crease box; it says nothing about whether the crease disappeared. Catalogue photos
   have no creases, so the honest substitute is E2-T3's manual rubric on phone photos. Until
   then, "the de-wrinkle works" is unmeasured, and calling the step *shadow/crease attenuation*
   is the only claim the numbers support.
3. **The logo fixtures measure arithmetic, not detection.** The labels are drawn by
   `build_fixtures.py` in a bitmap font on clean fabric. Real packaging adds embossing, foil,
   tonal prints on tonal fabric, and small multi-line care text. The automatic detector was
   calibrated on these nine cases; its **recall on real garments is unknown**, and one fixture
   (`logo-04`, a tartan belt) shows the failure mode up close: the detector cannot tell print
   from a busy woven pattern, so on patterned fabric it will over-protect (harmless) and
   confuse "pattern" with "logo".
4. **The white-balance sample assumes a backdrop exists.** On a garment cropped to the frame
   the ring holds fewer than 500 pixels and the step is skipped with
   `white_balance_detail.skipped = true` and gains `[1.0, 1.0, 1.0]`. That is honest, but it
   means the stage's headline correction silently does nothing on the photos most likely to be
   badly lit. A per-image grey-patch or illuminant estimate would be the real fix; neither is
   here.
5. **White balance is nearly a no-op on the production input path.** `rectify()` composites the
   garment onto exactly `#F2F1EE`, so on any real flat-lay from that stage the backdrop is
   already neutral and the gains come out ~1.0. It only earns its keep if rectify stops
   compositing the backdrop, or if raw photos are passed in. That is worth deciding before
   round 2 rather than carrying a stage that looks busy and does nothing.
6. **`WRINKLE_SCORE_MIN = 0.55` is tuned in-sample on 9 fixtures.** No held-out set. The
   detector's behaviour on dark garments (where a 20 % luminance dip is 12 levels, not 40) is
   likely worse than on the pale museum fabric used here — `crease-04` is the only dark case
   and it scores the second-lowest crease hit (0.079).
7. **Performance is unmeasured as a requirement.** 1.88 s for the classical path at 1200×960
   on CPU (CLI run, `segment` 0.93 s + `attrs` 2.37 s beside it). PLAN has no per-stage latency
   gate for prettify — the gate is client-side `prettify_latency_ms` p50 ≤ 20 s for the whole
   pipeline — but 1.9 s of it is a plain numpy box-blur bill, and half-resolution scoring would
   cut it without changing a single threshold. Not done, because the constant-tuning would then
   need re-measuring.
8. **Round 2 will change these numbers and probably worsen them.** An SDXL inpaint inside a mask
   that covers 4–12 % of the garment will move SSIM down (more pixels changed, further), and
   the `>= 0.80` ceiling was chosen with an inpaint in mind, not with a low-pass filter. The
   current 0.9927 is not a cushion that survives a real generative step; treat it as the
   *classical* baseline.
9. **`--prettify` defaults to `classical` in the CLI**, i.e. the dev CLI now hands back a
   processed image where it used to hand back the rectified flat-lay. That is the intended
   change (it is the only mode with pixels behind it), but anyone who diffed `prettified.png`
   against an older run will see a difference that is not a bug. `--prettify pass_through`
   reproduces the old bytes exactly.

---

# E2-T2 round 1b — the protection red line, and the missing metric tests

QA returned **CONDITIONAL PASS** with one finding that invalidated the central claim: the
`protect_overlap = 0` I reported was `~(box) & box`. The test passed `protect=[protect_box]`
and then measured the same box — the subtraction *made* it zero. With no caller box the mask
put **362 px** into the ground-truth rectangle, and on QA's own 48 px synthetic wordmark the
mask touched **63 %** of the stroke pixels. In the same round, a mutation that made the metric compare an
image with itself printed `1.0000` and the whole suite stayed green.

Both are now fixed, and the second one was the more dangerous.

## 1. The detector: a resolution pyramid, not a threshold relaxation

`_auto_protect_mask` is now multi-scale (`PROTECT_SCALES = (1, 2, 4)`) and takes the strongest
of two contrast radii. The reasoning that made it work: **a large glyph at 1/4 resolution is
small type** — which is the case the old filter was already good at — and the score was made
polarity-free (`|lum − local mean|`, not "darkness"), so light ink scores like dark ink.

`PROTECT_SCORE_MIN` is **unchanged at 0.30**. QA measured that 0.40 starts eating knit texture,
so the whole gain had to come from geometry. Measured coverage of the printed pixels
(`test_auto_detector_covers_large_solid_and_light_type`, synthetic probes, short edge 260 px):

| probe | printed px | covered | mask leak |
|---|---|---|---|
| 48 px solid wordmark (QA's case) | 1605 | **98.3 %** (round 1: 12 %) | 0 |
| light type on dark fabric, 48 px (QA: 0 %) | 1605 | **100 %** | 0 |
| 24 px dark type | 484 | 100 % | 0 |
| navy on white, 36 px | 928 | 100 % | 0 |
| 14 px light-on-dark | 177 | 91 % | 0 |
| 11 px dark type (~4 % of the short edge) | 119 | **21 %** | 94 |

The last row is the honest limit: recall is **scale-relative**, and small type is still weak.
Protection dilation went 2 px → 4 px (`PROTECT_DILATE_MIN_PX`) because a stroke's antialiased
halo is 1–2 px on each side; at 2 px, 18 printed pixels of the 9 fixtures leaked, at 4 px, 0.
The cost is over-protection: the protection region now covers **19–22 % of the garment on
average, 69 % worst** (the tartan belt). That is the safe direction, and it is reported.

## 2. The red line, measured on **print** instead of on the rectangle

The fixtures now carry a second ground truth: `ink/<id>.png`, the pixels the builder actually
**printed** (glyphs + emblem, no patch background, no outline). Blank padding inside a label is
pixel-identical to the fabric around it — no pixel-level detector can separate them — so a red
line drawn on the rectangle is a red line nobody can meet; a red line drawn on the print is the
DoD's actual sentence and it is meetable.

`protect=None` (automatic detector only, no caller help), on the delivered fixtures:

| case | protect_box | ink px | residual on rectangle | residual on **printed ink** |
|---|---|---|---|---|
| logo-01-tee-chest | 8236 | 845 | 149 (1.81 %) | **0** |
| logo-02-breeches-waistband | 5104 | 358 | 34 (0.67 %) | **0** |
| logo-03-sneaker-side | 10498 | 1010 | 65 (0.62 %) | **0** |
| logo-04-belt-stamp | 4060 | 245 | 56 (1.38 %) | **0** |
| logo-05-jacket-emblem | 5800 | 609 | 78 (1.34 %) | **0** |
| **total** | 33698 | 3067 | **382** (round 1: 407 on the old fixtures, QA: 362) | **0** |

* `test_mask_never_overlaps_the_printed_ink_without_a_caller_box` — **hard 0**, no caller box,
  ground truth is the builder's ink. This is the test that would have caught round 1.
* `test_mask_never_enters_the_label_rectangle_without_a_caller_box` —
  `@pytest.mark.xfail(strict=True)`, carrying the numbers above in its reason. It is red because
  the residual is real (crease pixels inside the label's blank padding), and `strict=True` means
  the day the detector reaches 0 it turns into XPASS and fails the suite until somebody promotes
  it to a real assertion. **I did not relax it to `<= N`**: that would hide a regression back to
  362 px, which is exactly the failure QA found.
* The residual is also in the artifact, not just in a test: `python -m evals.metrics.prettify`
  prints an `auto only px` column and `evals/reports/vision.md` carries
  "…and 382 px on the same boxes with the automatic detector alone (of which **0 px are printed
  ink**)".

## 3. The other five QA findings

| finding | fix |
|---|---|
| fixture truth box 1 px off (Pillow's `rectangle` includes both endpoints) | `[x, y, x+w-1, y+h-1]`; the box and the pixels now agree by construction, and `test_fixture_manifest_records_provenance_and_truth_boxes` asserts `ink ⊂ protect_box`. Fixtures rebuilt; a rebuild reproduces byte-identical output (aggregate md5 `b417672e…` before and after) |
| `summarise()` dropped a `0.0` measurement (`if row[...]`) | `is not None`, plus `n_delta` in the summary so the denominator is printed next to the mean |
| `sources` claimed `['auto']` with `auto_px = 0` | each source is listed only if it produced pixels |
| docstring conflated `natural_coverage` (7.0–17.3 %) with final `coverage` (4.4–12.5 %), and quoted an unverifiable crease-band number | both numbers are now stated separately, and the unverifiable one is replaced by `crease_hit`, which `evals/metrics/prettify.py` prints |
| PLAN wants the **rectify+prettify** wall clock per backend; only prettify was reported | `build_fixtures.py` records `source.rectify_inputs` (the photo + alpha each fixture came from) and the metric times `rectify()` on them: **rectify `local_bbox_warp_v1` 1.30–1.43 s total / 0.145–0.159 s mean**, prettify `local_shadow_attenuation_v1` 1.54–1.66 s total / 0.171–0.184 s mean (two runs; wall clock on this CPU-only machine varies ~10 %). Both lines print |

## 4. The metric now has tests — `evals/tests/test_prettify_metric.py` (8)

`ssim_inside_mask` is pinned on: identity = 1.0 and noise strictly lowers it; the closed form
`(2µaµb + C1)/(µa² + µb² + C1)` to 1e-9 on constant images; shape mismatch raises (`mask` of
the wrong shape now raises `ValueError` with both shapes named instead of numpy's
`IndexError`); symmetry **and** sensitivity (`changed < identical` when one output pixel moves);
the mask really changes the denominator (same pair, box vs whole garment); an empty mask is
`NaN`, not 0.0; and `summarise()` keeps an exact `0.0` (two 0.0s and one 9.0 must mean 3.0).

QA's mutation — "SSIM compares an image with itself" — now reddens **five** of those tests
(M8 below), and the `summarise()` falsy-filter regression reddens the sixth (M9).

## 5. Mutations, 9/9 killed, `/tmp` copies only

`prettify.py` md5 `b6e5c75384ee4a47bca51abb76574e56` and `evals/metrics/prettify.py` md5
`4b6bc6d0b1b223aac0bf9d180802a717` are **identical before and after the whole run**; each
mutant was reverted from a pristine copy and its md5 compared (all match).

| # | mutation | red test(s) |
|---|---|---|
| M1 | protection subtraction removed | `test_protection_region_is_never_invaded`, `test_mask_never_overlaps_the_printed_ink_…`, 2 detector probes |
| M2 | `WRINKLE_COVERAGE_CAP = 1.0` | `test_coverage_cap_binds_on_an_over_creased_garment` |
| M3 | white balance made an identity | `test_white_balance_pulls_a_known_cast_back_to_neutral`, `test_outside_the_mask_only_the_white_balance_gain_moves` |
| M4 | boundary band removed | `test_mask_never_enters_the_boundary_band` |
| M5 | auto detector disabled (`PROTECT_SCORE_MIN = 10`) | the printed-ink red line + `test_automatic_detector_fires_on_the_synthetic_ink` + 4 parametrised probes |
| M6 | fill disabled (`FILL_STRENGTH = 0`) | `test_fill_does_not_change_pixels_outside_the_feathered_mask`, `test_fill_actually_attenuates_local_contrast_inside_the_mask` |
| M7 | pyramid collapsed to one scale | the printed-ink red line + the 48 px and light-type probes — i.e. **round 1's behaviour is now a red test** |
| M8 | SSIM compares an image with itself (QA's) | 5 of the 8 metric tests |
| M9 | `summarise()` drops a `0.0` again | `test_summarise_keeps_a_zero_measurement` |

## 6. `make test` / `make lint`, real tails

```console
$ make test
# pass 72                                (node; baseline 72)
461 passed, 1 xfailed, 1 warning in 44.94s   (pytest; baseline 421, +40)

$ make lint
All checks passed!                       (ruff)
packages/shared lint: Done
apps/mobile lint: Done
```

Final md5s: `prettify.py` `b6e5c75384ee4a47bca51abb76574e56` ·
`evals/metrics/prettify.py` `4b6bc6d0b1b223aac0bf9d180802a717` ·
`evals/fixtures/prettify/manifest.json` `b2f77b1a91565f59fa20e3c9cfb6de60` ·
`packages/vision/tests/test_prettify.py` `6f2fbe4da6a117cdda63a37f579516c2` ·
`evals/tests/test_prettify_metric.py` `e7eb77518a6bcf4988f3eea1db41c7f9` ·
`evals/run.py` `81ede28b987f9f09eb0d8e4b0577472a`.
Fixtures: 27 files, **1.2 MB**, long edge 480 px.

## 7. Still not standing

1. **The rectangle red line is not met, and cannot be met by a pixel detector.** 382 px of the
   5 label rectangles are unprotected without a caller box; they are crease pixels in blank
   padding. Meeting it needs either a region model (a label is a rectangle whose border is
   detected — I tried closing, hole filling, strong-edge enclosure and coarse-cluster bounding
   boxes; all either missed the padding or flooded patterned garments) or a PLAN wording change
   from "logo/text region" to "printed pixels". **This is the decision I am handing back.**
2. **Small type is the weak spot**, and the number moved once the factory dilation was used.
   The `21 % at ~4 % of the short edge` above was measured at `PROTECT_DILATE_MIN_PX = 2`;
   at the factory value of 4 it is ≈58 % with 30 px leaking, and `SNAP` @9 px is 0 % covered /
   55 px leaking. Real care-label print sits at the small end. If the product needs
   care-label text protected, this detector is not enough. The per-probe table in
   `packages/vision/prettify.py` is the authority; it is quoted per probe precisely because
   recall depends on the glyph string.
3. **Over-protection is now material**: 22 % of the garment on average, 69 % worst. On patterned
   fabric the stage will do very little de-wrinkling. That is the correct trade under a "never"
   constraint, but it is a product cost and nobody has looked at a patterned garment photo yet.
4. **All of this is synthetic type.** No printed garment was photographed for these probes; the
   numbers are detector behaviour, not recall on real packaging.
5. Unchanged from round 1 and still true: SSIM ≥ 0.80 is a destruction budget (0.9936 means
   "barely touched"), crease *removal* is unmeasured (that is E2-T3's rubric), white balance is
   near-dead code on the `rectify()` output path, and the classical path costs ~0.17 s/case at
   480 px / 1.9 s at 1200×960.

## 8. Final round (1c) — what closed, and the decision I handed back

Three things QA's round-1b audit found, all fixed after the section above was written:

* **The metric's wiring had no test.** Round 1 shipped teeth for `ssim_inside_mask()` and none
  for `run()`, so `after = before.copy()` inside `run()` reported **1.0** for every case (a
  perfect score against a 0.80 threshold) with 461 tests green. `test_run_reports_input_against_output_on_the_real_fixtures`
  now runs `run()` on the real fixtures, requires every row's SSIM to be `< 1.0`, and recomputes
  that row from the fixture bytes to 1e-9. This is the only test that pins the number the report
  publishes, as opposed to the function that produces it.
* **The probe table was quoted at a stale dilation.** The prose numbers were measured at
  `PROTECT_DILATE_MIN_PX = 2` after the constant moved to 4 — the third time in this ticket that
  a constant changed and the prose did not. The table is now generated at factory settings and
  includes the counterexamples that break the absolute claim: `M` @48 leaks 38 px,
  `MADE IN PORTUGAL` @36 leaks 66 px, `SNAPLIST` @36 leaks 54 px.
* **`_time_rectify` promised "never raises" and raised.** One stale `rectify_inputs.photo` path
  turned the whole prettify SSIM row into UNMEASURED. It now returns `(None, reason)` and the
  reason is printed, so a timing number cannot take the measurement down with it.

**The wording decision, stated once.** `docs/PLAN.md` E2-T2 says the inpaint mask must *never*
overlap the logo/text region. That is only satisfiable if "region" means **printed pixels**:
blank padding inside a label is pixel-identical to the fabric around it, so no pixel detector can
separate them, and the workaround (grow the dilation until the rectangle is covered) was measured
to zero out crease detection. So:

* printed ink → **hard assertion, 0 px** on the fixture set and the four equal-length `SNAP`
  probes; **not** a general claim, and the counterexamples above are printed in the module
  docstring rather than omitted;
* the label rectangle → **382 px residual**, pinned by an `xfail(strict=True)` test whose reason
  carries the five per-case numbers. `strict=True` means reaching zero turns it into XPASS and
  reddens the suite, which forces the assertion to be promoted instead of the xfail rotting.

I did not edit `docs/PLAN.md` to match the implementation: the acceptance text stays as written,
and the interpretation plus its evidence lives here.

## 9. E2-T2 round 2 — SDXL inpainting: the machine works, the picture does not

Round 1 shipped the mask machinery and refused `mode=SDXL_INPAINT` with the fix inside the
error. Round 2 implements it and runs it on this machine. **Headline: the mode is now real,
deterministic, protected and timed — and its output is not shippable.** That sentence is the
deliverable; the rest of this section is the evidence for it.

### 9.0 Three measured failures, before anything else

Anyone reading this to decide "can we just switch SDXL on" needs these three lines and nothing
else. They are also at the top of `prettify_sdxl()`'s docstring, so the answer travels with the
code.

| # | gate | E0-T5 / E2-T2 requires | measured | verdict |
|---|---|---|---|---|
| 1 | `prettify SSIM (inside mask)` | **≥ 0.80** | **0.6920** (crease-01), **0.7415** (logo-01) | **fails** — classical scores 0.9856 / 0.9792 on the same two |
| 2 | prettify latency | **p50 ≤ 20 s, p90 ≤ 35 s** | **38.3 s** warm (crease-01), **42.5 s** warm (logo-01); +10 s cold load | **fails ~2×** — both samples are above *p90*, so p50 ≈ 40 s |
| 3 | "did it remove the crease" | the thing the mode exists for | the fold comes back as a thin hard blue-grey **scratch line** | **fails** — on a real photo the garment **looks like someone drew on it with a fine pen** |

Number 3 is not a matter of taste and it is not a step-count artefact (§9.5 has the sweeps).
The wrinkle mask is a 1–3 px hairline/speckle field; SDXL's VAE downsamples 8×, so the mask is
**0.25–0.75 latent pixels wide** and the UNet is asked to fill a feature it cannot represent.
Widening it until it is representable means replacing 58–86 % of the garment (measured).
**SDXL inpainting wants a region, not a network of creases.**

Nothing here says to keep it off in *code* — it is implemented, tested, deterministic and
protected. It says do not put it on the product path.

### 9.1 What was installed

`uv add torch diffusers transformers accelerate safetensors huggingface-hub`, recorded in
`pyproject.toml` + `uv.lock` and in `docs/DEPS.md` with licence and reason per row.

| package | version | licence | installed |
|---|---|---|---|
| torch | 2.15.0 | BSD-3-Clause | 581 MB |
| transformers | 5.22.1 | Apache-2.0 | 55 MB |
| diffusers | 0.42.1 | Apache-2.0 | 29 MB |
| sympy (torch transitive) | 1.14.0 | BSD-3-Clause | 29 MB |
| tokenizers (transformers transitive) | 0.23.3 | Apache-2.0 | 9.4 MB |
| networkx (torch transitive) | 3.6.1 | BSD-3-Clause | 7.8 MB |
| hf-xet (hub transitive) | 1.8.0 | Apache-2.0 | 7.1 MB |
| huggingface_hub | 2.4.0 | Apache-2.0 | 3.5 MB |
| accelerate | 1.15.0 | Apache-2.0 | 1.6 MB |
| safetensors | 0.8.0 | Apache-2.0 | 1.1 MB |

≈ **0.72 GB of site-packages** on top of the previous 190 MB venv. `torch.backends.mps.is_available()`
→ **`True`** (torch 2.15.0, `is_built()` also True, macOS arm64). Nothing else in the venv moved:
`onnxruntime`, `numpy`, `pillow` versions are unchanged, so `segment()`/`matte()` numbers from
earlier sections still stand.

`torch` and `diffusers` are imported **lazily, inside the generative branch**.
`test_importing_prettify_does_not_import_torch` runs a subprocess with a meta-path hook that
raises on `torch`, imports `packages.vision.prettify`, and asserts `torch` and `diffusers` are
absent from `sys.modules`. Without that rule a checkout with no round-2 extras could not even
run `make test` — which is the reason the cheap segmentation backend runs on onnxruntime at all.

### 9.2 The weights

| | |
|---|---|
| model | `diffusers/stable-diffusion-xl-1.0-inpainting-0.1` |
| revision | **`115134f363124c53c7d878647567d04daf26e41e`** (a commit sha, not `main`) |
| variant | `fp16` only — 18 files, **6 941 218 469 bytes (6.94 GB)**; the fp32 set is another ~13 GB and produces the same pixels |
| path | `~/.cache/snaplist/models/sdxl-inpaint-0.1/` (`SNAPLIST_SDXL_DIR`, else `SNAPLIST_MODEL_DIR`/`sdxl-inpaint-0.1`) |
| fetched by | `make fetch-sdxl` — **not** part of `install`, **not** reachable from `prettify()` |
| measured download | **65 s** on this connection, 4 workers (`hf-xet` was used) |
| licence | **CreativeML Open RAIL++-M** — commercial use allowed, **use-restricted**, and the restrictions travel: no unlawful/defamatory/harassing/deliberately-harmful use, and redistributing the model or a derivative must carry the same restrictions. Outputs are not encumbered the same way. Recorded in `docs/DEPS.md`. |

Integrity is **per-file byte counts pinned against the revision** (`SDXL_REQUIRED_FILES`, 18
entries), not a `.complete` sentinel: a truncated UNet file is caught before load, and a
sentinel is the easiest thing in the world to copy next to the wrong files. `make fetch-sdxl`
downloads into a sibling `*.partial` tree, verifies, then `os.replace`s into place; any failure
deletes the partial tree. Two tests cover it — download raising, and download "succeeding" with
the wrong bytes — both assert nothing is left on disk.

Run-time is **zero network**: `from_pretrained(..., local_files_only=True)`, and the whole
`test_prettify.py` module still runs under the autouse socket trap.

### 9.3 What it costs, cold and warm

`packages/vision/cli.py --prettify sdxl_inpaint`, i.e. the module defaults: **30 steps,
strength 0.85, guidance 5.0, seed 20261104**, working canvas = long edge 1024 (multiples of 64),
MPS fp16, attention slicing on, VAE slicing on. Two runs per fixture in one process.

| fixture | canvas in | canvas out | cold | warm | model load | generate | effective denoise steps |
|---|---|---|---|---|---|---|---|
| `crease-01-breeches-leg` | 360×480 | 704×1024 | **50.9 s** | **38.3 s** | 10.3 s | 38.9 s | 25.5 |
| `logo-01-tee-chest` | 480×360 | 1024×768 | **55.1 s** | **42.5 s** | 10.5 s | 42.9 s | 25.5 |

Roughly **1.9 s per effective denoise step** at 0.72–0.79 MP on M4 Pro MPS. No OOM at 24 GB
with attention slicing; peak footprint was not measured with a profiler, only "it ran". The
pipeline is `lru_cache`d per (dir, device, dtype), so "warm" means "weights already in RAM",
which is what a worker doing a second item would see.

**Against the product gate (E0-T5: prettify p50 ≤ 20 s, p90 ≤ 35 s): this fails, by roughly 2×.**
Warm is 38.3 s and 42.5 s — both above the p90 ceiling, so these two numbers alone would put
p50 ≈ 40 s. The ticket's acceptance ("implement and measure on this machine") is met; the
product gate is not, and step-trimming does not close a 2× gap: the §9.5 sweeps ran 20 steps
on a 768 canvas at **15.8 s per image** of generation time, which is still the whole p50 budget
before the ~10 s load, and the artefacts do not improve with fewer steps — they get worse.

### 9.4 SSIM, and what it is and is not

Same definition as `evals/metrics/prettify.py` (luminance SSIM, 11×11 window, over the garment
mask). `in mask` = over the final wrinkle mask.

| fixture | SSIM garment SDXL | SSIM garment classical | SSIM in mask SDXL | SSIM in mask classical |
|---|---|---|---|---|
| `crease-01-breeches-leg` | 0.8504 | 0.9945 | **0.6920** | **0.9856** |
| `logo-01-tee-chest` | 0.8253 | 0.9916 | **0.7415** | **0.9792** |

Read this as a **destruction budget**, as the metric docstring insists: the generative step
moves far more pixels, far further, than the classical fill — 0.69 vs 0.99 *inside the mask it
was allowed to touch*. It is not evidence that the creases went away. The 0.85 on the garment is
mostly the white balance (a global gain) plus the masked region.

Mask audit, from the same mask object `prettify()` consumed:

| fixture | mask coverage | mask px | repaint px | feather α>0 px | feather radius | `protect_overlap` | protect region px |
|---|---|---|---|---|---|---|---|
| `crease-01-breeches-leg` | 0.0772 | 4 412 | 4 412 | 41 872 (73 % of interior) | 3 px | **0** | 2 332 |
| `logo-01-tee-chest` | 0.0939 | 8 647 | 8 647 | 62 989 (68 % of interior) | 3 px | **0** | 10 809 |

`protect_overlap = 0` in both cases (caller box passed for the logo case): the printed label is
untouched, and the composite guarantees it structurally — `out = balanced·(1−w) + generated·w`
with `w == 0` everywhere in the protection region, so the output there is the white-balanced
input **bit for bit** (`test_sdxl_output_equals_the_white_balanced_input_where_the_weight_is_zero`).

### 9.5 What my eyes see (the part the numbers do not show)

I looked at each output next to its input, cropped on the ground-truth crease/label box, at 2–3×.

* **`crease-01-breeches-leg` — the folds are still there, and worse.** Each soft dark horizontal
  fold comes back as a **thin, hard, blue-grey speckled line**, like a scratch or a stray
  pinstripe. The fold is not removed; it is *traced*. The fabric between the folds is preserved,
  which is the one thing that went right. If someone handed me this as a "cleaned" listing photo
  I would ask what scratched the trousers.
* **`logo-01-tee-chest` — the label is perfect and the cloth around it is scratched.** `SPRING
  / 1948` and the red dot are pixel-identical; the diff heatmap is black over the whole label
  rectangle. That is the protection working. But the tee's body is covered in long white/blue
  **streak marks**, and the drawn fold crossing under the label is traced rather than filled.
* **So: yes, it looks repainted.** Not "slightly retouched" — it looks like the image was drawn
  on with a fine brush.

**Why, and this is the useful part.** The wrinkle mask is a **hairline/speckle field**: lines
1–3 px wide with a lot of isolated pixels. SDXL's VAE downsamples 8×, so at a 704×1024 canvas
the mask is **0.25–0.75 latent pixels wide**. The UNet is being asked to fill a feature it
cannot represent, and the decoder resolves it as a high-frequency ridge along the contour — the
scratch. I tested the obvious fixes rather than guessing:

| experiment | mask coverage of interior | what it looked like |
|---|---|---|
| shipped: thin mask, 30 steps, strength 0.85 | 7.7 % | soft folds → hard blue-grey scratch lines |
| thin mask, strength **0.45** | 7.7 % | same scratch lines, 12.6 s — *not* a noise effect |
| thin mask, strength **0.65** | 7.7 % | same, slightly stronger |
| mask dilated **+2 px** | **57.7 %** | flat grey blobs with hard latent-grid contours — clearly broken |
| mask dilated **+4 px** | **85.7 %** | the garment is gone, replaced by grey rectangles |
| mask dilated **+8 px** | **103 %** (bands merge) | same, worse |

Dilation does not scale linearly because the mask is mostly isolated pixels, not bands: area
grows with the *square* of the radius. Widening it into something the latent grid can represent
means repainting 58–86 % of the garment, which is a different and much worse product than a
de-wrinkler. **Conclusion: SDXL inpainting is the wrong tool for a crease *network*. It wants a
region — a stain, a hole, a removed object — not a hairline.** That is a modelling decision for
Bert, not a tuning knob, and I did not silently pick a different model or quietly drop steps to
manufacture a prettier number.

### 9.6 Where this leaves the ticket, honestly

* **Passed**: the mode exists; the 6.9 GB is pinned, verified and fetched only by `make
  fetch-sdxl`; the run-time path has zero network calls; it is deterministic (same seed ⇒
  byte-identical PNG, asserted on MPS); it never writes outside the feathered mask; the logo
  region is untouched; the timing is real and the steps/strength/seed/canvas are recorded in
  `meta.notes` on every run.
* **Failed**: it is not a de-wrinkler. It is a scratch-line generator on this mask, ~2× over the
  E0-T5 latency gate, and the fix is not in this ticket's scope.
* **Recommended next step**: make the mask a *band* — which needs a crease detector that emits
  regions, not edges, plus a coverage budget that survives the latent grid. That is a separate
  ticket, and it is the only path on which this mode becomes useful.
* **A recommendation I got wrong, recorded because the correction matters more than the
  suggestion.** I first proposed re-pointing `mode=SDXL_INPAINT` at **defect** inpainting —
  stains, holes, an object to remove — because those are region-shaped and would suit the
  model. That is technically true and **product-wrong**: `ItemAttributes` carries a `defects`
  field because a listing has to *disclose* imperfections. Using a generative model to erase a
  stain is helping a seller lie to a buyer about the item's condition, in the one field the
  schema added to prevent it. So: generative inpainting is technically usable on region-shaped
  defects and **ethically unusable in this product**. Creases are the only legitimate target
  here, and creases are exactly what this method cannot do. That is the whole finding.

### 9.7 Still not standing (round 2 additions)

1. **The output is not shippable** (§9.5). This is the first item on purpose.
2. **~40 s warm / image** vs the 20 s p50 gate — 2× over, and the time is dominated by UNet
   passes, not by anything I can trim.
3. **No memory measurement.** "It did not OOM at 24 GB with attention slicing" is not a number;
   I did not profile peak footprint, so I cannot say how much headroom a 4096 px flat-lay would
   need. Attention slicing is on and recorded, but its cost in seconds is not isolated.
4. **The feather boundary at the protection region is a step.** The outward ring is clipped to
   `interior & ~protect_mask`, so where a crease runs into the label the repaint alpha drops
   from ~0.85 to 0 over one pixel. On these fixtures it does not show, because the painted fold
   under the label is inside the label. On a real photo it might. Not measured.
5. **The 0.8 %-of-short-edge feather radius** is calibrated by eye on two outputs, and my eye
   was looking at outputs that are broken anyway. Revisit if the mask ever becomes a band.
6. **`transformers` is pinned at 5.22.1** because that is what resolved; diffusers 0.42 works
   with it here, but the two moving major versions together is a hazard nobody has bounded.
   Recorded, not solved.
7. Unchanged from round 1: the label *rectangle* still has a 382 px residual without a caller
   box, small type is still the detector's weak spot, and over-protection still costs
   de-wrinkling on patterned fabric.

## 10. E2-T2 round 2b — the regression my own install caused, and the 6.9 GB on disk

Round 2 shipped the generative mode and left two things behind that had to be fixed before the
work could be called done. Both were found by the lead reading the diff, not by me, and both
are worth writing down because they are the same failure in two shapes: **a fact about the
environment that the code kept in prose instead of keeping in a check.**

### 10.1 `runtime.probe()` selected a backend that cannot run

```
$ python -c "from packages.vision.runtime import probe; r=probe(); print(r.device, r.backend)"
mps birefnet          # before the fix — and segment() refuses birefnet
```

Rule 3 of the policy read *"torch sees cuda or mps ⇒ BiRefNet"*, which is only sound if BiRefNet
is runnable. It is not: `segment.py` implements the u2net path only, and there are no BiRefNet
weights anywhere. While torch was absent the rule never fired, so the bug was invisible; the
moment 0.72 GB of torch landed for the SDXL work, **the default vision path stopped working on
a machine where nothing about vision had changed.** `evals/tools/seg_iou.py` calls
`probe().backend` and hands it straight to `segment()`, so that tool was broken too.

I also reported the wrong cause for it: I saw the CLI print
`SEGMENT UNVAILABLE: … needs torch, which is not installed … (SNAPLIST_SEGMENT_BACKEND=birefnet)`
and concluded `SNAPLIST_SEGMENT_BACKEND` was set in `.env`. It is not, and never was — I read a
stale sentence as evidence. Which is exactly what the stale sentence deserved.

The fix, in three parts:

* **`segment.birefnet_available() -> (bool, why-not)`** is now the single place that knows
  whether this build can run BiRefNet, and it is a *function*, so the policy can ask before
  selecting and the refusal message is built from the same string. `probe()` calls it through
  `runtime._birefnet_available()` before choosing.
* **Rule 3 asks first.** Accelerator present **and** backend runnable ⇒ BiRefNet; otherwise the
  cheap backend, with the accelerator named in `notes`:
  *"mps is present but BiRefNet cannot run in this build: no BiRefNet implementation and no
  BiRefNet weights exist … Falling back to the cheap backend (u2net), which runs on the CPU —
  stated here, not silent"*. `RuntimeInfo.device` is `"cpu"` in that case, because u2net runs on
  onnxruntime's CPU EP and a cost report claiming "mps" for a CPU-bound session is the same
  class of lie as the message that started this.
* **The refusal no longer blames torch.** `segment(BIREFNET)` now says BiRefNet does not exist
  in this build and points at `probe()`; `test_birefnet_refusal_does_not_blame_a_missing_torch`
  asserts `"not installed" not in message`, so the next time a sentence like that goes stale
  the suite reddens instead of the runtime policy going quietly wrong.

New tests: probe **never** selects a backend that cannot run (with `_birefnet_available` faked
both ways), the fallback names both the accelerator and the reason, an explicit
`SNAPLIST_SEGMENT_BACKEND=birefnet` override is still honoured but carries a note predicting the
refusal, and the real machine's default path is asserted to be runnable.

**A weakness in this fix, stated rather than hidden.** `birefnet_available()` is a
**written-down dead switch**: it returns `False` with a reason, and the only thing that keeps
the policy honest is that nothing overrides it. A `return False` is a fact the code can be
asked about, which is a real improvement on a sentence in an error message — but it is still one
place to forget. The root fix is a single `_IMPLEMENTED_BACKENDS` set in `segment.py` from which
the policy, the refusal text and the CLI's choices all derive; that is a ticket, not a patch.

**If BiRefNet is implemented, three things must change together or the messages become lies
again:** (1) the return value of `segment.birefnet_available()` (the refusal text derives from
it, so it needs no edit — that is the point of the indirection), (2) the weights/implementation
in `segment.py`, and (3) the two tests that pin the current truth,
`test_birefnet_refusal_does_not_blame_a_missing_torch` and
`test_probe_asks_segment_py_instead_of_keeping_its_own_answer`. Miss the third and the suite
stays green while the policy goes back to guessing.

```
$ python -c "from packages.vision.runtime import probe; r=probe(); print(r.device, r.backend)"
cpu u2net             # after the fix
```

### 10.2 0.72 GB that nobody chose — now an optional dependency group

The generative stack moved from `dependencies` to **`[dependency-groups] sdxl`** (PEP 735, the
same section as `dev`; this root is a virtual project so `optional-dependencies` would be the
wrong mechanism). `make install` is lean again; `make install-sdxl` does `uv sync --group sdxl`
and then `fetch-sdxl`.

| target | does | size |
|---|---|---|
| `make install` | `uv sync` + `pnpm install` | **no torch, no SDXL** |
| `make install-sdxl` | `uv sync --group sdxl` + `make fetch-sdxl` | 0.72 GB + 6.9 GB |
| `make fetch-sdxl` | weights only | 6.9 GB |

A plain `uv sync` now **prunes** the stack from an existing venv, which is the intended
behaviour and worth knowing before you run `make install` on a machine where you were using the
generative mode.

Two different gaps, two different fixes, so the refusal reports them separately. Both are
checked with `importlib.util.find_spec` (which does not import) and per-file byte counts:

* missing group ⇒ *"the `sdxl` dependency group is not installed (missing: torch, …) — run
  `make install-sdxl`"*;
* missing weights ⇒ *"N of 18 pinned weight files are absent or the wrong size in … — run `make
  fetch-sdxl`"*;
* missing `huggingface_hub` at fetch time ⇒ a `RuntimeError` naming the group, not an
  `ImportError` traceback from a `python -c` one-liner.

`make test` is green with **neither** the group nor the weights: three tests skip
(`skipif not sdxl_backend_available()`), the fetch tests `importorskip("huggingface_hub")`, and
the subprocess purity test proves the availability check answers by *finding* torch rather than
importing it — so `pytest` collection does not pay torch's import either.

### 10.3 The 6.5 GB on disk is Bert's decision, not mine

`~/.cache/snaplist/models/sdxl-inpaint-0.1/` still holds **6.5 GiB on disk (6.94 GB, 18 files)**
of pinned weights. I did not delete it.

* Keep it if anyone wants to re-run the §9 measurements: they need it, and it is the only way
  to check that the numbers in this document are real.
* Delete it to reclaim 6.5 GiB; the cost is one **65 s** re-download (`make fetch-sdxl`,
  measured on this connection) and nothing else. No code path depends on the weights being
  present — `mode=SDXL_INPAINT` refuses cleanly without them, and that refusal is what the
  test suite exercises on every run without them.

`evals/metrics/prettify.py` still measures **`mode=CLASSICAL` only** and is unchanged: the
headline number in `evals/reports/vision.md` must come from the path that passes the gate.

# E1-T4 — the segmentation IoU report on the 20 ADR-010 masks (2026-11-12)

Ticket: `E1-T4`, "segmentation IoU report on the 20 masks that satisfy ADR-010". Its two
dependencies are finally both landed: E1-T2 (`segment()`) long ago, and E0-T1 on 2026-11-12
(masks re-made and point-reviewed in PR #15/#16, edit-distance threshold decided in PR #17).
Until PR #17 this ticket could not be closed: with U1 UNMET every report said so.

## Acceptance, run verbatim

```
$ python -m evals.run --suite vision && grep -A2 "segmentation mean IoU" evals/reports/vision.md
| segmentation mean IoU | >= 0.92 | 0.9357 (n=20, golden) | E1 |
| rectify SSIM (inside mask) | >= 0.85 | UNMEASURED | E2 |
| prettify SSIM (inside mask) | >= 0.80 | 0.9936 (n=9, fixtures/prettify) | E2 |
```

and the row's provenance line now ends:

```
the point-review register is 20/20 reviewed with the edit-distance threshold SET, so U1 is MET
and the hand-mask gate is closed; per-fixture IoU and the mask paths are in
evals/reports/seglab.md (`python -m evals.tools.seg_iou --golden-dir evals/golden`)
```

The DoD asks for the IoU **per fixture**, the mean over the 20 masks, and **the mask paths
listed**. That is `evals/reports/seglab.md` (gitignored, regenerated by the command quoted
above). Its summary block:

```
| segmentation mean IoU | **0.9357** |
| median IoU | 0.9857 |
| worst / best | 0.5642 / 0.9973 |
| masked entries scored (n) | 20 |
| entries without a mask (not scored) | 20 |
```

## Three sentences in the reports were false, and are now derived

All three were written before the register could ever reach 20/20, and none of them was a
typo — each was a literal that outlived the fact it described.

| where | said | truth |
|---|---|---|
| `vision.md` | `AI-assisted point-reviewed=1` | 20 entries, one shared `mask_source` string; the code counted *distinct strings* with `len()` |
| `vision.md` | `U1 is UNMET (register 0/20 reviewed)` | register is 20/20, threshold SET → U1 MET |
| `seglab.md` | `register is 20/20 point-reviewed (U1 UNMET)` | self-contradictory in one sentence |

Fixed at the source: `seg_iou.render()` and `evals/run.py::_measure_segmentation()` now derive
the sentence from `ReviewState` (`current_review_state()`, the same reader for both) and count
entries per tier with `seg_iou._source_counts()`. Two tests hold it down
(`test_the_report_says_u1_met_when_the_injected_state_says_so`,
`test_the_report_says_u1_unmet_when_the_injected_state_says_so`,
`test_the_harness_note_counts_entries_per_tier_not_source_names`); both were mutation-killed —
forcing the report to always say MET turns the UNMET test red, and reverting the tally to
`len(names)` turns the count test red.

## What the number means — and what it does not

* **0.9357 ≥ 0.92 on the golden set**, so the threshold the ticket quotes is met. Distribution:
  7 rows ≥ 0.99, 17 ≥ 0.95, and **3 rows below 0.90**: `hard-01` 0.5819, `hard-03` 0.8348,
  `hard-05` 0.5642.
* The direction of the error is **measured, not guessed**: for `hard-01`, 99.94% of the
  reference sits inside the prediction with only 172 reference px missed and 220,582 px
  predicted beyond it; for `hard-05`, 99.04% / 1,443 px / 113,850 px. Both are the two
  "garment colour ≈ furniture colour" hard cases: the model covers extra area (the chair, the
  sofa) rather than failing to find the garment. `hard-03` loses in both directions
  (90.04% containment, 34,954 px missed, 27,596 px beyond) — a backlit near-silhouette.
  `seglab.md` prints these counts next to the rows below 0.90 so nobody has to guess.
* **I did not visually adjudicate those three in this session.** The model driving this run
  cannot read images, so the claims above come from pixel counts only. Anyone quoting the
  number should still eyeball `masks/hard-0{1,3,5}.png` against `photos/`.
* **Half the photos are synthetic** (Bert ruling 3, PR #16). This IoU therefore describes
  synthetic inputs as well as real ones and must not be quoted as real-photo performance. The
  original E0-T1 goal of 40 real seller photos is still not met.
* Known reference-quality limits, already in the register's DISCLOSURE note: `hard-05`'s mask
  covers only part of the draped trousers; `hard-01` is a low-confidence silhouette. Their low
  IoU is partly the reference's fault and the report does not pretend otherwise.

## `make test`, real tail

```
927 passed, 6 skipped, 1 xfailed, 1 warning in 110.93s (0:01:50)
```

**Environment debt found on the way.** `make test` died before I changed anything:
`apps/api/tests/conftest.py` could not import `fastapi`, which is a *main* dependency in
`pyproject.toml`, and `pydantic-settings` / `uvicorn` were missing too. The venv was therefore
not the one `make install` produces. I installed exactly those three with `uv pip install`
(**not** `uv sync`, which would also have pruned the `gen` group) and deliberately left
`pillow` at 13.0.0 — the project pins `<12`, so the venv was already inconsistent with
`pyproject.toml`, and downgrading mid-ticket would change image behaviour without a
measurement proving it safe. Someone should reconcile the venv with the lockfile on purpose,
not as a side effect of a ticket.

## Files touched beyond the ticket's list — stated, not buried

E1-T4 names `packages/vision/segment.py` and `evals/reports/seglab.md` (generated). I did not
touch `segment.py`; the model is unchanged. I did touch:

* `evals/tools/seg_iou.py` — the report generator that produces the deliverable, and the three
  fixes above live in it;
* `evals/run.py` — the harness note that summarises the same number in `vision.md`;
* `evals/tools/validate_point_reviews.py` — module docstring only (it still said "today,
  prints U1 UNMET");
* `evals/tests/test_seg_iou_null_mask.py`, `evals/tests/test_harness.py`, and this file.

The deliverable was not reachable without them: both generators contained hardcoded claims that
are now false. If the integrator treats that as a scope violation, the alternative is a report
that contradicts the ledger it cites.
