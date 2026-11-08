# ADR-010 — mask provenance: `ai-assisted:<model>+point-reviewed`

**Date:** 2026-11-08 · **Status:** accepted (Bert's five rulings) · **Supersedes:** the
`mask_source`-prefix-only interpretation of E0-T1 / E1-T4 · **Does not supersede:** the
`hand` prefix, the E0-T1 "zero synthetic placeholders" rule, or the copyright freeze.

---

## 0. Read this first — this ADR does NOT unblock anything

> **U1 is still UNMET. E1-T4 is still blocked. E2 must not start. CI's `gates_report` is
> still a non-blocking report step.**

Bert's ruling 1 says the 20 existing masks are **not grandfathered**: they must be re-made
under the new rules and the review must be evidenced. This change **does not re-make the 20
masks, does not touch any binary in the copyright-frozen zone, and does not change any value
in `manifest.json`**. The register ships with 20 `pending` slots, `0/20` point-reviewed, and
the edit-distance threshold `UNSET`. Changing a contract is not the same as producing the
evidence the contract asks for.

Consequently the existing caveats stay, word for word, in the machine tier:
`hand-mask gate stays open`, `not the E1-T4 DoD number`, `pipeline check`, `not a PASS`.
`infra/ci/gates/baseline.json`'s self-referential warning sentence is unchanged, and the
existing `0.9299` stays permanently labelled a pipeline check, not release evidence.
`baseline.json`'s `metrics` remains `{}`; `regression_gate.py` still exits `2` (INCONCLUSIVE).

The **reason** a report says `not a PASS` today is the *present-tense register state*
(`0/20` reviewed, threshold `UNSET`), not the mask-source tier. ADR-010 deliberately does not
pre-adjudicate the future state: the IoU semantics after the 20 masks are re-made belong to a
follow-up ticket. The `**PASS**` / `**BELOW**` branch in `seg_iou.py` remains reachable.

---

## 1. Context

E0-T1 required 40 real photos and 20 hand-drawn masks. The 20 masks that exist on this machine
were produced by 18 `algorithmic:matting` runs and 2 `ai:google-ai-mode-nano-banana` runs, and
were checked only by looking at a red overlay (`PHOTO_QC.md:20`,
`mask_quality: ok (visually checked as red overlay)`). Four consumers independently classified
provenance with `source.strip().lower().startswith("hand")`: `evals/run.py`,
`evals/tools/seg_iou.py`, `evals/tools/validate_golden.py`, and
`infra/ci/gates/regression_gate.py`. A vocabulary change in one of them would let the harness
row, the segmentation report, the validator tally and the baseline guard disagree about the
same mask.

The relaxed DoD accepts an AI-generated **proposal** mask followed by a human **point-by-point
review**, provided the review is evidenced in a structured register. It does not accept an
AI mask with no review, and it does not accept a self-declared string as proof of a review.

---

## 2. Bert's five rulings (input; not re-argued here)

1. **No grandfathering.** The 20 existing masks (18 `algorithmic:matting` + 2
   `ai:google-ai-mode-nano-banana`, red-overlay visual check only) are **not** retroactively
   accepted; their old values are void. This change does not re-make them, does not touch the
   frozen binaries and does not edit `manifest.json` values. A separate ticket owns the re-make.
2. **"Human point-by-point review" is a two-condition check.** "人工逐点复核" means (a) a
   committable structured review register, (b) the proposal model **and its full version**,
   and (c) a pixel edit-distance threshold between the reviewed mask and the proposal mask.
   The decision is upgraded from "look at the prefix" to **source field + evidence record**.
   **The numeric threshold stays `UNSET` / `null` in this change:** it needs the measured
   distribution from the 20 re-made masks; writing a number now would be fabricating data.
   Until it is decided, `u1_met` **must be False even if all 20 records are `reviewed`**.
3. **A new, separate prefix:** `ai-assisted:<model>+point-reviewed`. The `hand` prefix keeps
   its meaning; reports keep **three** tiers (pure hand / AI-assisted reviewed / machine).
   Labelling an AI-assisted mask with a `hand` prefix is forbidden and is a hard conflict.
4. **`baseline.json`'s self-referential warning sentence is not changed by one character.**
   The existing `0.9299` is permanently a pipeline check, not release evidence.
5. **No future state is hard-coded.** `not a PASS` follows from the current `0/20 reviewed`
   register, not from the tier. The machine tier's verdict sentence is unchanged; the
   AI-assisted and hand tiers get **neutral** wording: ADR-010 writes neither a permanent
   exclusion of those tiers nor a promise that they will one day pass. `seg_iou.py`'s `else`
   branch stays reachable. Future IoU semantics are a separate ticket; ADR-010 does not
   pre-adjudicate them.

### Other settled questions

* **Q1** — the edit-distance threshold stays `UNSET` / `null`; it needs the measured
  distribution of the 20 re-made masks. Until it is decided, `u1_met` must be False even if
  all 20 rows are `reviewed`. See §5, Threshold.
* **Q6** — this change does not re-make the 20 masks; that is another ticket; U1 is always
  UNMET in this state.
* **Q9** — the validator is **not** wired into any CI step (`ci.yml`, `infra/ci/steps.sh` are
  untouched, and no `dirty_check` whitelist line was added). `exit 0` means only "the register
  is structurally valid"; U1's last step is a **human sign-off**.
* **Q11** — no `manifest.json` value is changed.
* **Q12** — see §9, Allowed-paths exception.

---

## 3. D2 — edit-distance recomputability (a hard contradiction, NOT solved)

Bert ruling 2 makes "human point review" verifiable only through a structured review record
plus a **pixel edit-distance** between the reviewed mask and the **AI-proposed mask**. That
comparison needs the proposal mask's **pixels** as the baseline. On this machine those pixels
exist at `evals/golden/masks_draft/<id>.png` (the manifest's `mask_draft` field points at them),
but `.git/info/exclude:62` excludes `/evals/golden/masks_draft/` as a whole: the files are
binary, untracked and absent from a clean clone. Committing the pixels — or any pixel-equivalent
artefact (RLE, base64 bitmap, downsampled array) — into the public repo is ruled out by N2.

**Therefore the CI side can never independently recompute the edit distance.** This is a hard
contradiction between "a mechanically decidable DoD" and "copyright-frozen reference data";
this ADR does not pretend to solve it. Three options, with what each can and cannot decide:

| option | can decide | cannot decide | status |
|---|---|---|---|
| **B1 declarative summary** (proposal/reviewed sha256, changed_pixels, changed_ratio, model + version, review record) | schema completeness, 20-slot reconciliation, hash format, value <= threshold, review fields, tier<->evidence consistency | whether the hashes really correspond to the on-disk masks; whether the pixel XOR was really computed; whether a human really reviewed | **adopted, CI default** |
| **B2 local recomputation** (`--verify-local-masks`, reads `masks_draft/` and `masks/`, binarise@128, XOR) | that the two local binaries do produce the registered hashes and edit distance | runs on CI / a clean clone (files absent); self-referential tampering of mask + register together | adopted as **local, human-run only**; never in CI, never in `make golden` |
| **B3 commit pixel-equivalent artefacts** | CI could recompute | violates N2 / copyright / repo size | **rejected** |

**Honest residual:** B1 + B2 still cannot cryptographically prove on CI that a human point
review took place. What is provable is only that a structurally valid, self-consistent, 20-slot
register was signed by a named reviewer at a stated UTC time and committed. That is **social
evidence, not cryptographic evidence**, and U1's last step remains a human signature. The
edit-distance threshold itself stays `UNSET` until a follow-up ticket derives it from the 20
re-made masks' measured distribution — see the Threshold paragraph in §5; that is Q1, not D2.

### 3.1 Related but distinct — do not conflate these with D2

* **D1's residual is an index reconciliation problem, not a pixel problem.** CI cannot
  independently verify that the committable `expected_ids` index matches the local untracked
  `manifest.json`; that reconciliation is the local read-only `--cross-check-manifest` step.
  D2 is about a **pixel-level recomputation baseline** being unavailable. Two independent
  residuals.
* **Bert ruling 2's motivation (not D2).** A provenance *string* can say `+point-reviewed`,
  and a string is free to write and free to forge. If the relaxed DoD were implemented as
  "does `mask_source` match a nicer pattern?", the new tier would be a self-certifying label —
  exactly the failure the original sceptical `hand`-prefix default was written to prevent.
  That is why the decision is upgraded to **source field + evidence record**, with the
  tier <-> `proposal.model` conflict detection in `validate_point_reviews.py`. It explains the
  two-condition check; it is not the D2 contradiction.
* **Q1 — threshold `UNSET` (decided, not D2).** No honest number exists before the 20 masks
  have been re-made and reviewed. `u1_met = (reviewed == slots == 20) and threshold_status !=
  "UNSET" and all_checks_ok`, so even 20 `reviewed` rows leave U1 UNMET. No number is invented.
* **U1 UNMET is a conclusion, not D2.** It follows from Bert ruling 1 (no grandfathering,
  re-make + evidence), Q1's `UNSET` threshold, and the register's 20 `pending` slots. In two
  layers: `evals/mask_provenance.py` classifies the source field,
  `evals/tools/validate_point_reviews.py` owns the evidence record, and report renderers
  receive only an immutable `ReviewState` summary — they never read the register and never
  judge evidence. No exit code closes U1; the last step is a human sign-off.

This ADR does not claim that satisfying those conditions will or will not turn an IoU number
into release evidence — that is the future ticket.

---

## 4. Decision — vocabulary and single source of truth

`evals/mask_provenance.py` is the single source of truth. All four consumers import from it.

| tier | example | `classify()` result |
|---|---|---|
| pure hand | `hand`, `hand-drawn`, `hand:photoshop` | `MaskSourceTier.HAND` |
| AI-assisted, point-reviewed | `ai-assisted:sam2+point-reviewed` | `MaskSourceTier.AI_ASSISTED_REVIEWED` |
| machine | `algorithmic:matting`, `ai:nano-banana`, `provisional`, `unspecified` | `MaskSourceTier.MACHINE` |

* `HAND_PREFIX_RE = ^hand` (IGNORECASE) — unchanged semantics.
* `AI_ASSISTED_RE = ^ai-assisted:(?P<model>[a-z0-9_-]+)\+point-reviewed$` (IGNORECASE) —
  full match only. A bare `ai-assisted:sam2` is MACHINE.
* **The slug forbids `.`, `;`, `,` and `:`.** `regression_gate.py:76`'s
  `_MASK_SOURCE_RE = r"mask source:\s*([^.;]+)"` truncates a provenance note at `.` and `;`,
  and `mask_sources()` splits the captured text on `,`; `:` is reserved by the prefix grammar.
  Any of them corrupts the coverage check. Full version strings such as `2.1` live in
  `proposal.model_version`, never in the prefix.
* `MACHINE_REFERENCE_MARKERS` keeps **both** `ai:` and `ai-assisted:`. The old `ai:*` values
  are still in the frozen manifest, and `ai-assisted:sam2+point-reviewed` does **not** contain
  `ai:` (the next character is `-`). Dropping the new marker would let that value slip past the
  `--update-baseline` guard — the D3 regression pinned by a test.
* **Sceptical default preserved:** missing / empty / unknown is MACHINE with `is_default=True`.
  `row.get("mask_source", "unspecified")` keeps its shape.

---

## 5. Evidence register — `evals/golden-review/point_review_register.json`

20 slots, one per MASK slot in `evals/golden/SOURCING.md:28`, all `pending`. No binaries: the
directory ships in git so a clean clone can mechanically judge register completeness.

A `reviewed` row must carry: `provenance` fully matching `AI_ASSISTED_RE`, `proposal.model` and
`proposal.model_version`, `proposal.sha256` and `reviewed.sha256` (64 lowercase hex), an
`edit_distance` object (`method`, `changed_pixels`, `changed_ratio`) and a `review` object
(`review_id`, `reviewer`, `reviewed_at` in ISO 8601 UTC, `points_reviewed`). A `pending` row
must leave all evidence fields `null` — no half-filled rows. A non-empty `proposal.model`
together with a `hand`-prefixed provenance is a hard conflict
(`CONFLICT: AI proposal recorded but mask_source starts with "hand"`).

**N2 — pixel-equivalent artefacts are forbidden as evidence.** A re-encoded copy with
identical pixels, a hash of a duplicate, a size-only comparison, or the old red-overlay visual
check does not constitute a point review. `PHOTO_QC.md:20`'s red-overlay inspection and
`mask_quality: ok (visually checked as red overlay)` **no longer count as review evidence**.
The register exists precisely because a screenshot cannot be audited.

### Field semantics for B2 (definitions, not a design change)

`--verify-local-masks` is B2 and recomputes three independent facts from the two local PNGs:

* `edit_distance.method = "xor@128"` and
  `changed_pixels = count_nonzero((proposal >= 128) XOR (reviewed >= 128))`. Written as **XOR**,
  not as an IoU `union - intersection` expression, even though the numbers coincide on binary
  masks — the edit distance and IoU are different metrics and must not be conflated.
* `changed_ratio = changed_pixels / (H * W)`. The denominator is the **whole frame**, not the
  union and not the garment region. B2 compares it with a four-decimal rounding tolerance.
* `proposal.sha256` and `reviewed.sha256` are
  `hashlib.sha256(Path(mask_path).read_bytes()).hexdigest()` — the **raw PNG file bytes**, never
  a binarised ndarray. The hash proves file identity; the XOR proves how many pixels changed.
  Re-encoding a PNG changes its bytes without changing its pixels, so the hash is
  not a pixel-equivalence judgement. Array hashing would also need a dtype / memory-order /
  C-contiguity convention nobody agreed on.
* A shape mismatch is a failing check naming the entry id and both sizes (the
  `seg_iou.run()` rule). B2 never silently resizes.

`expected_ids` is a committable CI-side substitute index. **D1 residual:** CI can mechanically
judge register completeness, but it **cannot independently verify that `expected_ids` matches
the local `manifest.json`** (a clean clone has no manifest). That reconciliation is the local
read-only `--cross-check-manifest` plus PR human review. **D2 residual (distinct):** CI also
cannot recompute the pixel edit distance, because the proposal mask's pixels live in the
excluded `masks_draft/` directory — see §3. Do not conflate the index-reconciliation residual
with the pixel-recomputation contradiction.

### Threshold (Q1, decided — not D2)

`edit_distance_threshold.min_changed_pixels`, `max_changed_pixels` and `max_changed_ratio` are
all `null` and `value_status` is `UNSET`. The number can only be derived from the measured
distribution of the 20 re-made masks, which does not exist yet; writing one now would fabricate
data. While the threshold is `UNSET`, `u1_met` is False even with 20 `reviewed` rows, and the
validator judges structure only. The decision belongs to a follow-up ticket.

---

## 6. Validator — two modes, and what `exit 0` means

| mode | reads | result |
|---|---|---|
| default (CI / clean clone) | register only | register self-consistency + `expected_ids` ↔ 20 `records`; **never opens `manifest.json`** |
| `--cross-check-manifest [PATH]` | + local manifest (read-only text) | reconcile the 20 `mask_path != null` ids; absent manifest prints `manifest absent (clean clone) — cross-check skipped` and is **not** a FAIL |
| `--verify-local-masks [DIR]` | + local PNGs | optional XOR@128 for `reviewed` rows; Pillow imported lazily, only here |

Exit `0` = structurally valid and prints `U1 UNMET`; `1` = broken/conflicting register or a
failed reconciliation; `2` = register missing or invalid JSON. **`0` is not a U1 verdict.**
No CI step was added; the validator is not wired into `ci.yml` or `infra/ci/steps.sh`.

---

## 7. Reporting — three tiers, neutral future wording

`seg_iou.render()` takes one new optional keyword argument `review_state: ReviewState | None`.
When omitted it uses the sceptical default `ReviewState(0, 20, False, "UNSET")`; the existing
five-positional-argument call in `test_seg_iou_null_mask.py` is unchanged. The renderer reads
only that immutable summary — no register, no evidence judgement (layering).

`if not review_state.u1_met:` first prints the register-state line that grounds `not a PASS`
in the present-tense `0/20 reviewed` fact, then emits the tier verdict. The machine-tier
sentence is **byte-for-byte unchanged**; the AI-assisted and hand tiers use neutral wording;
`elif machine_sources:` keeps the old prose; and the `else:` branch keeps
`outcome = "PASS" if stats["mean"] >= threshold else "BELOW"` **reachable**, with one appended
sentence pointing at the follow-up ticket. `evals/run.py`'s note keeps `mask source: <list>`
for `_MASK_SOURCE_RE`, and its all-machine caveat is unchanged.

`validate_golden.py` reports a three-tier tally under the **same** check name
`mask provenance (reported, not gated)`; it still does not gate provenance and `make golden`
still exits `0`. The `"1"`-mode / pixel-value-set gap at `:409` is deliberately **not** fixed
here (separate ticket).

---

## 8. Three-layer rollback

1. **Text only.** Revert this ADR, the register and its README. No binary is touched, so the
   frozen golden set is byte-identical.
2. **Vocabulary.** `evals/mask_provenance.py` holds every prefix, regex and marker. Reverting
   that one module restores old behaviour in all four consumers simultaneously.
3. **Commit.** This change is a single commit on its own branch. Reverting it requires no
   restoration of photos, masks or `manifest.json`, and no CI wiring to undo.

---

## 9. Allowed-paths exception

E0-T5 (`docs/QA.md:28`) declares "This ticket delivers exactly 18 files" and lists
`docs/PLAN.md`, `Makefile`, `evals/run.py`, `pyproject.toml` under "NOT touched (by E0-T5
itself)": that is a **ticket-scoped scope statement, not a permanent global ban**. Bert's
ADR-010 ruling explicitly overrides it for this change, in the same spirit as the exception
recorded in `docs/QA.md` §15.8.

**Changed (tracked text only):**

* Added: `evals/mask_provenance.py`, `evals/golden-review/point_review_register.json`,
  `evals/golden-review/README.md`, `evals/tools/validate_point_reviews.py`,
  `docs/ADR-010-mask-provenance-ai-assisted-point-reviewed.md`,
  `evals/tests/test_mask_provenance.py`, `evals/tests/test_point_review_register.py`.
* Modified: `evals/run.py`, `evals/tools/seg_iou.py`, `evals/tools/validate_golden.py`,
  `infra/ci/gates/regression_gate.py`, `infra/ci/gates/baseline.json`,
  `infra/ci/gates/README.md`, `infra/ci/gates/tests/test_regression_gate.py`,
  `docs/PLAN.md`, `docs/QA.md`, `evals/golden/README.md`, `evals/golden/SOURCING.md`,
  `docs/HANDOFF-lead.md`, `docs/HANDOFF-vision.md`, `docs/HANDOFF-engineer.md`.

**NOT touched:** `evals/golden/photos/**`, `evals/golden/masks/**`,
`evals/golden/masks_draft/**` (any binary), `evals/golden/manifest.json` (values and
commit state), `evals/golden/PHOTO_QC.md`, `SHOT_LIST.md`, `GOOGLE_SEARCH_LOG.md`,
`MASK_PROMPTS.md`, `evals/reports/seglab.md`, `vision.md`, `e1t3_matte.md`,
`baseline.json`'s `metrics`, `validate_golden.py:409`, `.github/workflows/ci.yml`,
`infra/ci/steps.sh` (no `dirty_check` whitelist line was added), `pyproject.toml`,
`Makefile`, `.env`, and `.git/info/exclude` itself.

This change is a single local commit on `feat/mask-provenance-adr-010`. It has **not** been
pushed, no PR has been opened and no CI status check has run yet: the required PR + CI status
check is a **pre-merge condition**, not a completed step, and belongs to the lead. No file under
`evals/golden/**` (beyond the five tracked scaffolding files) was staged, committed or pushed,
and the `.git/info/exclude` rules themselves were not modified. `gates_report` remains a
non-blocking report step; this ADR does not upgrade it.

---

## 10. Explicitly NOT decided here

* The numeric edit-distance threshold (UNSET; needs the 20 re-made masks' distribution).
* Whether a fully registered set's IoU can ever be release evidence — **future ticket**.
* Re-making the 20 masks, or changing any `manifest.json` value.
* Any new IoU number, any `cost.md`, any `baseline.json` metrics, any CI step or blocking
  gate, and the `"1"`-mode validator gap.
* E2's precondition: **still not lifted**.
