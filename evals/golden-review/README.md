# evals/golden-review — ADR-010 point-review register

**This directory does NOT unblock U1, E1-T4 or E2.** All 20 slots are `pending`,
`0/20` are point-reviewed, and the edit-distance threshold is `UNSET`. Bert's ruling 1 says
the 20 existing masks are **not grandfathered**: they must be re-made under the new rules and
the review must be evidenced here. This ticket does not re-make them, does not touch
`evals/golden/photos/**`, `masks/**`, `masks_draft/**` or any value in `manifest.json`.
So `U1` remains **UNMET**, the E1-T4 hand-mask gate stays open, E2 must not start, and CI's
`gates_report` remains a non-blocking report step.

Everything in this directory is **text and is committed**. The photos and masks are not:
the repo is public and those files are all-rights-reserved. A clean clone therefore has this
register and no golden binaries; the default validator command must still work there.

## What the two-condition check means

ADR-010 replaces "does `mask_source` start with `hand`?" with **both** of:

1. the source field classifies into one of the three tiers in
   `evals/mask_provenance.py` — a `hand` prefix, an
   `ai-assisted:<model>+point-reviewed` value, or machine-made; and
2. this register carries the evidence record: the proposal model and version, the proposal
   and reviewed mask paths and their sha256 digests, the pixel edit distance between them,
   and the review record (`review_id`, `reviewer`, `reviewed_at`, `points_reviewed`).

A source string alone never certifies a review. In particular
`ai-assisted:sam2+point-reviewed` with an empty register is still **not** evidence — it is
also matched by the baseline guard's `ai-assisted:` marker so `--update-baseline` refuses it.

The threshold is deliberately `UNSET` (`edit_distance_threshold.*` are all `null`). Bert's
ruling 2 requires the measured distribution from the 20 re-made masks before a number can be
chosen; writing one now would be inventing data. While it is `UNSET`, `u1_met` is **False even
if all 20 records are marked `reviewed`**. `python -m evals.tools.validate_point_reviews`
returning `0` means only "the register is structurally valid" — the last step of U1 is a
**human sign-off**, not an exit code.

## Schema (schema_version 1)

Top level:

| key | rule |
|---|---|
| `schema_version` | integer `1` |
| `policy` | `"ADR-010"` |
| `expected_ids` | exactly 20 unique lowercase ids, the 20 MASK slots from `evals/golden/SOURCING.md:28` |
| `expected_ids_source` | explains that this is a committable CI-side index; CI cannot independently verify it against the local `manifest.json` |
| `edit_distance_threshold` | `method: "xor@128"`, three numeric bounds (`null` = UNSET), `decided_by`, `value_status` |
| `records` | exactly 20 objects, `id` order equal to `expected_ids` |
| `notes` | free text, including the limitations above |

One record:

```json
{
  "id": "top-01",
  "status": "pending",
  "provenance": null,
  "proposal": {"model": null, "model_version": null,
               "mask_path": "masks_draft/top-01.png", "sha256": null},
  "reviewed": {"mask_path": "masks/top-01.png", "sha256": null},
  "edit_distance": null,
  "review": null
}
```

* `status` is `pending` or `reviewed`.
* **`pending` means every evidence field is `null`** — no half-filled rows. `proposal.mask_path`
  and `reviewed.mask_path` are the two *declared* relative paths and stay populated; they are
  pointers, not evidence.
* `reviewed` requires: `provenance` fully matching
  `^ai-assisted:(?P<model>[a-z0-9_-]+)\+point-reviewed$`; non-empty `proposal.model` and
  `proposal.model_version`; both `sha256` values as 64 lowercase hex characters; an
  `edit_distance` object with `changed_pixels`, `changed_ratio` and `method`; and a `review`
  object with `review_id`, `reviewer`, `reviewed_at` (ISO 8601 UTC) and `points_reviewed`.
* **Hard conflict:** a non-empty `proposal.model` together with a `hand`-prefixed provenance is
  rejected (`CONFLICT: AI proposal recorded but mask_source starts with "hand"`). An
  AI-assisted tier without a `review` object is rejected too.
* **Field semantics (B2 recomputation, clarified by the architect — definitions, not a design
  change).** These are the meanings `--verify-local-masks` recomputes and compares:
  * `edit_distance.method` is `xor@128`; `changed_pixels = count_nonzero((proposal >= 128) XOR
    (reviewed >= 128))`. It is written as **XOR**, not as an IoU `union - intersection`
    expression, even though on binary masks the numbers coincide — the two metrics must not be
    conflated.
  * `changed_ratio = changed_pixels / (H * W)`, where the denominator is the **whole frame**,
    not the union and not the garment region. B2 tolerates four-decimal rounding
    (`1e-4`) when comparing against the recorded value.
  * Both `sha256` values are `hashlib.sha256(Path(mask_path).read_bytes()).hexdigest()` — the
    hash of the **raw PNG file bytes**, never of a binarised ndarray (array hashes would need a
    dtype / memory-order / C-contiguity convention nobody agreed on).
  * **File hash and pixel difference are independent facts.** The hash proves "it is the same
    file"; the XOR proves "this many pixels changed". Re-encoding a PNG changes its bytes
    without changing its pixels, so the hash is not a pixel-equivalence judgement — an
    honest limitation, not something B2 hides.
  * A shape mismatch is reported as a failing check naming the entry id and both sizes (the
    `seg_iou.run()` rule). B2 never silently resizes a mask.
* The `<model>` slug forbids `.`, `;`, `,` and `:`. `regression_gate.py:_MASK_SOURCE_RE`
  (`r"mask source:\s*([^.;]+)"`) truncates at `.` and `;`, and `mask_sources()` splits the
  capture on `,`; `:` is reserved by the prefix grammar. Any of them would corrupt the
  coverage check. Full version strings such as `2.1` go in `proposal.model_version`.

## Reconciliation rules

| command | reads | meaning |
|---|---|---|
| `python -m evals.tools.validate_point_reviews` | register only | register self-consistency + `expected_ids` ↔ 20 `records` — **the CI / clean-clone mode** |
| `python -m evals.tools.validate_point_reviews --cross-check-manifest [PATH]` | register + local `evals/golden/manifest.json` (read-only text) | reconcile `expected_ids` with the 20 entries whose `mask_path != null` |
| `python -m evals.tools.validate_point_reviews --verify-local-masks [DIR]` | register + local proposal/reviewed PNGs (PIL, lazy import) | optional local pixel check for `reviewed` rows |

**CI never reads `manifest.json`** — a clean clone does not have it. When the optional
cross-check is requested and the manifest is absent, the validator prints
`manifest absent (clean clone) — cross-check skipped` and does **not** fail. The residual is
explicit: CI can mechanically judge register completeness, but it cannot independently verify
that the committable `expected_ids` index matches the local manifest. That reconciliation is
the local `--cross-check-manifest` plus PR human review.

Exit codes: `0` structurally valid (and prints `U1 UNMET` in the current state),
`1` broken/conflicting register or a failed reconciliation, `2` register missing or invalid
JSON.

## SOP for a future ticket that owns the 20 masks

1. Re-make each of the 20 masks as an AI proposal followed by a human point-by-point review.
   Record the proposal model and its **full version string**, and keep the proposal PNG.
2. Compute `xor@128`: binarise both masks at 128, XOR them, count changed pixels and the
   changed ratio. Store the three `edit_distance` values.
3. Fill sha256 for both files, the `review` record, and set `provenance` to
   `ai-assisted:<slug>+point-reviewed` (slug: `[a-z0-9_-]+` only).
4. Only after all 20 rows are evidenced, measure the edit-distance distribution and have the
   threshold **decided and signed off**. Until then `u1_met` stays False.
5. Run the local cross-check and pixel verification, then obtain the **human sign-off** that
   closes U1. ADR-010 does not pre-adjudicate what the IoU verdict means after that; the
   future-state semantics belong to a follow-up ticket.

## Companion tool (mechanical help only)

`python -m evals.tools.mask_review_kit` removes the arithmetic from steps 2–3 of the SOP above.
It is a **local, human-run helper**: not a CI gate, not part of `make golden`, and not wired
into `ci.yml` or `steps.sh`. It reads the copyright-frozen PNGs but never copies, stages or
commits any binary.

```bash
# Read-only: hashes of both raw PNGs, xor@128 changed_pixels / changed_ratio, format info
python -m evals.tools.mask_review_kit measure --id top-01
python -m evals.tools.mask_review_kit measure --all

# Read-only batch format check: L mode, photo size, 20 ids reconciled, files present
python -m evals.tools.mask_review_kit check
python -m evals.tools.mask_review_kit --strict-binary check   # opt-in: require only {0,255}

# Build a reviewed record (prints it; add --write to update the register)
python -m evals.tools.mask_review_kit record --id top-01 \
    --model sam2 --model-version 2.1 --reviewer bert \
    --review-id PR-010-top-01 --points-reviewed 412 \
    --proposal-source ai-assisted:sam2+point-reviewed --attest [--write]
```

**What the tool deliberately will not do** (ADR-010 §6): `--attest` is a human's explicit
statement that they reviewed that mask point by point; `reviewer`, `review_id` and
`points_reviewed` are required arguments the tool never infers. Without `--attest` the record
stays `pending` and the tool writes nothing. It never sets the edit-distance threshold, never
sets `u1_met`, never produces an IoU number, and never changes `manifest.json`. While the
threshold is `UNSET`, 20 `reviewed` rows still leave U1 UNMET by construction — the last step
of U1 is a **human sign-off**, not an exit code. `--proposal-source` is a required human
declaration of the proposal's actual provenance; the tool refuses machine-tier proposals
(`algorithmic:floodfill-border`, historical `ai:*`) rather than relabelling them as
`ai-assisted:<slug>+point-reviewed` (ADR-010 N2).

`check` is stricter than `validate_golden.py` on **mode** (`L` only; the validator also accepts
`"1"`), but not on pixel values: `evals/golden/README.md` allows soft edges and B2 binarises
at 128, so anti-aliased greys are **INFO by default**. `--strict-binary` is the opt-in for a
strictly `{0,255}` mask. On a clean clone the binaries are absent; missing files are reported,
never fabricated. `exit 0` means only "the mechanical checks passed on the files present".

## Limitations (deliberately not solved here)

* **N2 — pixel-equivalent artefacts are forbidden as evidence.** A re-encoded copy with the
  same pixels, a size-only comparison, a hash of a duplicate, or the old red-overlay visual
  check (`PHOTO_QC.md:20`, `mask_quality: ok (visually checked as red overlay)`) is **not** a
  point review. The register exists because a screenshot cannot be audited.
* **D2 — edit-distance recomputability (a hard contradiction, not solved here).** The pixel
  edit distance needs the AI-proposed mask's pixels as its baseline. Those live at
  `evals/golden/masks_draft/<id>.png` (the manifest's `mask_draft`), which
  `.git/info/exclude` excludes as a whole: binary, untracked, absent from a clean clone.
  Committing the pixels or any pixel-equivalent (RLE, base64 bitmap, downsampled array) is
  ruled out by N2, so **CI can never independently recompute the edit distance**. B1
  (declarative summary, the CI default) and B2 (`--verify-local-masks`, local/human-run only)
  cannot cryptographically prove a human review; the register is social evidence with a named
  reviewer and a stated UTC time. See ADR-010 §3. This is distinct from the **D1** residual:
  CI cannot independently verify the committable `expected_ids` index against the local
  `manifest.json` (that is the local `--cross-check-manifest` step).
* The validator judges structure. It does not watch a human review points, it does not decide
  the threshold, and it does not produce or back-fill any IoU number. B2's XOR@128 uses the
  same binarisation threshold as `seg_iou._iou()`; it reads only the two declared paths, is
  never wired into CI or `make golden`, and never claims CI-side recomputation.
* `validate_golden.py`'s `"1"`-mode / pixel-value-set gap is unchanged (separate ticket).
* No CI step was added for this validator (`ci.yml`, `infra/ci/steps.sh`, `dirty_check`
  whitelist untouched); a committed file disappears from `git status`, so a whitelist entry
  would be meaningless.

## Rollback (three layers)

1. **Text only** — revert this directory and the ADR; no binary is touched, so the frozen
   golden set is byte-identical.
2. **Vocabulary** — `evals/mask_provenance.py` holds every prefix, regex and marker. Reverting
   that one module restores the old two-tier behaviour in all four consumers at once.
3. **Commit** — this change is a single commit on its own branch; reverting it does not
   require restoring photos, masks or `manifest.json`.
