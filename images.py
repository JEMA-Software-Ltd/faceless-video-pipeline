#!/usr/bin/env python3
"""
images.py — stage 3 of 6.

For each chunk, works out how many images it needs (one per 8-12s of that
chunk's MEASURED audio duration), searches Pexels (primary) then Pixabay
(fallback) on the chunk's keywords, and downloads landscape stills into
images/. Records the downloaded paths in chunks.json.

    python3 images.py --date 2026-07-26
    python3 images.py --force     # re-download even chunks already satisfied

Only the official Pexels / Pixabay search APIs are used — no scraping.
Re-runnable: a chunk with enough existing images on disk is skipped unless
--force. Depends on tts.py having written each chunk's `duration`.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from urllib.parse import urlparse

import common


PEXELS_SEARCH = "https://api.pexels.com/v1/search"
PIXABAY_SEARCH = "https://pixabay.com/api/"


def images_needed(duration: float, cfg: dict) -> int:
    """Number of images for a chunk: aim for one image per 8-12s of narration.

    Picks the count whose per-image on-screen time sits inside the [min, max]
    band, or — for durations where no split fits (e.g. ~15s: 1 image is 15s but
    2 is 7.5s) — the count whose per-image time is *closest* to the band. Ties
    keep the smaller count (fewer image fetches). Never fewer than 1.
    """
    vid = cfg.get("video", {})
    lo = float(vid.get("min_seconds_per_image", 8))
    hi = float(vid.get("max_seconds_per_image", 12))
    if duration <= 0:
        return 1

    def penalty(per: float) -> float:
        if per > hi:
            return per - hi     # too long / stale
        if per < lo:
            return lo - per     # too fast / flashing
        return 0.0

    # Consider 1 .. enough images that each would dip below `lo`.
    import math
    max_n = max(1, math.ceil(duration / lo))
    best_n, best_pen = 1, penalty(duration)
    for n in range(2, max_n + 1):
        pen = penalty(duration / n)
        if pen < best_pen - 1e-9:  # strictly better; ties keep the smaller n
            best_n, best_pen = n, pen
    return best_n


def _ext_from_url(url: str, default: str = ".jpg") -> str:
    path = urlparse(url).path
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if path.lower().endswith(ext):
            return ext
    return default


def query_pexels(keyword: str, cfg: dict, log) -> list[dict]:
    """Return normalised candidates: [{id, url, source}]. Empty list on any
    problem (caller falls back to Pixabay)."""
    api_key = common.get_env("PEXELS_API_KEY", required=False)
    if not api_key:
        return []
    img_cfg = cfg.get("images", {})
    params = {
        "query": keyword,
        "per_page": int(img_cfg.get("per_page", 20)),
        "orientation": img_cfg.get("orientation", "landscape"),
    }
    try:
        resp = common.http_request("GET", PEXELS_SEARCH, logger=log,
                                    headers={"Authorization": api_key}, params=params)
    except common.HTTPError as exc:
        log.warning("Pexels query %r failed: %s", keyword, exc)
        return []
    if resp.status_code != 200:
        log.warning("Pexels query %r -> %d", keyword, resp.status_code)
        return []
    out = []
    # Response shape: {"photos": [{"id":..., "src": {"original","large2x",...}}]}
    for photo in resp.json().get("photos", []):
        src = photo.get("src", {})
        # large2x (~1880px wide) is plenty for 1080p; fall back to original.
        url = src.get("large2x") or src.get("original") or src.get("large")
        if url:
            out.append({"id": f"pexels-{photo.get('id')}", "url": url, "source": "pexels"})
    return out


def query_pixabay(keyword: str, cfg: dict, log) -> list[dict]:
    api_key = common.get_env("PIXABAY_API_KEY", required=False)
    if not api_key:
        return []
    img_cfg = cfg.get("images", {})
    params = {
        "key": api_key,
        "q": keyword,
        "image_type": "photo",
        "orientation": "horizontal",  # Pixabay's term for landscape.
        "per_page": int(img_cfg.get("per_page", 20)),
        "min_width": int(img_cfg.get("min_width", 1920)),
        "min_height": int(img_cfg.get("min_height", 1080)),
        "safesearch": "true",
    }
    try:
        resp = common.http_request("GET", PIXABAY_SEARCH, logger=log, params=params)
    except common.HTTPError as exc:
        log.warning("Pixabay query %r failed: %s", keyword, exc)
        return []
    if resp.status_code != 200:
        log.warning("Pixabay query %r -> %d", keyword, resp.status_code)
        return []
    out = []
    # Response shape: {"hits": [{"id":..., "largeImageURL":..., "webformatURL":...}]}
    for hit in resp.json().get("hits", []):
        url = hit.get("largeImageURL") or hit.get("webformatURL")
        if url:
            out.append({"id": f"pixabay-{hit.get('id')}", "url": url, "source": "pixabay"})
    return out


def gather_candidates(keywords: list[str], need: int, cfg: dict, log,
                      used_ids: set) -> list[dict]:
    """Build a de-duplicated candidate list big enough to fill `need` slots,
    Pexels first then Pixabay, skipping ids already used by earlier chunks."""
    pool: list[dict] = []
    seen_ids: set[str] = set()

    def add(cands: list[dict]):
        for c in cands:
            if c["id"] in seen_ids or c["id"] in used_ids:
                continue
            seen_ids.add(c["id"])
            pool.append(c)

    # Round 1: Pexels across all keywords.
    for kw in keywords:
        add(query_pexels(kw, cfg, log))
        if len(pool) >= need:
            return pool
    # Round 2: Pixabay fallback across all keywords.
    for kw in keywords:
        add(query_pixabay(kw, cfg, log))
        if len(pool) >= need:
            return pool
    return pool


def main() -> int:
    parser = common.build_arg_parser("Stage 3: fetch stock images per chunk.")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even chunks that already have enough images.")
    args = parser.parse_args()
    ctx = common.StageContext(args, "images")
    log = ctx.log
    ctx.ensure_dirs()

    data = common.read_chunks(ctx.work_dir)
    chunks = data.get("chunks", [])
    if not chunks:
        common.fail(log, "No chunks in chunks.json — run script.py first.")

    images_dir = ctx.work_dir / "images"
    used_ids: set[str] = set()  # avoid repeating the same photo across chunks
    problems = 0

    for chunk in chunks:
        idx = chunk["index"]
        duration = chunk.get("duration")
        if not duration:
            common.fail(log, f"chunk {idx:03d} has no measured duration — run tts.py first.")

        need = images_needed(duration, ctx.config)

        # Skip if already satisfied (files present on disk).
        existing = chunk.get("images", [])
        if not args.force and len(existing) >= need and all(
            (ctx.work_dir / p).exists() for p in existing
        ):
            log.info("chunk %03d: %d image(s) already present — skipping", idx, len(existing))
            for p in existing:
                used_ids.add(Path(p).stem)  # best-effort de-dup carry-over
            continue

        keywords = chunk.get("keywords") or ["personal finance"]
        log.info("chunk %03d: need %d image(s) for %.1fs; keywords: %s",
                 idx, need, duration, ", ".join(keywords))

        candidates = gather_candidates(keywords, need, ctx.config, log, used_ids)
        if not candidates:
            log.error("chunk %03d: NO images found on Pexels or Pixabay for %s",
                      idx, keywords)
            problems += 1
            chunk["images"] = []
            common.write_chunks(ctx.work_dir, data)
            continue

        downloaded: list[str] = []
        for slot, cand in enumerate(candidates[:need]):
            ext = _ext_from_url(cand["url"])
            dest = images_dir / f"{idx:03d}_{slot}{ext}"
            try:
                common.download_file(cand["url"], dest, logger=log)
            except common.HTTPError as exc:
                log.warning("chunk %03d slot %d: download failed (%s)", idx, slot, exc)
                continue
            used_ids.add(cand["id"])
            downloaded.append(str(dest.relative_to(ctx.work_dir)))
            log.info("chunk %03d: %s <- %s (%s)", idx, dest.name, cand["source"], cand["id"])

        if not downloaded:
            log.error("chunk %03d: found candidates but all downloads failed", idx)
            problems += 1
        elif len(downloaded) < need:
            log.warning("chunk %03d: wanted %d image(s), got %d (will hold each longer)",
                        idx, need, len(downloaded))

        chunk["images"] = downloaded
        common.write_chunks(ctx.work_dir, data)

    common.write_chunks(ctx.work_dir, data)
    if problems:
        common.fail(log, f"{problems} chunk(s) ended with no usable images — "
                         f"fix keywords or API keys before rendering.")
    total = sum(len(c.get("images", [])) for c in chunks)
    log.info("Images done: %d file(s) across %d chunk(s).", total, len(chunks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
