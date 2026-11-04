# evals/fixtures/dedupe — the E3-T4 false-merge fixture, 20 pairs, two layers

Owner: CV role. Ticket: `docs/PLAN.md` E3-T4. Consumer: `packages/vision/dedupe.py`
(`find_duplicate()`) and `packages/vision/tests/test_dedupe.py`.

```bash
python -m packages.vision.dedupe --fixture evals/fixtures/dedupe
```

## What is in here

| | |
|---|---|
| pairs | **20** — `3 exact` / `7 near` / `10 distinct` |
| image files | 12 `base/` + 3 `exact/` + 7 `variant/` (JPG, long edge <= 800 px, **0.96 MB** total) |
| manifest | `manifest.json`: `id`, `a_path`, `b_path`, `label`, **`kind`**, `transform`, `why`, `source`, `licence` |
| sources | 12 of the 17 photos in `evals/golden-provisional/photos/` (CC0 / public domain / CC BY) |
| attribution | `CREDITS.md` here, chained to `evals/golden-provisional/CREDITS.md` |

A pair is scored as `find_duplicate(a_bytes, [b_path])`: `a_path` is the upload, `b_path` is
the candidate, and the prediction is "duplicate" iff a match is returned.

## Two kinds of duplicate, because the key has two halves

`apps/api/requests.py::CreateItemRequest.source_sha256` is the dedupe key and it predates this
stage. So `find_duplicate()` has two layers and the fixture has to exercise both:

| `kind` | what `b` is | which layer decides | what the number means |
|---|---|---|---|
| `exact` | a **byte-for-byte copy** of `a` (same sha256) | digest comparison, no threshold | **1.0 is the layer's definition, not a measured capability.** Quote it as plumbing, never as accuracy. |
| `near` | a **transform** of `a` — different bytes, so the digest cannot see it | perceptual layer, `STRUCTURE_MIN` / `COLOUR_MIN` | the only recall worth reporting, and an **upper bound** — see the next section |
| — (`label: distinct`) | a different physical item | both layers must say no | the false-merge denominator |

Why three `exact` pairs and not seventeen: an exact pair is a digest equality, so the second
one proves nothing the first did not. Three check the manifest plumbing across three sources.
The remaining duplicate pairs go to the layer that can be wrong.

**The exact layer is not decoration, and it is not only about plumbing.** A flat, textureless
frame — a care-label close-up, a plain backdrop, a grey flat-lay — has no structural direction,
so the perceptual layer answers "no match" for two frames that look identical. Without the
digest layer those uploads would never be de-duplicated at all. `test_flat_frame_with_one_small_mark_is_not_a_match`
pins the other side of it: near-identical to the eye but different bytes must still **not** merge,
which is what stops this layer from being a similarity heuristic wearing a hash.

`build_fixtures.py` asserts the split rather than trusting it, and the test suite enforces both
directions: `test_exact_pairs_are_byte_identical_and_matched_by_digest` fails if an `exact` pair
is not byte-identical, `test_near_pairs_are_not_byte_identical` fails if a `near` pair is. A
fixture where the labels lie is worse than no fixture.

## Read this before quoting a recall number

**The seven `near` positives are not duplicate uploads either. They are one licensed photo and a
transform of it** — scaled, re-encoded at a lower JPEG quality, given a white border, mildly
cropped, brightness-nudged (`manifest.json` records which, per pair). `evals/golden-provisional/`
holds one photo per item and therefore contains **zero** real duplicate pairs of either kind;
generating them was the only honest option, and it has two consequences that must travel with
every number:

1. **Near-recall is an upper bound.** Every near positive is the same pixels, so the structural
   score lands near 1.0. A real near-duplicate that went through a messaging app, a screenshot
   round trip or a different phone will score lower. Measured here: near-recall 1.00; the
   weakest near positive is `near-05` at **0.915** against a 0.90 threshold. A harsher transform
   than that would be missed.
2. **The perceptual threshold is in-sample.** 0.90 was chosen on these 20 pairs. There is no
   held-out set, so this fixture cannot claim the threshold generalises — only that a false
   merge does not happen on it. `docs/HANDOFF-vision.md` says the same thing in the same words.

**The `exact` layer gets no caveat and needs none**: it is `sha256(a) == sha256(b)`, and the
test suite pins that it still works with the perceptual thresholds pushed out of reach. It also
cannot produce a false merge, because two different files do not share a sha256.

**The false-merge number is the honest one, and it belongs to the perceptual layer.** All 10
negatives have different bytes from their query, so every one of them is a perceptual decision.

## The negatives

Ten pairs of *different physical items*, distinguishable by accession number in
`evals/golden-provisional/CREDITS.md`. They were chosen to be hard, not decorative:

| pair | why it is hard |
|---|---|
| `dist-01` – `dist-06` | six pairs of tan riding breeches from **one museum series**, on the same white sweep, shot from the same height with the legs overlapping. Same category, same colour family, same lighting. A colour-only or layout-only matcher merges all six. |
| `dist-07` | black chelsea boot vs dark brown derby pair — both dark footwear, both small in a white frame, both in the lower half. |
| `dist-08` | two *pairs* of shoes laid out side by side at the same angle: layout alone cannot separate them. |
| `dist-09` | a white sleeveless jersey and a white tee — the garment colour matches, only the surroundings differ. |
| `dist-10` | a tan jacket and cream breeches: mean colour within a few RGB units, same white sweep, similar scale. |

Composition bias, stated rather than hidden: 6 of the 10 negatives are breeches, because the
source set contains 6 breeches out of 17 (it is a web-sourced stand-in, not the golden set —
see `evals/golden-provisional/README.md`). The negatives are therefore biased towards
"same garment type, same studio", which is the *dangerous* end, not the easy one.

**`dist-01` is the one pair that is hard to tell apart by eye, and it is worth naming.** `bottom-01`
vs `bottom-02` are two pairs of tan breeches from one museum series: at the waistband the belt
loops, the two buttons, the woven label and the pleat shapes are nearly identical. They are two
different Commons files with two different accession numbers (`AM 1973.193-1` / `-2`), and QA
independently checked that (`AM 1973.193-1` is not a re-upload of `-2`). During the audit QA also
measured best-alignment NCC on the pair: **0.635 for `dist-01`**, against **0.821 for a genuine
re-encode of one photo** and 0.438 / 0.068 for two other negatives — i.e. clearly below the
"same photograph" band, so calling them distinct holds. Note that this module's own score for the
pair is **0.4672**, not 0.635: QA used a different, uncentred NCC, and the two numbers are not
interchangeable. `dist-01` is the only negative in the set that needs the accession number rather
than the eye to be believed, and everything downstream inherits that.

## The denominator, and why the gate is effectively zero-tolerance

`false_merge_rate` divides by the **10 distinct pairs** — a false merge can only happen there,
and dividing by all 20 would halve the number. With a denominator of 10, the PLAN gate
(`<= 0.05`) allows exactly **zero** false merges: one false merge reads 0.10. That is a
stricter gate than the number suggests, and it is the right strictness — a false merge deletes
a seller's listing.

## Regenerating

```bash
.venv/bin/python -m evals.fixtures.dedupe.build_fixtures
```

Deterministic: PIL's JPEG encoder is fixed-parameter, the transforms are literal constants, and
nothing is downloaded. It **prunes stale images** — a `.jpg` in `base/`, `exact/` or `variant/`
that no recipe claims is deleted, because stale debris still counts against the size budget and
still looks like part of the set. Regenerating rewrites `manifest.json`; run
`python -m packages.vision.dedupe --fixture evals/fixtures/dedupe` afterwards, because a
regenerated fixture invalidates every number quoted above.

Adding a pair means adding a recipe to `build_fixtures.py`, not hand-copying a file — so the
provenance stays checkable. **No pair may enter this directory without a licence and a
`source` path under `evals/golden-provisional/`.**
