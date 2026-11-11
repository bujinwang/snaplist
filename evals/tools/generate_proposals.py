"""Generate AI-proposed mask *drafts* for the 20 golden masks that need re-making (ADR-010).

THIS IS STEP 1 OF THE SOP ONLY -- mechanical proposal generation.
It does NOT review, sign off, compute edit distance, or touch the point-review register or
manifest. A human must still review each draft point by point and sign off (ADR-010 section 6);
that step can never be automated, and this tool makes no claim about it.

Two backends are pluggable via --backend:

  * floodfill-border   -- pure Pillow + numpy, zero extra deps. Honest provenance
                          `algorithmic:floodfill-border` (NOT ai-assisted): the mask is a
                          mechanical background-colour flood fill, not an AI proposal.
  * google-nano-banana -- calls Google's Gemini image model to produce a segmentation mask.
                          Needs `google-genai` (opt-in `gen` dependency group) + GOOGLE_API_KEY.
                          Provenance would be `ai-assisted:google-nano-banana+point-reviewed`
                          ONLY after a human review + sign-off; this tool never asserts that.

HONESTY NOTE ON MASK EXTRACTION
-------------------------------
Nano Banana is an image-generation model, not a segmentation model. The recipe used here --
"ask the model for a black/white garment mask, then binarise the returned image at 128" -- is
an ASSUMED DEFAULT. It MUST be validated against the two existing
`ai:google-ai-mode-nano-banana` masks (top-? / shoe-?) before any draft is treated as a real
proposal. If the model returns a red-overlay or a full render instead of a clean mask, the
binarise step is meaningless and the draft must be discarded. See ADR-010 N2: pixel-equivalent
artefacts are not evidence of a point review.

The draft directory (evals/golden/masks_draft) is excluded from git by the tracked .gitignore,
so that exclusion follows every clone; this tool writes PNGs there but never commits binaries.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN = ROOT / "evals" / "golden"
DEFAULT_REGISTER = ROOT / "evals" / "golden-review" / "point_review_register.json"
DEFAULT_MANIFEST = DEFAULT_GOLDEN / "manifest.draft.json"
DEFAULT_OUT_DIR = DEFAULT_GOLDEN / "masks_draft"

# gemini-2.5-flash-image was deprecated (shutdown 2027-03-15); gemini-nano-banana-2.1 is
# Google's recommended replacement and supports image-to-image (reference photos) prompts.
# gemini-3.1-flash-lite-image is the cheaper alternative named in the deprecation notice.
GNB_DEFAULT_MODEL = "gemini-nano-banana-2.1"
GNB_DEFAULT_PROMPT = (
    "Return ONLY a black-and-white segmentation mask of the main garment in this product "
    "photo. White pixels = the garment, black pixels = everything else. No text, no border, "
    "no other content."
)
GNB_BINARIZE_THRESHOLD = 128
GNB_REQUIRES_MSG = (
    "google-nano-banana backend requires `pip install google-genai` and GOOGLE_API_KEY"
)


# --------------------------------------------------------------------------- #
# Backend 1: floodfill-border (zero-dep algorithmic proposal)
# --------------------------------------------------------------------------- #
def floodfill_border(photo, *, tolerance: int = 32):
    """Mechanical background flood fill. Returns an L-mode mask (255 = garment/foreground).

    Samples the 2% border ring as the background reference, then floods the connected
    background component that touches the border over pixels within `tolerance` of that
    reference. Whatever the flood cannot reach is treated as the (centred) subject.

    This is NOT an AI proposal and is labelled `algorithmic:floodfill-border` accordingly.
    Interior holes in the garment (e.g. inside a tote handle) that are enclosed by the
    subject and unreachable from the border stay foreground -- acceptable for a draft a human
    will review and fix.
    """
    import numpy as np
    from PIL import Image

    rgb = photo.convert("RGB")
    arr = np.asarray(rgb, dtype=np.int16)  # h, w, 3
    h, w = arr.shape[:2]

    bf = max(1, int(0.02 * min(h, w)))
    rings = [
        arr[:bf, :, :].reshape(-1, 3),
        arr[-bf:, :, :].reshape(-1, 3),
        arr[:, :bf, :].reshape(-1, 3),
        arr[:, -bf:, :].reshape(-1, 3),
    ]
    ring = np.concatenate(rings, axis=0)
    bg = np.median(ring, axis=0).astype(np.int16)  # 3-vector
    diff = np.abs(arr - bg).sum(axis=2)  # per-pixel sum-of-abs over channels
    seed_ok = diff <= tolerance

    visited = np.zeros((h, w), dtype=bool)
    bg_mask = np.zeros((h, w), dtype=bool)
    dq: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if seed_ok[y, x] and not visited[y, x]:
                visited[y, x] = True
                dq.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if seed_ok[y, x] and not visited[y, x]:
                visited[y, x] = True
                dq.append((y, x))

    while dq:
        y, x = dq.popleft()
        bg_mask[y, x] = True
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and seed_ok[ny, nx]:
                visited[ny, nx] = True
                dq.append((ny, nx))

    fg = (~bg_mask).astype(np.uint8) * 255
    return Image.fromarray(fg)  # L inferred for uint8 2D


# --------------------------------------------------------------------------- #
# Backend 2: google-nano-banana (Gemini image model)
# --------------------------------------------------------------------------- #
def _extract_generated_image_bytes(response):
    """Pull raw image bytes out of a Gemini image-generation response.

    For the `gemini-nano-banana-2.1` model, `client.models.generate_content` returns a
    `GenerateContentResponse` whose image data arrives as an `inline_data` Blob inside the
    first candidate's `content.parts` -- NOT as a `generated_images` attribute (that field
    belongs to the separate `client.models.generate_images` API and is absent here). Walk the
    candidates and return the first image part's bytes; raise a clear error if there is none.
    """
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", []) or []:
            blob = getattr(part, "inline_data", None)
            data = getattr(blob, "data", None) if blob is not None else None
            if data:
                return data
    raise RuntimeError(
        "google-nano-banana: model response contained no image part "
        "(was the prompt answered with text, or was the image RAI-filtered?)"
    )


def google_nano_banana(
    photo,
    *,
    api_key=None,
    model=GNB_DEFAULT_MODEL,
    prompt=None,
):
    """Ask Gemini to return a garment segmentation mask, then binarise at 128.

    This function produces ONLY the raw proposal image; it writes, asserts and records no
    provenance. The fully qualified `ai-assisted:<slug>+point-reviewed` string is declared
    later by a human at `evals.tools.mask_review_kit record --proposal-source ... --attest`,
    and ONLY after a point-by-point review + sign-off (ADR-010 N2). Do NOT pass the bare
    `ai-assisted:<slug>` (without `+point-reviewed`) to --proposal-source: classify() treats
    it as MACHINE and the record is refused. See the module HONESTY NOTE.
    """
    try:
        import google.genai as genai
    except ImportError as exc:
        raise RuntimeError(GNB_REQUIRES_MSG) from exc
    if not api_key:
        raise RuntimeError(GNB_REQUIRES_MSG)

    client = genai.Client(api_key=api_key)
    prompt = prompt or GNB_DEFAULT_PROMPT
    response = client.models.generate_content(model=model, contents=[prompt, photo])
    raw = _extract_generated_image_bytes(response)

    import numpy as np
    from PIL import Image

    generated = Image.open(io.BytesIO(raw)).convert("L")
    arr = np.asarray(generated)
    mask = (arr >= GNB_BINARIZE_THRESHOLD).astype(np.uint8) * 255
    return Image.fromarray(mask)  # L inferred for uint8 2D


BACKENDS = {
    "floodfill-border": floodfill_border,
    "google-nano-banana": google_nano_banana,
}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _count_effective_drafts(report: list[tuple[str, str, str]], out_dir: Path) -> int:
    """Count ids whose draft really exists once the run has finished.

    `OK` means this run wrote the draft; `SKIP / draft exists` means an earlier run wrote it
    and this run is an idempotent no-op. Both are effective output. A `photo missing` SKIP is
    NOT, even when an unrelated stale PNG happens to sit in out_dir for that id. Counting only
    rows that resolved to a real draft keeps the zero-output check honest without breaking
    idempotent re-runs.
    """
    count = 0
    for rid, status, msg in report:
        if status != "OK" and not (status == "SKIP" and msg.startswith("draft exists")):
            continue
        if (out_dir / f"{rid}.png").is_file():
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.tools.generate_proposals",
        description="Step 1 of the ADR-010 SOP: generate (not review) 20 mask drafts.",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help="golden manifest with photo paths")
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER,
                        help="point-review register whose expected_ids select the 20 targets")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN,
                        help="base dir for photos and the masks_draft output dir")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                        help="where draft PNGs are written (default: <golden-dir>/masks_draft)")
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="floodfill-border")
    parser.add_argument("--tolerance", type=int, default=32,
                        help="floodfill-border colour tolerance (sum-of-abs over RGB)")
    parser.add_argument("--ids", default=None,
                        help="comma-separated subset of ids; default = register expected_ids")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing draft instead of skipping it")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen, write nothing")
    # google-nano-banana specific
    parser.add_argument("--model", default=GNB_DEFAULT_MODEL)
    parser.add_argument("--api-key", default=None,
                        help="GOOGLE_API_KEY; read from env if omitted")
    parser.add_argument("--prompt", default=None,
                        help="override the default mask-extraction prompt")
    args = parser.parse_args(argv)

    if args.api_key is None:
        args.api_key = os_environ("GOOGLE_API_KEY")

    manifest = _load_json(args.manifest)
    photo_by_id = {m["id"]: m.get("path") for m in manifest}
    register = _load_json(args.register)
    expected = list(register.get("expected_ids", []))
    ids = [i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else expected

    if args.backend == "google-nano-banana":
        call_kwargs = {
            "api_key": args.api_key,
            "model": args.model,
            "prompt": args.prompt,
        }
    else:
        call_kwargs = {"tolerance": args.tolerance}
    backend = BACKENDS[args.backend]

    out_dir = args.out_dir
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    report: list[tuple[str, str, str]] = []
    failures = 0
    for rid in ids:
        photo_rel = photo_by_id.get(rid)
        if not photo_rel:
            report.append((rid, "SKIP", "no photo path in manifest"))
            continue
        photo_path = args.golden_dir / photo_rel
        if not photo_path.is_file():
            report.append((rid, "SKIP", f"photo missing: {photo_path}"))
            continue
        out_path = out_dir / f"{rid}.png"
        if out_path.is_file() and not args.force and not args.dry_run:
            report.append((rid, "SKIP", "draft exists (use --force)"))
            continue
        if args.dry_run:
            report.append((rid, "DRY", f"would write {out_path}"))
            continue
        try:
            from PIL import Image

            im = Image.open(photo_path).convert("RGB")
            mask = backend(im, **call_kwargs)
            mask.save(out_path)
            report.append((rid, "OK", str(out_path)))
        except Exception as exc:  # clean failure: no traceback, non-zero exit (QA fix #1)
            report.append((rid, "FAIL", str(exc)))
            failures += 1

    counts = {"OK": 0, "SKIP": 0, "DRY": 0, "FAIL": 0}
    for rid, status, msg in report:
        print(f"{rid}\t{status}\t{msg}")
        counts[status] += 1
    print(
        f"\n{len(report)} id(s) processed: "
        f"OK={counts['OK']} SKIP={counts['SKIP']} "
        f"DRY={counts['DRY']} FAIL={counts['FAIL']}."
    )

    if failures:
        print(f"\n{failures} draft(s) failed to generate.", file=sys.stderr)
        return 1
    if not args.dry_run and _count_effective_drafts(report, out_dir) == 0:
        print(
            "no proposal was produced; see SKIP rows "
            "(photo missing, no manifest path, or no draft written)",
            file=sys.stderr,
        )
        return 1
    return 0


def os_environ(name: str) -> str | None:
    import os

    return os.environ.get(name)


if __name__ == "__main__":
    raise SystemExit(main())
