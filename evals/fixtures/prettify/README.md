# evals/fixtures/prettify — the E2-T2 logo-protection and crease fixtures, 9 cases

Owner: CV role. Ticket: `docs/PLAN.md` E2-T2. Consumers: `packages/vision/prettify.py`
(`prettify()`, `wrinkle_mask_for()`), `packages/vision/tests/test_prettify.py`,
`evals/metrics/prettify.py`.

```bash
python -m evals.metrics.prettify                        # measure all 9 and print the table
.venv/bin/python evals/fixtures/prettify/build_fixtures.py   # regenerate (deterministic)
```

## What is in here

| | |
|---|---|
| cases | **9** — `5 logo` (do the protection fixtures) + `4 crease` |
| files | `flat/<id>.png` (the flat-lay a stage would receive) + `alpha/<id>.png` + `ink/<id>.png` (logo cases only: the **printed** pixels, glyphs and emblem, no patch background) |
| size | **1.2 MB** total, long edge **480 px**, PNG |
| manifest | `manifest.json`: `id`, `kind`, `flat`, `alpha`, **`ink`**, `note`, `source` (incl. `rectify_inputs`), `overlay`, **`protect_box`**, **`crease_box`**, camera cast |
| sources | 9 of the 17 photos in `evals/golden-provisional/photos/` (CC0 / public domain / CC BY) |
| attribution | `CREDITS.md` here, chained to `evals/golden-provisional/CREDITS.md` |

Each case is `prettify(flat, alpha, mode=CLASSICAL, protect=[protect_box])`; the
`protect_box` is passed as a caller-supplied protection region, and the test asserts the
resulting wrinkle mask never enters it.

## Two ground truths, and why both exist

| truth | what it is | what it can prove |
|---|---|---|
| `protect_box` | the label **rectangle**: patch + blank padding | the guarantee a *caller* buys by declaring a region |
| `ink/<id>.png` | the **printed pixels** only: glyphs and emblem | the DoD line *"the inpaint mask never overlaps the logo/text region"* with **no caller box** — 0 px |

They are not interchangeable, and QA caught round 1 conflating them: a test that asserted
`mask ∩ protect_box == 0` while also passing `protect=[protect_box]` was measuring
`~(box) & box` — a tautology, because the subtraction *made* it zero. Blank padding inside a
label is pixel-identical to the fabric around it, so no pixel-level detector can protect all of
it; the printed pixels are a claim a detector can actually be held to.

Measured, `protect=None` (automatic detector only, no caller help):

| case | `protect_box` | ink px | residual on the rectangle | residual on printed ink |
|---|---|---|---|---|
| logo-01-tee-chest | 8236 px | 845 | 149 (1.81 %) | **0** |
| logo-02-breeches-waistband | 5104 | 358 | 34 (0.67 %) | **0** |
| logo-03-sneaker-side | 10498 | 1010 | 65 (0.62 %) | **0** |
| logo-04-belt-stamp | 4060 | 245 | 56 (1.38 %) | **0** |
| logo-05-jacket-emblem | 5800 | 609 | 78 (1.34 %) | **0** |

The rectangle residual is reported, never asserted away: `python -m evals.metrics.prettify`
prints it as `auto only px`, and `test_mask_never_enters_the_label_rectangle_without_a_caller_box`
is an `xfail(strict=True)` carrying these numbers — if the detector ever reaches 0, that test
turns into XPASS and fails the suite until the assertion is promoted.


## Read this before quoting a number from this set

**Every overlay is synthesised.** The labels, wordmarks, emblems and folds are *drawn* by
`build_fixtures.py` (a bundled bitmap font and a numpy luminance field) on top of a real
photograph. **None of these is the mark of a real product, and no logo was downloaded.**

Therefore:

* the logo cases measure **"the protection region is not invaded"** — a property of the mask
  arithmetic. They do **not** measure "the detector recalls real packaging logos": a real
  wordmark has kerning, anti-aliasing on fabric weave, tonal embossing and foil, none of which
  are modelled here;
* the crease cases measure **"the mask moved something in the region where folds were drawn"**.
  They do **not** measure crease recall on real flat-lays, because **museum catalogue
  photography has no creases to recall** — it is pressed and evenly lit. Measuring that needs
  phone photos of clothes on a bed, which this repo does not have (E0-T1's golden set is the
  place for them);
* `SSIM` in the report is a **destruction budget** (output vs input, inside the garment mask),
  not a cleaning score. See `evals/metrics/prettify.py`'s docstring.

## Why a fold is drawn through every label

The DoD line is *"the inpaint mask never overlaps the logo/text region"*. A test for it is
only worth something if the mask **wants** to be there: otherwise deleting the protection
subtraction leaves the test green and the constraint unmeasured. So each `logo-*` case:

1. draws the label on the garment interior,
2. re-centres one fold band through the label's centre line, and
3. applies the fold field **after** the label, so the crease is visible on the print too —
   which is what happens to a real printed label when the fabric beneath it folds.

`test_protection_subtraction_is_load_bearing` asserts step 2 actually worked (the candidate
mask enters the box with protection off) for all five cases.
`test_protection_region_is_never_invaded` asserts the outcome for all five.

## Why every flat-lay carries a camera cast

`CAMERA_CAST = (1.035, 1.000, 0.960)` is multiplied in at the end. Without it the input's
backdrop is *exactly* `#F2F1EE` — which is what `rectify()` composites — and the
white-balance step would score as an identity on every fixture. The stage exists because a
phone's auto-white-balance does not land on the backdrop; the fixture carries the error the
stage is supposed to remove, so that `notes["white_balance"]` having non-1.0 gains means
something.

## Regenerating

`build_fixtures.py` is deterministic (numpy + Pillow, no RNG, no network) and the generated
PNGs are committed, so the test suite never depends on running it. It refuses to build a case
whose label cannot sit **90 % on the garment**: the interior centroid of a V-shaped belt lands
in the empty triangle between its halves, and a label drawn there would be on the backdrop —
looking perfectly reasonable in the manifest while testing nothing. That failure mode is why
placement maximises garment coverage instead of trusting the centroid.

## Box semantics: closed on the drawn pixels, half-open when sliced

Pillow's `rectangle([x0, y0, x1, y1])` includes **both** endpoints, so the label is drawn with
`x1 = x + w - 1` / `y1 = y + h - 1` and occupies exactly `[x, x+w) × [y, y+h)`. Round 1 drew
to `x + w` and every consumer slices half-open (`region[y:y+h, x:x+w]`), which left the last
column and row of the drawn patch **outside the ground-truth box** — QA measured 48 escaping
pixels on logo-01. The manifest box and the pixels now agree by construction, and
`test_fixture_manifest_records_provenance_and_truth_boxes` asserts that `ink ⊂ protect_box`.

