# ADR-011 — what `rectify SSIM (inside mask)` measures

**Date:** 2026-11-12 · **Status:** `Proposed — pending Bert's review` · **Ticket:** E2-T1 ·
**Supersedes:** nothing · **Changes:** the *meaning* of one `docs/PLAN.md` DoD sentence, not any
threshold, not any binary, not any test that is currently green.

> This ADR is a proposal. Nothing here is adjudicated. Until Bert rules, the harness row
> `rectify SSIM (inside mask)` stays `UNMEASURED` and **E2-T1's DoD is UNMET** — that is a
> statement about a missing definition, not about the implementation, which is finished and
> passes its own 1e-9 geometry tests.

---

## 0. The one-paragraph version

`rectify()` is a geometric transform, so "SSIM ≥ 0.85 inside the mask **versus the source**" has
no literal reading: the output and the source are in different coordinate systems and different
frames. Every reading of it that *does* typecheck is either meaningless (compare across a
homography) or a tautology (compare the stage against the same homography it declared). So this
ADR **splits the DoD sentence into three facts that are each independently checkable and none of
which is self-referential**, keeps the row name and the `0.85` threshold, and states exactly
which defect each fact can and cannot see. The number that ships is still
`rectify SSIM (inside mask)`; the sentence beside it changes from "versus the source" to
"the output against an independently re-rendered reference under the homography **the stage
itself declared**", plus two exact conformance checks that pin the declared homography so the
SSIM half cannot be satisfied by a wrong one.

---

## 1. Context

### 1.1 What exists

| thing | state |
|---|---|
| `packages/vision/rectify.py` (472 lines) | **implemented** (`d0d97fe`). `rectify(image, alpha_png, category) -> RectifyResult(flat_png, homography, meta)`. Homography from the alpha bounding box (v1), inverse-mapped canvas, numpy bilinear, composite on `#F2F1EE`, zero network. |
| `evals/run.py:75` | `Target("vision", "rectify SSIM (inside mask)", ">= 0.85", "E2")` exists. No `_measure_rectify` exists. The row prints `UNMEASURED` forever. |
| `evals/metrics/rectify.py` | does not exist |
| definition of the metric, anywhere in the repo | **does not exist.** `grep -rn "rectify SSIM"` hits the target line and `UNMEASURED` tables only. |
| `docs/HANDOFF-vision.md:737-738` | says the SSIM "is measured by `prompts/02-vision.md`, and wiring it is not this ticket's file list". The first clause is **false** — `prompts/02-vision.md:24-25` carries only the same one-liner as PLAN, no formula, no mask, no coordinate system. |
| fixture `evals/fixtures/bottom-03.jpg` | **never existed** (`git log --all` empty). The acceptance command cannot run. The bytes do exist at `evals/golden-provisional/photos/bottom-03.jpg:337 KB` with mask `masks/bottom-03.png` — only the ticket's path prefix is wrong. Same for `top-01.jpg` / `dress-02.jpg` / `top-04.jpg`. |
| `evals/metrics/prettify.py` | the precedent. Its SSIM is output-vs-own-input and its docstring calls itself a **destruction budget**: 1.0 = nothing changed, so the `>= 0.80` floor is trivially satisfied and proves only "nothing was destroyed". Measured 0.9884–0.9938. |

### 1.2 Why this cannot be settled by writing the obvious code

Three candidate readings, all of which look reasonable and all of which are broken:

**(a) Output vs source, pixelwise.** The source is `Hp×Wp`, the output is `960×1200`. There is
no correspondence to index. Resizing one to the other measures the resize, not the stage.

**(b) Output vs "the source warped by the stage's own homography".** This typechecks, is what an
implementer will write by reflex, and is **≈ 1.0 for every implementation that is internally
consistent — including a wrong one.** If the homography is the identity, the reference is built
with the identity too and they agree. It is the function compared with itself. `test_prettify_
metric.py`'s docstring records QA doing exactly this mutation to the prettify metric once and the
report printing `1.0000` with 449 tests green. Reading (b) is that bug, pre-installed.

**(c) Inverse-warp the output back into the photo frame, then compare.** Two resamplings, still
≈ 1.0 for the same reason, plus a second chance to hide an error in the interpolation.

So the ADR has to answer a harder question than "which numbers do we subtract": **what property
of a rectification stage is worth a DoD gate in the first place, and what is the cheapest
measurement that can actually lose points?**

### 1.3 The two properties that matter, and which one SSIM can see

| # | property | what it rules out | can SSIM see it? |
|---|---|---|---|
| P1 | **transport fidelity** — every garment pixel in the output came from that garment pixel in the photograph, through the declared map | generative repaint, inpainting, a fill, a colour profile shift, a wrong/naive resampler, an opaque composite that ate the backdrop, a broken alpha | **yes**, if the reference is rendered by code that is not the stage's code |
| P2 | **geometric conformance** — the stage's declared map is the documented map: garment bbox → canonical canvas, in the documented corner order, at the documented aspect | identity/scale/offset/flip/mirror homography, wrong canvas, wrong category aspect, inverted corner order, a quad inset by 10 % | **no.** SSIM is computed over correspondences the homography *defines*; an error that lives in the homography is in both images. |

The honest consequence, and the reason this ADR does not simply implement the sentence as
written:

> **SSIM cannot detect a geometric error that is consistently encoded in the declared
> homography.** Any design that reports only an SSIM number for a rectification stage is
> reporting a number that a wrong homography passes. That is the whole difficulty.

So the DoD is **two artefacts, not one number**: a pixel number (P1) and an exact conformance
check (P2). Neither is allowed to be derived from the other, and neither is allowed to be
computed by calling the code under test.

---

## 2. Decision

### 2.1 Symbols

| symbol | meaning |
|---|---|
| `P` | source photograph, RGB uint8, `Hp×Wp` |
| `A` | alpha matte, L uint8, same size as `P` |
| `c` | category string given to `rectify()` |
| `O` | stage output, RGB uint8, `w×h`, decoded from `RectifyResult.flat_png` |
| `H` | `RectifyResult.homography`, 9 floats row-major, `h33 = 1`, photo → canvas |
| `L(·)` | luminance `0.299R + 0.587G + 0.114B`, the same helper prettify uses |

### 2.2 Part A — the number on the row: `rectify SSIM (inside mask)`

**Frame:** the **canvas**. Both operands are on the `w×h` output grid. The output is never
resampled; it is compared where it already is.

**Reference:** `H` is inverted, normalised so `h33 = 1`, and reduced to PIL's 8 PERSPECTIVE
coefficients. Then

```
R  = P.transform(canvas, PERSPECTIVE, coeffs, BILINEAR)      # RGB
α  = A.transform(canvas, PERSPECTIVE, coeffs, BILINEAR) / 255  # float in [0,1]
Ref = R·α + backdrop·(1−α)                                    # backdrop = (242,241,238)
```

`Ref` is built by **Pillow's C resampler, not by numpy code and not by importing anything from
`packages/vision/rectify.py`.** That is the load-bearing decision of this ADR: it is what makes
Part A a *differential* measurement instead of a self-comparison. Pillow is already a dependency
(`docs/DEPS.md`, pillow 11.3.0); no new package, no `scikit-image`.

**The region — this is the "inside the mask" answer:**

```
SSIM  = ssim_map(L(O), L(Ref))                # 11×11 uniform window, C1=(0.01·255)², C2=(0.03·255)²
w     = α                                     # the warped alpha, continuous, no threshold
value = Σ(w·SSIM) / Σ(w)
```

Three deliberate choices inside that formula:

* **The mask is `α`, the alpha matte carried through the same declared map into the canvas
  frame** — not the source-frame alpha (which lives in the wrong frame) and not a re-derivation
  of the garment from the output (which would need a backdrop classifier and would let the stage
  define its own region).
* **Weighted, not thresholded, and no erosion.** An 11×11 SSIM window centred within 5 px of the
  silhouette necessarily mixes garment and backdrop, so a binary mask scores the compositing
  seam. A boolean mask also has two worse failures here: erosion to avoid the seam can **vanish
  entirely on a thin garment** (`logo-04-belt-stamp` is a belt, and the alpha-bbox quad stretches
  it across a 1200 px canvas, so its mask is a thin diagonal), and a hard threshold is a
  parameter with no derivation. Alpha weighting is threshold-free, cannot vanish, and
  down-weights the seam in proportion to how much of the pixel is actually garment.
* **`ssim_binary` is reported alongside** (mean over `α ≥ 128/255`), so the number stays
  comparable with prettify's binary-mask convention and so a large gap between the two is
  visible rather than hidden. The DoD gate uses the weighted value; the binary value is
  evidence.

**Guards.** `Σ(w) < 0.01·w·h` → this case is `UNMEASURED` with the reason named (a canvas that
is 99 % backdrop is not a measurement of anything). A `run()` that could not read a fixture
**raises**, it does not skip: a metric that quietly drops a case has a different denominator
from the one it reports (the rule `evals/metrics/prettify.py::run()` already states).

**What the number means, in one sentence, and it goes in the report next to it:**

> `1.0` = every garment pixel in the output is that garment pixel of the photograph, transported
> by the map the stage declared. Below `0.85` means more than 15 % of the garment's structure was
> **replaced or mis-transported**. This is a *transport budget*, not a correction score: it
> cannot tell a good quad from a bad quad, and it is blind to any error the declared homography
> itself carries.

### 2.3 Part B — the two exact conformance checks that give Part A teeth

Reported per case, `passes_dod = (Part A ≥ 0.85) and Part B1 and Part B2`.

**B1 — corner conformance (pins all 8 degrees of freedom of `H`).**

```
quad_ref = (left, top), (right, top), (right, bottom), (left, bottom)   # TL,TR,BR,BL of A > 0
```

`quad_ref` comes from **Pillow's `Image.getbbox()`** on `A`, cross-checked against
`np.nonzero(A > 0)` min/max (they must agree; a mismatch is a fixture anomaly and is named, not
smoothed). Then, in that order:

```
for i in (TL, TR, BR, BL):  H · quad_ref[i]  ==  target_rect[i]    within 0.5 px
det(H) > 0                                                          # a mirror is a bug, always
```

Why this is not circular: `target_rect` and the aspect table are **transcribed into the metric
from the spec** (`docs/PLAN.md` E2-T1 + `BRIEF.md`), not imported from `rectify`. Drift between
the transcription and the stage's table is caught by a dedicated test, so a future change fails
loudly in one place instead of silently redefining both sides.

Why 0.5 px is the right tolerance, and why it is still sharp: the solve is *exact* at the four
correspondences by construction (the geometry tests pin it to 1e-9), so the residual on a correct
implementation is float noise. 0.5 px is enormous slack for float noise and microscopic for the
failures the check exists to catch — a wrong corner order (off by the canvas dimension), a
transposed axis, an inset quad (off by the inset), the identity (off by the bbox offset and the
scale). The check is a *discriminator*, not a precision claim.

**B2 — canvas conformance.**

```
(w, h) == canonical_size(c)  computed from the transcribed aspect table (4/5, 4/5, 3/4, 4/5,
                             4/3, 1.0, 4/5) at long edge 1200, within 1 px per side
max(w, h) <= 1600                              # BRIEF.md literal
decoded mode == "RGB", 8-bit, no alpha band    # PNG magic bytes checked too
```

This is the only place the "sRGB PNG ≤ 1600 px" half of the DoD is actually asserted. Note what
it does **not** prove: `_encode_rgb` writes no ICC chunk, so this asserts "8-bit RGB, no alpha",
not "carries an sRGB profile". Say that in the note rather than letting the word sRGB imply a
colour-management claim nobody tested.

### 2.4 What the pair proves, and why the composition is the honest claim

```
Part A  ⇒  O ≈ warp_H(P)         (inside the garment, to within 0.85 SSIM)
Part B1 ⇒  H  = the documented map of the alpha bbox onto the canvas, corner order included
∴       O ≈ the documented warp of the photograph's garment onto the canonical canvas
```

That is exactly the DoD's intent, decomposed so each half is checkable without borrowing the
other's assumptions. Either half alone is worthless: A without B is the tautology of §1.2(b), B
without A is a geometry assertion about an image nobody looked at.

### 2.5 What it cannot prove — the list that must travel with the number

1. **Quad quality.** `H` is derived from the alpha's bounding box. If the mask is wrong, the
   bbox is wrong, the warp crops or stretches the garment, and **all three checks still pass** —
   Part A because the transport was faithful, Part B because `H` was the documented map of that
   bbox. "Is the bbox a good garment quad" is not measured here and is not measurable by this
   design; it is what `meta.notes["quad_source"] = "alpha_bbox"` is admitting and what the
   corner-detector ticket has to answer. The 9 fixtures' alphas are **machine-made**
   (`algorithmic:background_colour_distance`), so this is not a small caveat.
2. **Absolute correctness.** Everything is relative to the declared map. There is no ground-truth
   quad on disk for any of the 9 photos. `alpha_bbox` is a *convention*, and this metric verifies
   the convention was followed, not that the convention is right.
3. **The rim.** α-weighting suppresses but does not remove the silhouette seam, and nothing
   measured here covers the 2 px composite edge (the "no halo wider than 2 px" property of
   E1-T3). That is pinned by `packages/vision/tests/test_rectify_pixels.py`, by unit test, not
   by this eval.
4. **Content outside the mask.** A garment pasted *outside* the reference mask is not scored —
   weight there is 0. Superseded by B1 for the v1 map (which has no freedom to place content
   outside), but it becomes a live blind spot the moment a corner detector adds perspective.
5. **The contact shadow.** `meta.notes["contact_shadow"] = False`, the ticket title asks for one,
   and no check here would notice either way. See §8 Q2.
6. **Wall clock.** Not a DoD item for this stage (§"Measurement contract", rule J). The metric
   prints seconds as evidence, and gates nothing on them.

---

## 3. Rejected alternatives

| # | alternative | why rejected |
|---|---|---|
| R1 | *Implement §1.2(b) literally — output vs the stage's own warp.* | Self-comparison. Passes an identity homography. Precisely the mutation QA used to fake the prettify metric. **This is the default thing an implementer will build**; the ADR exists mostly to forbid it. |
| R2 | *Compare out-of-mask too, whole canvas.* | Dilutes the number by the backdrop area, which is 60–90 % of the canvas on these fixtures, and would let a stage destroy the garment while scoring 0.97. Also makes the number a function of garment size. |
| R3 | *Histogram / colour-statistic comparison (mean, std, χ²) in the mask.* | Blind to geometry, blind to structure, and — worse — a blanket destructive filter (blur the whole garment to flat) keeps the histogram. It would score a destroyed image highly. |
| R4 | *Perceptual hash (pHash/dHash) of the masked region.* | Binary decisions on a 64-bit digest produce a pass/fail with no gradient, cannot express "15 % destroyed", and a 1-px shift flips bits non-monotonically. |
| R5 | *Inverse-warp the output back to the photo frame.* | Two resamplings, twice the interpolation error, still ≈ 1.0 for any internally consistent stage. Strictly worse than §2.2. |
| R6 | *Wire one of the 20 point-reviewed masks as a quad ground truth.* | Those masks are in `evals/golden/`, gitignored, absent from a clean clone (ADR-010 §3 D2). A metric that only runs on one machine cannot be the DoD. |
| R7 | *Add `scikit-image` for `structural_similarity`.* | Forbidden by the ticket's spirit and by `docs/DEPS.md`'s "not dependencies, on purpose" reasoning that the prettify metric already recorded. The Wang formulation is 30 lines and already exists. |
| R8 | *Composite the two facts into one 0–1 score.* | A composite whose arithmetic is not printed next to it is a number nobody can audit, and when it drops you cannot tell which half dropped. Keep two facts; one of them is the row because PLAN says the row must be a number. |
| R9 | *Add two new `TARGETS` rows (`rectify canvas conformance`, `rectify garment registration`).* | Tempting and defensible, but it expands a shared report table owned by every epic, for one epic's benefit. Deferred to Q3: the conformance results live in the row's note and in the generated `evals/reports/rectify.md`, which is where a reviewer will actually read them. |
| R10 | *Loosen the DoD to "SSIM ≥ 0.85 against the input's own warp" and say no more.* | True, cheap, and it is what would ship if nobody wrote this ADR. It is a number that a wrong homography passes, i.e. a gate that cannot fail. |

---

## 4. Degenerate-case kill table

Each row is a defect that must lose points, with the mechanism that catches it. Column 3 is the
assertion the engineer writes in `evals/tests/test_rectify_metric.py` (§6).

| # | degenerate output | caught by | killing assertion |
|---|---|---|---|
| D1 | blank / pure-backdrop canvas (no garment) | A | `value < 0.85` (structure destroyed: the variance term collapses) |
| D2 | identity homography | **B1** | `corners_ok is False` — and, pinned explicitly, `ssim > 0.98 and not passes_dod` |
| D3 | uniform scale error (0.5× or 2×) | **B1** | residual = the scale delta at the corners |
| D4 | global offset (garment shifted 24 px) | **B1** (if in `H`) / A (if in the resampler) | one of the two must fail — the test asserts `not passes_dod` for both variants |
| D5 | 180° rotation via corner order `(BL,BR,TR,TL)` | **B1** | corner 0 lands on `(w,h)`, not `(0,0)` |
| D6 | mirror (negative determinant) | **B1** | `determinant_ok is False` |
| D7 | wrong canvas size or aspect (e.g. 1200×1200 for a `top`) | **B2** | `canvas_ok is False`, reason names both numbers |
| D8 | long edge > 1600 px | **B2** | `canvas_ok is False` |
| D9 | wrong resampler (nearest neighbour, or an fx/fy swap) | A | `value < 0.85` |
| D10 | generative repaint / inpaint inside the garment | A | `value < 0.85`; a *partial* repaint moves it monotonically (assert the direction, print the magnitude) |
| D11 | opaque composite (alpha ignored, backdrop never visible) | A only inside the mask, weakly | **not fully caught** — outside-mask coverage is R2-rejected; the composite is pinned by `test_rectify_pixels.py`. Named here so nobody assumes the eval covers it. |
| D12 | quad inset by 10 % (a plausible corner-detector bug) | **B1** | residual = 10 % of the canvas edge |
| D13 | alpha threshold 200 instead of 128 in the composite | A (§2.2 `ssim_binary` gap) | `|weighted − binary|` exceeds the run's median by a margin, plus the rim is visibly wrong — a *soft* signal, stated as such |

D11 and D13 are the two rows where this design is honestly weaker than it looks. They are in the
table on purpose.

---

## 5. Files

### 5.1 Minimum set

| file | change | in E2-T1's file list? |
|---|---|---|
| `evals/metrics/rectify.py` | **new** — the metric: transcribed spec literals, `reference_render()`, `score_output()`, `run()/summarise()/render()/main()` | ❌ outside |
| `evals/metrics/ssim.py` | **new** — the Wang SSIM moved out of `prettify.py`, plus `ssim_weighted()` | ❌ outside |
| `evals/metrics/prettify.py` | move ~65 lines out; re-export `ssim_map`, `ssim_inside_mask`, `SSIM_*`, `_luminance` so `evals/tests/test_prettify_metric.py:41-49` keeps importing exactly the same names from the same module | ❌ outside |
| `evals/run.py` | add `_measure_rectify()`; call it in `measurements()`; do **not** touch `TARGETS` | ❌ outside |
| `evals/tests/test_rectify_metric.py` | **new** — the kill matrix of §4 | ❌ outside |
| `evals/tests/rectify_controls.py` | **new** — synthetic builders (asymmetric glyph, known-quad photo) shared by the tests; ~80 lines, no fixture files | ❌ outside |
| `docs/ADR-011-rectify-ssim-semantics.md` | this file | ❌ outside |
| `packages/vision/rectify.py` | **docstring only** — lines 28-30 and 42-43 point at `prompts/02-vision.md` for a definition that is not there; repoint at this ADR. **Zero behaviour change.** | ✅ in scope |

### 5.2 Declared extensions (ticket surface is `packages/vision/rectify.py` only)

Wiring a metric necessarily touches `evals/run.py` and `evals/metrics/`. This is the same
situation `docs/HANDOFF-vision.md:737` waved away with a false sentence, and the same pattern
ADR-010 §9 had to declare explicitly. The PR must carry this list in its description:

* **Required to satisfy the DoD:** `evals/metrics/{rectify,ssim}.py`,
  `evals/metrics/prettify.py` (extraction only), `evals/run.py`,
  `evals/tests/{test_rectify_metric.py,rectify_controls.py}`.
* **Truth repair, not scope creep:** `docs/HANDOFF-vision.md:737-738` (the sentence is false and
  will be false in the opposite direction after this lands — it must be rewritten to "measured
  by `evals/metrics/rectify.py`, defined by ADR-011"), `docs/PLAN.md` E2-T1 acceptance block
  (§8 Q1: the fixture path and the DoD wording).
* **Not the engineer's to touch:** `prompts/02-vision.md:24-25` (role prompt, owned by the lead /
  PO). Flagged as a request in `HANDOFF-vision.md`, per rule 4 of `prompts/02-vision.md`.
* **Untouched, explicitly:** `packages/vision/rectify.py`'s behaviour, all of
  `packages/vision/tests/`, `docs/DEPS.md` (no new dependency), `pyproject.toml`, `Makefile`,
  `infra/ci/**`, `TARGETS`, any existing threshold.

---

## 6. Task list (dependency-ordered)

**T01 — metric core + SSIM extraction** · P0 · deps: none
`evals/metrics/ssim.py` (new) · `evals/metrics/prettify.py` (move + re-export) ·
`evals/metrics/rectify.py` (new: §2.1 spec literals, `reference_render`, `score_output`,
`CaseScore`)
Acceptance: `python -m pytest evals/tests/test_prettify_metric.py` still green with **zero**
edits to that file's imports; `score_output` returns a `CaseScore` with every field of §2 filled;
`ruff check .` clean.

**T02 — negative controls and the kill matrix** · P0 · deps: T01
`evals/tests/rectify_controls.py` (new) · `evals/tests/test_rectify_metric.py` (new) ·
`evals/tests/test_prettify_metric.py` (add the two drift tests of §2.3 / §6, nothing else)
Acceptance: every row of §4 that is marked caught has a test that fails if the corresponding
check is stubbed out. Verified by *actually stubbing it out* and watching the test go red; the
diff of that experiment goes in the PR description.

**T03 — wiring, report, and the DoD sentence** · P0 · deps: T01, T02
`evals/metrics/rectify.py` (`run/summarise/render/main`) · `evals/run.py`
(`_measure_rectify` + `measurements()`) · `evals/tests/test_harness.py` (assert the row is a
number, n=9, and that the note carries the §2.5 caveat verbatim) · `docs/PLAN.md` (E2-T1
acceptance block: fixture path + restated DoD)
Acceptance: `python -m evals.run --suite vision` prints a number in that row;
`python -m evals.metrics.rectify` prints the per-case table; `make test` still 927+ passed.

**T04 — document truth** · P1 · deps: none (must merge ≤ T03)
`docs/ADR-011-rectify-ssim-semantics.md` · `docs/HANDOFF-vision.md` (§5.2 repair + the measured
table) · `packages/vision/rectify.py` (docstring only)
Acceptance: `grep -n "prompts/02-vision.md" packages/vision/rectify.py` returns no
*definition* claim; the HANDOFF sentence names `evals/metrics/rectify.py` and ADR-011.

Deliberately **not** a task: adding fixtures under `evals/fixtures/rectify/` with hand-checked
quads. The metric runs on the 9 photos the E2-T2 fixtures were built from (already recorded as
`source.rectify_inputs` in `evals/fixtures/prettify/manifest.json`), so n=9 today with zero new
binaries. A dedicated fixture set is only worth building once there is a corner detector whose
quads could be wrong in ways §2.5 item 1 cannot see.

---

## 7. Test plan — what must be able to go red

The prettify metric's own header records the reason this section exists: QA mutated the metric
and 449 tests stayed green. The same discipline applies here, with the mutation table written in
advance.

| mutation of the *metric* | test that must fail |
|---|---|
| `score_output` compares `O` with `O` | `test_reference_is_a_function_of_the_photo_not_the_output` — two different outputs from one photo must score differently |
| the reference is rendered by importing `rectify._bilinear` | `test_reference_resampler_is_not_the_stage_resampler` — the identity case must show `max|Δ| ≤ 1` **and** at least one differing pixel; a shared implementation gives exactly 0 everywhere |
| the weight is `1` inside the bbox only | `test_the_mask_changes_the_denominator` — scoring the whole canvas vs the alpha-weighted region differs by > 0.005 on a fixture whose garment covers < 60 % |
| `corners_ok` is computed but dropped from `passes_dod` | `test_an_identity_homography_fails_the_dod_even_though_ssim_is_high` (asserts `ssim > 0.98` **and** `passes_dod is False`) — the single most important test in this file |
| the corner tolerance is relaxed to 5 px | `test_corner_check_kills_a_4px_quad_inset` |
| the aspect table is imported from the stage | `test_metric_aspect_table_is_a_transcription` (hard-coded literals) + the drift test |
| `run()` skips a case it cannot decode | `test_run_raises_instead_of_skipping` + `test_run_covers_every_case_with_rectify_inputs` (n is asserted, not inferred) |
| `summarise()` drops a `0.0` row | `test_summarise_keeps_a_zero_measurement` (the exact bug QA found in prettify) |
| the row's note loses the §2.5 caveat | `test_harness.py` asserts the caveat substring |
| the whole thing is replaced by §1.2(b) | the positive control still passes (a correct stage scores ≥ 0.95) **and** D2's test fails — a tautology cannot do both |

**Positive control, and the honest ceiling.** A correct implementation must score **≥ 0.95**, and
the reason it is not `1.0` must be measurable: Pillow's bilinear is fixed-point 8-bit, so
`max|Δ|` between `Ref` and a numpy-bilinear render of the same `P` under the same `H` is
expected to be **1 level**, not 0. That ±1 is the noise floor of this metric and it must be
*measured and printed*, not assumed. Consequence to state in the note: the achievable maximum is
≈ 0.999, so `0.85` is not being met by luck — but a reviewer who sees `0.998` must know the last
0.002 is interpolation quantisation, not a residual defect.

**Pixel-centre convention.** The one thing that can silently invalidate the whole design is a
half-pixel disagreement between Pillow's `PERSPECTIVE` and the stage's inverse mapping: it would
show up as a uniform SSIM loss and be "fixed" by loosening the threshold. Therefore T01's first
milestone is `test_identity_homography_on_a_checkerboard_matches_within_one_level` — identity
`H`, canvas == photo size, compare `Ref` with `O`; `max|Δ| ≤ 1` and mean `|Δ| ≤ 0.2`. If that
fails, **stop and report**: the two resamplers do not share a convention, and the fallback is a
freshly written numpy bilinear in the metric module (still an independent implementation, since
it is written from the formula rather than imported).

**Stop condition.** If, with every check implemented as specified and a known-good `rectify()`,
the end-to-end mean lands **below 0.85**, the engineer reports it and changes nothing. Lowering
the threshold, widening the tolerance or dropping a check to make the row green is the failure
mode this ADR exists to prevent.

**Budget.** 9 cases ≈ 9 × (0.34 s `rectify` + ~0.02 s Pillow warp + ~0.12 s SSIM) ≈ **4.3 s**,
measured `rectify` 0.336 s on `top-02` → 960×1200 canvas. If the total exceeds 5 s, the specified
reduction is to compute part A on both operands box-downsampled 2× (identical on both sides, and
it makes this number directly comparable with prettify's 480 px number), and the note must say
the comparison was at half resolution.

---

## 8. Not clear, and what I assumed

**Q1 — does Bert accept a restatement, or does the row get deleted?** I am assuming the DoD
sentence is *intent*, not *formula*, and that "SSIM ≥ 0.85 inside the mask" may be re-anchored
from "versus the source" to "versus the reference under the declared homography", with the two
conformance checks added as part of the same gate. If he rejects the re-anchoring, the fallback
is: keep the row `UNMEASURED` **permanently** with the reason "a geometric stage has no honest
SSIM-vs-source reading", and re-specify E2-T1's DoD in PLAN around conformance + a difference
metric to be named later. That is worse for the epic and better than a gate that cannot fail; I
would rather be told which one he wants.

**Q2 — the contact shadow.** The ticket *title* asks for "quad estimate, homography, `#F2F1EE`
backdrop, contact shadow"; the DoD does not mention it; the module sets
`meta.notes["contact_shadow"] = False` and says so in the docstring. Nothing here measures it. Is
the shadow in scope for E2-T1's DoD or not? If yes, the DoD needs a fourth fact and this ADR's
file list grows.

**Q3 — one row or three?** I chose one row (PLAN-mandated) + conformance in the note + a
generated `evals/reports/rectify.md` (gitignored, like `prettify.md`, pasted into the handoff by
whoever runs it). If Bert wants the conformance results visible in the `--suite vision` table
itself, that is two new `TARGETS` rows and a wider table for every epic. R9.

**Q4 — the acceptance command.** `evals/fixtures/bottom-03.jpg` never existed; the bytes are at
`evals/golden-provisional/photos/bottom-03.jpg` with mask `masks/bottom-03.png`. I assume PLAN's
path is the typo and the photo is the fixture — but note the command also omits
`--category bottom`, and it is *harmless by coincidence* only because `top` and `bottom` share the
4/5 aspect: the output would be the right shape with `meta.notes["category"] = "top"`. The
amended command should be
`python -m packages.vision.cli --in evals/golden-provisional/photos/bottom-03.jpg --out /tmp/sn2 --category bottom`.
I have not changed PLAN — that is T03, and it needs Bert's sign-off with Q1.

**Q5 — 9 cases is not 20.** n=9 comes from the E2-T2 fixture set's `rectify_inputs`; those alphas
are machine-made and 8 photos in `evals/golden-provisional` are unused. I assume a 9-case
denominator, reported as n=9 in the cell, is acceptable for a DoD that says nothing about sample
size. If the number must stand on more photos, the cheap extension is to iterate the provisional
manifest (17 photos with masks) instead of the prettify manifest — same code, no new files, n=17.
The reason I did **not** make that the default: only the 9 in the prettify manifest carry a
recorded `category` and a provenance chain in `CREDITS.md`.

**Q6 — is the ±1 fixed-point noise floor acceptable?** I assume yes, because the floor's distance
to `0.85` is 0.14, but it does mean the number is not comparable with a `scikit-image` SSIM to
four decimals. If exact comparability matters, the fallback is the numpy resampler (§7).

---

## 9. Rollback

1. **Metric only.** Delete `evals/metrics/rectify.py` and `evals/tests/test_rectify*`. The row
   returns to `UNMEASURED`; nothing else in the harness changes, because `_measure_rectify` is
   one function and one call site.
2. **SSIM extraction.** `evals/metrics/ssim.py` is a move plus a re-export; reverting it restores
   `prettify.py` byte-for-byte and `test_prettify_metric.py` never noticed the move.
3. **This ADR.** Text only. No binary, no fixture, no threshold, no `packages/vision/` behaviour
   is touched by any commit this ADR authorises.
