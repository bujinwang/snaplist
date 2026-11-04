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
* E3-T2's real acceptance command **has** produced a measured attribute set. Accuracy
  (E3-T3: category ≥ 0.95, colour ≥ 0.90, pattern ≥ 0.85, material ≥ 0.80) is still
  **UNMEASURED**, blocked on E0-T1's 40 real photos / 20 hand masks — those are needed to score
  the accuracy, not to run the pipeline.
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
acceptance has produced a measured record. Two things remain:

1. **Legal sign-off on the OPEN items in ADR-006** before any seller photo is sent. The privacy
   policy places personal-data processing/storage in the PRC and the terms are PRC-governed;
   `prompts/08-legal.md` §7 needs a subprocessor table with region. This is the one place where
   "cheapest model" is not the only axis — do not treat it as settled.
2. **E3-T3 accuracy report** is still UNMEASURED — it needs E0-T1's 40 real photos / 20 hand
   masks as the golden set. The pipeline runs; the score does not exist yet.

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
