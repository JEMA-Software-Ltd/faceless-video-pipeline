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


# Trigger word (matched whole-word, case-insensitive) -> a short phrase a stock
# library actually returns good landscape photos for. The point is that the
# narration's own vocabulary is usually abstract ("perimeter", "permissions",
# "obvious"), which searches terribly; these map that vocabulary onto something
# photographable. Keep phrases to 2-3 words — long queries return thin results.
_TOPIC_IMAGERY = {
    # Regulation / institutions
    "regulator": "government office building",
    "regulated": "government office building",
    "fca": "london office building",
    "authority": "government office building",
    "authorised": "official documents desk",
    "watchdog": "government office building",
    "treasury": "whitehall london",
    "parliament": "houses of parliament",
    "government": "whitehall london",
    "perimeter": "locked gate fence",
    "permissions": "official stamp document",
    "compensation": "insurance paperwork",
    "scheme": "official paperwork desk",
    # Money / products
    "savings": "savings jar coins",
    "saver": "piggy bank savings",
    "isa": "savings account statement",
    "bank": "bank branch entrance",
    "bond": "financial certificate document",
    "minibond": "financial certificate document",
    "minibonds": "financial certificate document",
    "interest": "rising graph chart",
    "investment": "stock market chart",
    "investor": "anxious person letter",
    "investors": "anxious person letter",
    "pension": "retired couple paperwork",
    "retired": "elderly person worried",
    "money": "banknotes counting hands",
    "pounds": "british pound coins",
    "cash": "banknotes counting hands",
    "capital": "city of london skyline",
    "loan": "loan agreement signing",
    "loans": "loan agreement signing",
    "lending": "loan agreement signing",
    "property": "uk property development",
    "portfolio": "financial documents desk",
    # Selling / marketing
    "advert": "online advertising screen",
    "advertised": "online advertising screen",
    "promotions": "marketing website screen",
    "website": "laptop screen browsing",
    "websites": "laptop screen browsing",
    "comparison": "laptop screen browsing",
    "brochure": "glossy brochure desk",
    "misleading": "fine print magnifying glass",
    "charges": "fine print magnifying glass",
    # Collapse / consequences
    "administration": "closed business sign",
    "administrators": "cleared office boxes",
    "collapse": "falling chart graph",
    "collapsed": "falling chart graph",
    "failed": "falling chart graph",
    "fraud": "handcuffs on documents",
    "ponzi": "house of cards",
    "arrest": "police uk street",
    "arrests": "police uk street",
    "court": "courtroom interior",
    "judgment": "judge gavel",
    "judge": "judge gavel",
    "investigation": "magnifying glass documents",
    "report": "thick report document",
    "whistleblower": "person silhouette window",
    "warnings": "warning sign red",
    "victims": "worried person kitchen table",
    "taxpayer": "banknotes counting hands",
    # Funds / asset management. Deliberately omits ubiquitous words like "fund"
    # and "shares" — a trigger that fires in almost every paragraph just becomes
    # a new source of repetition. These are the distinctive terms.
    "liquidity": "bank vault door",
    "liquid": "bank vault door",
    "illiquid": "locked safe deposit",
    "suspension": "closed sign door",
    "suspended": "closed sign door",
    "redemption": "bank withdrawal counter",
    "redemptions": "bank withdrawal counter",
    "withdraw": "cash machine queue",
    "unquoted": "empty boardroom table",
    "unlisted": "empty boardroom table",
    "listed": "stock exchange screen",
    "equity": "stock exchange screen",
    "stake": "business handshake",
    "valuation": "calculator spreadsheet desk",
    "factsheet": "printed report pages",
    "platform": "laptop investment platform",
    "dealing": "trading floor screens",
    "trading": "trading floor screens",
    "trustee": "signing official document",
    "depositary": "signing official document",
    "tribunal": "courtroom benches",
    "redress": "compensation cheque desk",
    "breach": "broken glass warning",
    "breached": "broken glass warning",
    "mismatch": "unbalanced scales",
    "spiral": "spiral staircase down",
    "manager": "fund manager office",
    "council": "town hall building",
    "performance": "line chart screen",
    "strategy": "chess board pieces",
    # Franchising / retail operations / real estate. Same rule as above: skip
    # words so common they would fire in nearly every paragraph.
    "franchise": "shop front signage",
    "franchising": "shop front signage",
    "franchisee": "small business owner",
    "franchisees": "small business owner",
    "franchisor": "corporate headquarters building",
    "royalty": "contract signing pen",
    "royalties": "contract signing pen",
    "landlord": "estate agent sign",
    "tenant": "empty retail unit",
    "lease": "lease contract signing",
    "leases": "lease contract signing",
    "rent": "for lease sign",
    "forecourt": "petrol station forecourt",
    "restaurant": "fast food counter",
    "restaurants": "fast food counter",
    "bakery": "bakery bread counter",
    "burger": "burger and fries",
    "kitchen": "commercial kitchen staff",
    "counter": "shop service counter",
    "till": "cash register till",
    "shops": "british high street shops",
    "shop": "british high street shops",
    # Costs, margins, supply chain
    "margin": "calculator and receipts",
    "margins": "calculator and receipts",
    "wages": "shop staff serving",
    "staff": "shop staff serving",
    "employment": "shop staff serving",
    "packaging": "cardboard packaging boxes",
    "supply": "warehouse logistics",
    "distribution": "delivery lorry depot",
    "warehouse": "warehouse shelves stock",
    "manufacturing": "factory production line",
    "integrated": "factory production line",
    "expenditure": "construction site crane",
    "capital expenditure": "construction site crane",
    "energy": "electricity meter dial",
    "beef": "butcher raw meat",
    "flour": "flour dough baking",
    "ovens": "industrial bakery oven",
    "vans": "delivery van street",
    "prices": "price tag label",
    "pricing": "price tag label",
    "customers": "customer queue counter",
    "volumes": "busy shop queue",
    "leverage": "seesaw balance",
    "brand": "illuminated brand sign",
    # Household bills / energy / utilities
    "prepayment": "prepayment meter key",
    "meter": "utility meter closeup",
    "meters": "utility meter closeup",
    "standing charge": "energy bill statement",
    "tariff": "energy bill statement",
    "electricity": "electricity pylon sky",
    "gas": "gas hob flame",
    "ofgem": "energy regulator office",
    "bills": "unpaid bills envelopes",
    "bill": "unpaid bills envelopes",
    "direct debit": "bank statement paper",
    # Insurance / credit / paying over time
    # NB "premium" is deliberately NOT a trigger. In this domain it usually means
    # a surcharge ("the poverty premium"), not an insurance premium, so mapping
    # it to policy imagery is wrong more often than right — and as the subject
    # word of a whole script it fires everywhere and dominates the imagery.
    "insurance": "car insurance documents",
    "instalment": "monthly payment calendar",
    "instalments": "monthly payment calendar",
    "doorstep": "front door step",
    "rent to own": "furniture showroom sofa",
    "catalogue": "mail order catalogue",
    "subprime": "credit card payment",
    "default": "final demand letter",
    "buffer": "empty purse coins",
    "illiquidity": "empty wallet hands",
    "advance": "calendar circled date",
    "discount": "sale discount sign",
    "price": "price tag label",
    # Benefits / the state
    "claimant": "job centre entrance",
    "claimants": "job centre entrance",
    "deduction": "payslip deductions",
    "deductions": "payslip deductions",
    "allowance": "government benefit letter",
    "benefit": "government benefit letter",
    "levy": "tax form paperwork",
    "regressive": "weighing scales tilted",
    # Shops / food / the domestic scene
    "corner shop": "corner shop exterior",
    "convenience": "convenience store aisle",
    "supermarket": "supermarket aisle trolley",
    "superstore": "supermarket car park",
    "food": "grocery shopping basket",
    "multipack": "supermarket aisle trolley",
    "fridge": "kitchen fridge open",
    "boiler": "boiler repair engineer",
    "uniform": "school uniform child",
    "postcode": "uk residential street",
    "poverty": "empty kitchen cupboard",
    "car": "parked car residential street",
    # Generic scene-setting
    "london": "city of london skyline",
    "kent": "english countryside town",
    "britain": "london skyline",
    "british": "london skyline",
    "people": "crowd city street",
    "public": "crowd city street",
}

# INVARIANT: no _TOPIC_IMAGERY value may also appear in _GENERIC_IMAGERY.
# A phrase reachable from both a trigger AND the fall-through pool gets selected
# twice as often as anything else and dominates the video. "company" -> "office
# building exterior" was the worst case: on a script about two companies it
# fired in 16 of 49 chunks while the same phrase was also being handed out as
# filler, producing 19 uses of one image cue. Ubiquitous words ("company",
# "firm") are deliberately absent as triggers for the same reason — see the
# note above the funds block. _assert_no_pool_overlap() enforces this at import.

# Rotated (by chunk index) to top up chunks whose text has no trigger, so
# neighbouring chunks don't all land on the same photo.
_GENERIC_IMAGERY = [
    "london financial district",
    "person reading documents",
    "british pound notes",
    "office building exterior",
    "laptop screen finance",
    "worried person paperwork",
    "high street bank",
    "financial charts desk",
    "city commuters london",
    "empty office desks",
    "revolving door office",
    "glass tower windows",
    "newspaper business pages",
    "meeting room chairs",
    "escalator office lobby",
    "rain city pavement",
]

def _assert_no_pool_overlap() -> None:
    """Enforce the invariant documented above. Cheap, and it fails loudly at
    import rather than silently skewing a 20-minute video's imagery."""
    overlap = sorted(set(_TOPIC_IMAGERY.values()) & set(_GENERIC_IMAGERY))
    if overlap:
        raise RuntimeError(
            "keyword tables overlap; these phrases are reachable from both a "
            f"trigger and the fall-through pool and will be over-used: {overlap}")


_assert_no_pool_overlap()

_PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){1,3})\b")


def heuristic_keywords(paragraph: str, n: int, index: int = 0) -> list[str]:
    """Offline fallback used when Gemini is unavailable.

    Deliberately NOT a bag-of-words pick: raw salient words out of this kind of
    narration ("where", "obvious", "weren") are abstract or broken tokens and
    make stock search return nothing usable. Instead:

      1. Map trigger words found in the text onto concrete, photographable
         phrases (_TOPIC_IMAGERY), in order of first appearance.
      2. Top up from a rotating generic pool so every chunk gets `n` queries and
         adjacent chunks don't collide on the same imagery.

    `index` is the chunk index and only affects which generic phrases are used.
    """
    lowered = paragraph.lower()
    picked: list[str] = []

    hits: list[tuple[int, str]] = []
    for trigger, phrase in _TOPIC_IMAGERY.items():
        match = re.search(rf"\b{re.escape(trigger)}\b", lowered)
        if match:
            hits.append((match.start(), phrase))
    hits.sort(key=lambda h: h[0])
    for _, phrase in hits:
        if phrase not in picked:
            picked.append(phrase)
        if len(picked) >= n:
            break

    # Rotate through the generic pool for anything still missing.
    cursor = index
    while len(picked) < max(1, n):
        candidate = _GENERIC_IMAGERY[cursor % len(_GENERIC_IMAGERY)]
        cursor += 1
        if candidate not in picked:
            picked.append(candidate)
        elif cursor - index > len(_GENERIC_IMAGERY):
            break  # pool exhausted; n is larger than the pool

    return picked[:max(1, n)]


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
        keyword_lists = [heuristic_keywords(p, n_kw, i)
                         for i, p in enumerate(paragraphs)]

    # Ensure every chunk has at least one keyword.
    for i, kws in enumerate(keyword_lists):
        if not kws:
            keyword_lists[i] = heuristic_keywords(paragraphs[i], n_kw, i)

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
