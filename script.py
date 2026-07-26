#!/usr/bin/env python3
"""
script.py — stage 1 of 6.

Reads a hand-written markdown script (scripts/<date>.md), splits it into
paragraph-sized narration chunks, asks Gemini for a few concrete stock-image
search keywords per chunk, and writes work/<date>/chunks.json.

This stage does NOT generate scripts — you write those by hand. It only parses
and enriches them.

    python3 script.py                 # uses scripts/<today>.md
    python3 script.py --date 2026-07-26
    python3 script.py --script path/to.md
    python3 script.py --dry-run       # first N chunks, each truncated

chunks.json shape after this stage:
    {
      "date": "...", "source": "...", "dry_run": false,
      "title": "...", "description": "...", "tags": [...],
      "chunks": [ {"index": 0, "text": "...", "keywords": ["...", ...]}, ... ]
    }
Later stages append audio_file/duration/images to each chunk.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import common


# --- markdown parsing -------------------------------------------------------

_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")


def parse_markdown(text: str) -> dict:
    """Return {title, description, tags, paragraphs}. Very small, forgiving
    parser: paragraphs are blank-line-separated blocks; the first ATX heading
    is the title; optional `<!-- description: ... -->` and `<!-- tags: a, b -->`
    comments feed the upload metadata."""
    description = ""
    tags: list[str] = []

    # Pull metadata out of HTML comments before stripping them.
    for body in _COMMENT_RE.findall(text):
        body = body.strip()
        low = body.lower()
        if low.startswith("description:"):
            description = body.split(":", 1)[1].strip()
        elif low.startswith("tags:"):
            tags = [t.strip() for t in body.split(":", 1)[1].split(",") if t.strip()]
    text = _COMMENT_RE.sub("", text)

    title = ""
    body_lines: list[str] = []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if not title:
                title = m.group(1).strip()
            # Drop all headings from the narration body.
            continue
        body_lines.append(line)

    # Blank-line-separated paragraphs; collapse internal wrapping to spaces so
    # the TTS reads each paragraph as one flowing block.
    blocks = re.split(r"\n\s*\n", "\n".join(body_lines))
    paragraphs = []
    for block in blocks:
        collapsed = " ".join(seg.strip() for seg in block.splitlines() if seg.strip())
        collapsed = collapsed.strip()
        if collapsed:
            paragraphs.append(collapsed)

    return {"title": title, "description": description, "tags": tags,
            "paragraphs": paragraphs}


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate to <= max_chars, preferring a sentence then word boundary, so
    dry-run narration still sounds like a complete thought."""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    # Prefer the last sentence end inside the window.
    for end in (". ", "! ", "? "):
        idx = window.rfind(end)
        if idx >= max_chars * 0.4:  # don't truncate absurdly short
            return window[: idx + 1].strip()
    # Otherwise the last word boundary.
    idx = window.rfind(" ")
    return (window[:idx] if idx > 0 else window).strip() + "."


# --- Gemini keyword extraction ---------------------------------------------

def extract_keywords_gemini(paragraphs: list[str], cfg: dict, log) -> list[list[str]]:
    """One batched Gemini call: returns a list of keyword lists, aligned to
    `paragraphs`. Raises on failure; caller decides whether to fall back."""
    from google import genai  # imported lazily so doctor/other stages don't need it

    api_key = common.get_env("GEMINI_API_KEY")
    model = cfg.get("gemini", {}).get("model", "gemini-2.0-flash")
    n = int(cfg.get("gemini", {}).get("keywords_per_chunk", 3))

    numbered = "\n".join(f"[{i}] {p}" for i, p in enumerate(paragraphs))
    prompt = (
        "You pick concrete, visually-searchable stock-photo keywords for an "
        "educational UK personal-finance slideshow video.\n"
        f"For each numbered paragraph below, give {max(2, n)} short keyword "
        "phrases (1-3 words each) naming imagery a stock library would return "
        "well — concrete nouns and scenes (e.g. 'piggy bank', 'london terraced "
        "houses', 'coins jar', 'person budgeting laptop'). Avoid abstract words "
        "like 'wealth' or 'success'.\n"
        "Return ONLY a JSON array where element i is an array of strings for "
        "paragraph i. No prose, no code fences.\n\n"
        f"{numbered}"
    )

    client = genai.Client(api_key=api_key)
    # NOTE (verify tomorrow): with google-genai, the plain text response is
    # `response.text`. We strip any accidental ```json fences before parsing.
    last_exc = None
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            raw = (response.text or "").strip()
            raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError("expected a JSON array")
            out: list[list[str]] = []
            for item in data:
                if isinstance(item, list):
                    out.append([str(k).strip() for k in item if str(k).strip()][:max(2, n)])
                else:
                    out.append([str(item).strip()])
            # Pad/trim to match paragraph count.
            while len(out) < len(paragraphs):
                out.append([])
            return out[: len(paragraphs)]
        except Exception as exc:  # network / parse / API error
            last_exc = exc
            log.warning("Gemini keyword attempt %d/3 failed: %s", attempt, exc)
    raise RuntimeError(f"Gemini keyword extraction failed: {last_exc}")


def heuristic_keywords(paragraph: str, n: int) -> list[str]:
    """Offline fallback: pick a few salient words. Deliberately crude — it only
    exists so a transient Gemini outage during a nightly cron degrades to a
    still-renderable video instead of failing the whole run."""
    stop = {
        "the", "and", "you", "your", "that", "this", "with", "for", "are", "but",
        "not", "have", "just", "into", "them", "they", "will", "what", "when",
        "money", "budget", "pounds", "month", "every", "which", "does", "from",
        "about", "than", "even", "much", "more", "most", "than", "these", "those",
    }
    words = re.findall(r"[a-zA-Z]{4,}", paragraph.lower())
    seen, picked = set(), []
    for w in words:
        if w in stop or w in seen:
            continue
        seen.add(w)
        picked.append(w)
        if len(picked) >= n:
            break
    return picked or ["personal finance"]


# --- main -------------------------------------------------------------------

def main() -> int:
    parser = common.build_arg_parser("Stage 1: parse script into chunks + keywords.")
    parser.add_argument("--script", default=None,
                        help="Path to the markdown script (default: scripts/<date>.md).")
    args = parser.parse_args()
    ctx = common.StageContext(args, "script")
    log = ctx.log
    ctx.ensure_dirs()

    script_path = Path(args.script) if args.script else (common.SCRIPTS_DIR / f"{ctx.date}.md")
    if not script_path.exists():
        common.fail(log, f"Script not found: {script_path}. Write it first "
                         f"(see scripts/example.md).")

    parsed = parse_markdown(script_path.read_text(encoding="utf-8"))
    paragraphs = parsed["paragraphs"]
    if not paragraphs:
        common.fail(log, f"No narration paragraphs found in {script_path}.")

    log.info("Parsed %d paragraph(s) from %s", len(paragraphs), script_path.name)

    # Dry-run: fewer, shorter chunks so the whole chain runs in ~2 minutes.
    if ctx.dry_run:
        dr = ctx.config.get("dry_run", {})
        n = int(dr.get("num_chunks", 3))
        cap = int(dr.get("truncate_chars", 220))
        paragraphs = [truncate_text(p, cap) for p in paragraphs[:n]]
        log.info("DRY-RUN: using %d chunk(s), truncated to <=%d chars each",
                 len(paragraphs), cap)

    # Keywords via Gemini, with an offline heuristic fallback on hard failure.
    n_kw = int(ctx.config.get("gemini", {}).get("keywords_per_chunk", 3))
    try:
        keyword_lists = extract_keywords_gemini(paragraphs, ctx.config, log)
    except Exception as exc:
        log.error("Gemini unavailable (%s) — falling back to heuristic keywords. "
                  "Image relevance will be lower for this run.", exc)
        keyword_lists = [heuristic_keywords(p, n_kw) for p in paragraphs]

    # Ensure every chunk has at least one keyword.
    for i, kws in enumerate(keyword_lists):
        if not kws:
            keyword_lists[i] = heuristic_keywords(paragraphs[i], n_kw)

    chunks = [
        {"index": i, "text": p, "keywords": keyword_lists[i]}
        for i, p in enumerate(paragraphs)
    ]

    data = {
        "date": ctx.date,
        "source": str(script_path),
        "dry_run": ctx.dry_run,
        "title": parsed["title"] or f"{ctx.date} — personal finance",
        "description": parsed["description"],
        "tags": parsed["tags"],
        "chunks": chunks,
    }
    common.write_chunks(ctx.work_dir, data)

    log.info("Wrote %s with %d chunk(s).", common.chunks_path(ctx.work_dir), len(chunks))
    for c in chunks:
        log.info("  chunk %03d: %s", c["index"], ", ".join(c["keywords"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
