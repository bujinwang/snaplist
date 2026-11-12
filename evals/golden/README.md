# evals/golden — the golden set (E0-T1)

**Current state (2026-11-08): 40 real photos, 20 masks and `manifest.json` exist ON THIS
MACHINE ONLY.** The repo is public and the downloaded photos are all-rights-reserved, so
`photos/`, `masks/` and `manifest.json` are deliberately kept out of git by
`.git/info/exclude`; the generated eval reports are excluded by `.gitignore`. They are
**NOT committed** and must not be added. On a clean clone none of these files exist and every
metric that depends on them falls back to `UNMEASURED` — the honest value, not a regression.

> **Status update (2026-11-10)** — The paragraph above records the state of the 2026-11-08
> machine and is kept verbatim for provenance; it does **not** describe this clone. This local
> clone was rebuilt on 2026-11-09 and `.git/info/exclude` no longer contains the
> `evals/golden/**` exclusion rules that paragraph names. The exclusion is now provided by the
> tracked `.gitignore` instead, so it travels with every clone: `evals/golden/photos/*`,
> `evals/golden/masks/*`, `evals/golden/masks_draft/*` and `evals/golden/manifest.json` are
> ignored, while the two `.gitkeep` files are re-included. Dated-addendum convention:
> `docs/ADR-010-mask-provenance-ai-assisted-point-reviewed.md` §9.1, `docs/QA.md` §15.8.
> The all-rights-reserved photos, masks and manifest are **still never committed**.

> **AMENDMENT (2026-11-12, Bert ruling 3)** — Bert explicitly ruled to permit
> **AI-generated synthetic photos** for the 10 replacement slots of this local set
> (`bottom-02`, `bottom-04`, `bottom-06`, `dress-02`, `dress-04`, `hard-01`…`hard-05`).
> This **supersedes** the "Synthetic stand-ins are still forbidden" sentence below and
> the corresponding licensing note in `SOURCING.md`; both are kept verbatim for provenance.
> The decision and its cost are recorded here, not silently applied:
>
> - Each of the 10 entries carries `photo_source: "synthetic:imagegen"` in
>   `manifest.draft.json` and the local `manifest.json`. They are **never** described as
>   real seller photographs; the other 30 web-sourced photos remain unchanged.
> - A uniform bottom strip was cropped off each generated image to remove the tool's
>   "AI generated" watermark, which would otherwise pollute the mask. The unmodified
>   originals remain in the workspace as provenance evidence.
> - The register's `DISCLOSURE (… Bert ruling 3)` note records the generation, the
>   watermark crop, the scripted mask refinement and the proxy `--attest` in full.
> - **Metric scope is permanently changed**: E3-T3 accuracy, E2-T3 human rubric and
>   dedupe thresholds measured on this set are results on **synthetic inputs**. They must
>   never be quoted as real-photo performance. The original E0-T1 goal — 40 *real* seller
>   photos — is **not** met by this set.
> - The edit-distance threshold is still `UNSET`, so **U1 is UNMET** and E1-T4 stays
>   blocked; `make golden` exit 0 is a mechanical file/count check, not a DoD verdict.

Under ADR-010 the golden set contract requires 40 *real* garment photos with 20 reference
masks in one of three provenance tiers: **hand-drawn**, **AI-assisted point-reviewed**
(`ai-assisted:<model>+point-reviewed`) or **machine-made**. Synthetic stand-ins are still
forbidden by `docs/PLAN.md` E0-T1 ("a fake golden set makes every downstream number a lie") —
that rule forbids fabricated photos and placeholder masks, not an AI proposal followed by an
evidenced human point review. On this machine all 20 masks are in the **machine tier**
(`algorithmic:matting` ×18, `ai:google-ai-mode-nano-banana` ×2); hand-drawn 0/20 and
AI-assisted point-reviewed 0/20. Bert's ruling 1 does **not** grandfather them: they must be
re-made under the new rules and the review evidenced in
`evals/golden-review/point_review_register.json`. The E0-T1 / E1-T4 hand-mask DoD is therefore
**NOT** met, **U1 is UNMET**, E1-T4 stays blocked and E2 must not start.

`make golden` exits 0 on this machine, but the validator itself prints
`STRUCTURE COMPLETE — … make golden exit 0 is NOT a golden-set DoD verdict`
and a three-tier `mask provenance (reported, not gated)` tally. Exit 0 means the file/count
checks hold; it is not a DoD verdict, and `validate_point_reviews.py` exit 0 means only that
the register is structurally valid.

This README plus `evals/tools/validate_golden.py` is the contract *around* the photos: drop
the files in, fill the manifest, run `make golden`, and the tool tells you exactly what is
still missing. No guessing, no half-valid set sneaking into a report.

## Layout

```
evals/golden/
  manifest.json        # a JSON ARRAY of 40 entries (not an object — `jq 'length'` must print 40)
  photos/<id>.jpg      # the real photo, JPEG or PNG, sRGB
  masks/<id>.png       # 8-bit single-channel mask, same pixel size as the photo, 255 = garment
```

`manifest.json` is a bare array because two consumers already assume it:
`jq 'length' evals/golden/manifest.json` in the E0-T1 acceptance, and `evals/run.py`'s
`golden_state()` which reads a list as the entry count. Do not wrap it in `{"items": ...}`.

## Entry shape

```json
{
  "id": "top-01",
  "path": "photos/top-01.jpg",
  "mask_path": "masks/top-01.png",
  "mask_source": "ai-assisted:sam2+point-reviewed",
  "mask_draft": "masks_draft/top-01.png",
  "mask_quality": "ok",
  "category": "top",
  "colors": ["charcoal"],
  "pattern": "solid",
  "material": ["cotton"],
  "hard_attributes": ["ribbed hem", "slight pilling at cuffs", "faded print"],
  "hard_case": false,
  "notes": "hanger shot, north window light"
}
```

| field | rule |
|---|---|
| `id` | unique, lowercase slug; the file names should match it |
| `path` | relative to `evals/golden/`; must exist and decode as an image |
| `mask_path` | `null` for the 20 entries without a reference mask, a path for the 20 with one |
| `mask_source` | present on all 20 masked entries; one of the three ADR-010 tiers, classified by `evals/mask_provenance.py`. The current local values (`algorithmic:matting`, `ai:google-ai-mode-nano-banana`) are **machine tier** and are not grandfathered |
| `mask_draft` | present on the 20 masked entries; relative path of the AI proposal PNG (`masks_draft/<id>.png`) that the reviewed mask was edited from |
| `mask_quality` | present on the 20 masked entries; free-text note. `ok (visually checked as red overlay)` is a historical visual check, **not** point-review evidence under ADR-010 |
| `category` | exactly one of `top`, `bottom`, `dress`, `outerwear`, `shoe`, `accessory`, `other` (the `GarmentCategory` contract values) |
| `colors` | 1–3 lowercase strings, primary first (`ItemAttributes.colors` contract) |
| `pattern` | one of the `Pattern` contract values (`solid`, `striped`, `plaid`, `floral`, `graphic`, `animal`, `geometric`, `tie_dye`, `knit`, `other`, `unknown`) |
| `material` | 1+ lowercase fibre names, no percentages (contract: evidence-only) |
| `hard_attributes` | **exactly 3** non-empty strings — the things a VLM usually gets wrong |
| `hard_case` | boolean; exactly 5 entries are `true` |

## Composition — 40 entries, and how the quotas add up

PLAN E0-T1 says "10 tops / 10 bottoms / 5 dresses / 5 shoes / 5 accessories / 5 hard cases".
That sums to 40 only if **the 5 hard cases are an extra bucket on top of the 35 category
entries**, so that is the interpretation `validate_golden.py` enforces:

- among the 35 entries with `hard_case: false`: exactly **10 top, 10 bottom, 5 dress,
  5 shoe, 5 accessory**;
- exactly **5 entries with `hard_case: true`** — any category, but still a real photo of a
  real garment;
- **20 of the 40 entries carry a reference mask** (`mask_path`) that must satisfy ADR-010's
  two-condition check; the rest are `null`.

Hard cases are the photos that break naive pipelines: black garment on a black chair, busy
patterned bedspread, garment on a wire hanger, strong window backlight, a strap or sleeve
crossing the garment. Pick five that each break something different.

If the lead ever wants a different split, the numbers live in `BASE_QUOTAS`, `HARD_CASES` and
`TOTAL_ENTRIES` at the top of `evals/tools/validate_golden.py` — one edit, and the tool
enforces the new rule instead of a stale one.

## Shooting rules (they are eval rules, not taste)

- One garment per photo, laid flat or on a hanger, filling most of the frame.
- Diffuse daylight or a softbox; no hard flash, no colour cast you would not want scored.
- Background: anything real and cluttered is allowed *in the hard cases*; keep the 35 normal
  entries on a plain light surface.
- Do not crop the garment out of frame — `bad_photo` is a producer of failures, not a fixture.
- Keep the originals; the JPEG you shot is the fixture, not a re-export.

## Masks

ADR-010 keeps **three** `mask_source` tiers, and the report must keep them distinct:

| tier | `mask_source` | how it is evidenced |
|---|---|---|
| hand-drawn | `hand`, `hand-drawn`, `hand:<tool>` | a human drew the 8-bit PNG (Photoshop, GIMP, labelme export; macOS Preview is not enough) |
| AI-assisted point-reviewed | `ai-assisted:<model>+point-reviewed` | an AI **proposal** PNG plus a human point-by-point review recorded in `evals/golden-review/point_review_register.json` (proposal model + full version, both sha256, XOR@128 edit distance, review record). A bare `ai-assisted:sam2` is machine tier — a proposal is not a review |
| machine-made | `algorithmic:*`, `ai:*`, `provisional`, `unspecified` | no human review; a pipeline check only |

255 = garment, 0 = background; soft edges welcome but not required. Masks are what turn "the
cutout looks fine" into an IoU number (E1-T4). The structured review register **replaces** the
old red-overlay visual check: `PHOTO_QC.md:20`'s red-overlay inspection and
`mask_quality: ok (visually checked as red overlay)` **no longer constitute review evidence**.
A pixel-equivalent artefact (re-encoded copy, duplicate hash, size-only comparison) is not a
point review either (ADR-010, N2). On this machine all 20 are machine tier and the register is
`0/20` reviewed with the edit-distance threshold `UNSET`, so the hand-mask gate stays open.

## Running the check

```bash
make golden                                   # the checklist, exit 0 only when complete
.venv/bin/python -m evals.tools.validate_golden --golden-dir evals/golden
```

Exit codes: `0` complete, `1` incomplete (something is missing), `2` the manifest itself is
unusable (absent, not JSON, not an array). A missing photo fails the run even if the count is
right — the manifest is a promise about files that exist.

> **Status update (2026-11-11 UTC).** The paragraphs above are kept verbatim for provenance but
> no longer describe this machine. In particular: line 3–4 ("40 real photos, 20 masks and
> `manifest.json` exist ON THIS MACHINE ONLY"), line 25–27 ("all 20 masks are in the **machine
> tier**") and line 135–136 ("all 20 are machine tier … register is `0/20`") refer to the
> contract's 20 MASK slots in `evals/golden-review/point_review_register.json`, not to files
> present here. The true local state is: `evals/golden/photos/` holds 40 JPEGs (git-ignored via
> the tracked `.gitignore`), `evals/golden/masks/` holds **0** PNGs, and
> `evals/golden/manifest.json` is **ABSENT**. There are no mask files to tier.
