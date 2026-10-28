# evals/golden-provisional — a web-sourced stand-in, NOT the golden set

**This directory is not `evals/golden/` and it cannot replace it.** It exists so the
segmentation metric can be exercised end to end — the report code, the mask format, the
plumbing between `segment()` and an IoU number — while `evals/golden/` stays empty and
honest, waiting for 40 photos Bert shoots himself.

If a number measured here ever reaches a document without the word *provisional* next to it,
that document is wrong.

## What this is

| | |
|---|---|
| entries | **17**, every one with a mask |
| photos | Wikimedia Commons, licences CC0 / public domain / CC BY (attribution in `CREDITS.md`) |
| masks | **algorithmic**, not hand-drawn: background-colour distance + Otsu + corner flood fill |
| categories | 2 top, 6 bottom, 4 shoe, 4 accessory, 1 outerwear, 0 dress |
| resolution | 1280 px on the long edge (Commons thumbnail, not the original upload) |

## Why the numbers from this set are optimistic, in one paragraph

Every photo here was picked *because* a colour mask could isolate it: one garment, one
plain-ish background, a clear colour boundary. That is the easy half of the problem. A
seller's photo is a black knit on a black chair, shot on a phone at night, with a sleeve
crossing the body. So a mean IoU computed here answers "does the pipeline work at all",
not "is the model good enough for the product". The DoD's `≥ 0.92` is a gate on the real
golden set; treat it as a smoke threshold here.

There is a second bias worth naming: the masks come from colour separation, and `segment()`
is a learned salient-object model. Two different algorithm classes, so this is not
circular — but where a garment is separable by colour alone, a colour mask is nearly exact,
which flatters any segmenter on those images.

## Composition is deliberately not the E0-T1 contract

`evals/golden/README.md` requires 10/10/5/5/5 plus 5 hard cases. Commons cannot supply that
with clean licences: a sweep of 672 licence-filtered garment photos yielded roughly a dozen
usable bottoms and no dresses that survived mask verification. Rather than fake the
composition, this set carries whatever variety the source actually had, and
`make golden-provisional` checks **structure only** (`--profile provisional`). The category
mix is printed in every report so nobody has to infer it.

## How the masks were made

`evals/tools/provisional_masks.py` — numpy + PIL, no new dependency:

1. estimate the background colour as the median of a 2 % border ring;
2. luminance-weighted RGB distance from that colour, min-max normalised;
3. Otsu threshold (floor 0.06), corner flood fill marks the background, so interior holes
   (laces, gaps between handles) fill for free;
4. keep the one connected body containing the mask's centre of mass — this is what drops a
   second object in frame;
5. 3×3 close, then a 1 px Gaussian for a soft edge.

Every mask was then **checked by eye against a tinted overlay**, and the ones that missed a
hem, a cuff or a sole were dropped rather than shipped. 17 survived out of 25 candidates.

## Regenerating

```bash
make golden-provisional     # structural check of this directory
make seg-iou                # segment every photo, IoU against the masks, writes evals/reports/seglab.md
```
