"""Harvest clothing photos from Bing Images for LOCAL evaluation only.

Outputs land in ~/.cache/snaplist/golden-web/ — **all rights reserved, never committed**.
This is a dev-only harvester, not a fixture generator. It documents how the local web set
was produced so the run is reproducible; the downloaded bytes are not part of the repo and
must never be committed, shipped, or used as marketing. See golden-web/README.txt.

    python -m evals.tools.fetch_web_images --target 70

Why Bing and not Google
-----------------------
Google Images bot-walls automated fetching: a direct fetch returns their "can't access
Google" page and zero image URLs (confirmed via WebFetch on 2026-10-28). Bing's
/images/async endpoint is fetchable and returns the same retail / marketplace clothing
photos. The earlier pass (before this script) pulled 9 usable images from 636 harvested
URLs via the same source; this script automates and widens that.

Acceptance gate (segmentability)
-------------------------------
A photo is kept only if the colour-distance mask (evals/tools/provisional_masks.py, the
same algorithm used for the provisional set) can isolate a single dominant object:
  * opens as RGB/RGBA, min(width,height) >= 220
  * garment coverage in [0.08, 0.80]
  * bounding-box fill >= 0.35  (two separated objects -> low fill -> rejected as multi-item)
This is deliberately the same bar as the provisional set, so the masks stay comparable.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from evals.tools.provisional_masks import mask_from_photo

WEB_DIR = Path.home() / ".cache" / "snaplist" / "golden-web"
SOURCES = WEB_DIR / "sources.json"
MANIFEST = WEB_DIR / "manifest.json"
PHOTOS = WEB_DIR / "photos"
MASKS = WEB_DIR / "masks"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# query -> category, biased toward product/stock shots (segmentable) with a few
# messy-seller queries kept for realism.
CATEGORY_BY_QUERY: dict[str, str] = {
    "shirt product photo white background": "top",
    "men t-shirt plain background": "top",
    "blouse product photo white background": "top",
    "polo shirt product photo": "top",
    "knit jumper product photo": "top",
    "hoodie product photo white background": "top",
    "dress product shot white background": "dress",
    "summer dress product photo": "dress",
    "jeans product photo white background": "bottom",
    "trousers pants product photo": "bottom",
    "shorts product photo": "bottom",
    "skirt product photo white background": "bottom",
    "jacket product photo white background": "outerwear",
    "winter coat product shot": "outerwear",
    "blazer product photo": "outerwear",
    "sneaker product photo white background": "shoe",
    "leather boots product photo": "shoe",
    "sandals product photo": "shoe",
    "handbag product photo white background": "accessory",
    "scarf product photo": "accessory",
    "beanie hat product photo": "accessory",
    "used clothing second hand photo": "top",
}

# The 9 legacy entries (idx 375..471) had no category field; map them explicitly so the
# regenerated manifest stays consistent.
LEGACY_CATEGORY = {
    375: "shoe", 376: "shoe", 382: "shoe", 386: "shoe", 387: "shoe",
    402: "shoe", 403: "shoe", 466: "top", 471: "top",
}

MIN_SIDE = 220
COVERAGE_MIN = 0.08
COVERAGE_MAX = 0.80
FILL_MIN = 0.35


def fetch_bing_urls(query: str, count: int = 50) -> list[tuple[str, str]]:
    """Return (murl, purl) pairs for a Bing Images query. murl = direct image CDN URL."""
    q = urllib.parse.quote(query)
    url = f"https://www.bing.com/images/async?q={q}&count={count}&first=1&mmasync=1"
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        print(f"  [fetch] {query!r} failed: {exc}", file=sys.stderr)
        return []
    raw = html.unescape(raw)
    murls = re.findall(r'murl":"(https?://[^"]+)', raw)
    purls = re.findall(r'purl":"(https?://[^"]+)', raw)
    pairs = []
    for i, murl in enumerate(murls):
        purl = purls[i] if i < len(purls) else ""
        pairs.append((murl, purl))
    return pairs


def download(url: str, max_bytes: int = 12_000_000) -> bytes | None:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": "https://www.bing.com/"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read(max_bytes)
    except (urllib.error.URLError, OSError):
        return None


def is_acceptable(photo_bytes: bytes) -> dict | None:
    """Return {size, coverage} if the photo is a single segmentable garment, else None."""
    try:
        im = Image.open(io.BytesIO(photo_bytes))
        im = im.convert("RGB")
    except Exception:
        return None
    w, h = im.size
    if min(w, h) < MIN_SIDE:
        return None
    # One fixed temp file, overwritten each call. We deliberately do NOT delete it here:
    # the sandbox's bulk-delete guard aborts the run once enough unlink() calls land in a
    # single turn. The temp is named outside the w*.jpg glob, so it never pollutes the set.
    tmp = WEB_DIR / "_accept_tmp.jpg"
    im.save(tmp)
    mask, stats = mask_from_photo(tmp)
    if stats.get("empty"):
        return None
    cov = stats["coverage"]
    if not (COVERAGE_MIN <= cov <= COVERAGE_MAX):
        return None
    arr = np.asarray(mask) > 127
    ys, xs = np.nonzero(arr)
    if not len(xs):
        return None
    bb_area = (int(xs.max()) - int(xs.min()) + 1) * (int(ys.max()) - int(ys.min()) + 1)
    if bb_area == 0:
        return None
    fill = float(arr.sum()) / bb_area
    if fill < FILL_MIN:
        return None
    return {"size": [w, h], "coverage": round(cov, 4)}


def next_idx(sources: list[dict]) -> int:
    existing = [s["idx"] for s in sources] + [
        int(p.stem[1:]) for p in PHOTOS.glob("w*.jpg")
    ]
    return (max(existing) + 1) if existing else 300


def rebuild_manifest(sources: list[dict]) -> None:
    entries = []
    for s in sources:
        entries.append(
            {
                "id": f"w{s['idx']}",
                "path": f"photos/w{s['idx']}.jpg",
                "mask_path": f"masks/w{s['idx']}.png",
                "category": s["category"],
                "mask_source": "algorithmic:background_colour_distance",
                "source": {
                    "provider": "Bing Images (all rights reserved)",
                    "page": s.get("page", ""),
                    "file": s["full"],
                    "query": s.get("query", ""),
                },
                "notes": s.get("note", ""),
            }
        )
    MANIFEST.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.tools.fetch_web_images")
    parser.add_argument("--target", type=int, default=70, help="max accepted images overall")
    parser.add_argument("--per-category", type=int, default=8)
    parser.add_argument("--max-downloads", type=int, default=240, help="hard cap on fetches")
    parser.add_argument("--count-per-query", type=int, default=50)
    args = parser.parse_args(argv)

    for d in (WEB_DIR, PHOTOS, MASKS):
        d.mkdir(parents=True, exist_ok=True)

    sources: list[dict] = []
    if SOURCES.is_file():
        sources = json.loads(SOURCES.read_text())
        for s in sources:
            s.setdefault("category", LEGACY_CATEGORY.get(s["idx"], "top"))

    seen = {s["full"] for s in sources}
    accepted = len(sources)
    per_cat: dict[str, int] = {}
    for s in sources:
        per_cat[s["category"]] = per_cat.get(s["category"], 0) + 1
    downloads = 0

    PHOTOS.mkdir(parents=True, exist_ok=True)
    MASKS.mkdir(parents=True, exist_ok=True)

    for query, category in CATEGORY_BY_QUERY.items():
        if accepted >= args.target:
            break
        if per_cat.get(category, 0) >= args.per_category:
            continue
        print(f"[query] {query!r} ({category})")
        pairs = fetch_bing_urls(query, count=args.count_per_query)
        for murl, purl in pairs:
            if accepted >= args.target or downloads >= args.max_downloads:
                break
            # Per-category cap is enforced here too, not only between queries, so a single
            # high-yield query cannot monopolise the whole set.
            if per_cat.get(category, 0) >= args.per_category:
                break
            if murl in seen:
                continue
            seen.add(murl)
            downloads += 1
            data = download(murl)
            if not data:
                continue
            verdict = is_acceptable(data)
            if not verdict:
                continue
            idx = next_idx(sources)
            photo_path = PHOTOS / f"w{idx}.jpg"
            mask_path = MASKS / f"w{idx}.png"
            im = Image.open(io.BytesIO(data)).convert("RGB")
            im.save(photo_path, "JPEG", quality=92)
            mask, _ = mask_from_photo(photo_path)
            mask.save(mask_path)
            sources.append(
                {
                    "idx": idx,
                    "page": purl,
                    "full": murl,
                    "query": query,
                    "title": "",
                    "size": verdict["size"],
                    "coverage": verdict["coverage"],
                    "note": "",
                    "category": category,
                }
            )
            accepted += 1
            per_cat[category] = per_cat.get(category, 0) + 1
            print(
                f"  + w{idx} {category} cov={verdict['coverage']:.3f} "
                f"({accepted}/{args.target}, dl={downloads})"
            )
            # Durable after every accept: a crash must not lose recorded progress again.
            SOURCES.write_text(json.dumps(sources, indent=2, ensure_ascii=False))
            rebuild_manifest(sources)

    SOURCES.write_text(json.dumps(sources, indent=2, ensure_ascii=False))
    rebuild_manifest(sources)
    print(
        f"\nDone. {accepted} images total "
        f"({accepted - len([s for s in sources if s['idx'] in LEGACY_CATEGORY])} new this run), "
        f"{downloads} downloads attempted. manifest.json + sources.json written."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
