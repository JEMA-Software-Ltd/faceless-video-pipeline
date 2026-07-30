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
    "regulator": ("government office building", "regulator office sign board", "official desk nameplate"),
    "regulated": ("regulation rulebook desk", "official seal stamp", "compliance binder shelf"),
    "fca": "london office building",
    "authority": "civic building columns",
    "authorised": "official documents desk",
    "watchdog": "inspector clipboard site",
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
    "money": ("banknotes counting hands", "cash on counter", "wallet with notes", "notes and coins pile"),
    "pounds": ("british pound coins", "sterling notes fan", "coins stacked column"),
    "cash": "bundled cash notes",
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
    # "report" is polysemous and the two senses want opposite imagery. In
    # regulatory writing it usually means a FILING — "eleven money laundering
    # reports were raised", "a Suspicious Activity Report" — not a bound
    # document. It previously mapped to "thick report document", which on a
    # financial-crime script produced library-shelf imagery for what is actually
    # a compliance form. Mapped to the filing sense, which dominates this
    # table's domain; "investigation" above still covers a published report.
    # Spread across distinct-but-related imagery rather than one phrase: on a
    # script about suspicion filings the whole family fires in ~13 of 46 chunks,
    # and pointing them all at one cue repeats a single shot type all video.
    "report": "compliance paperwork desk",
    "reports": "stack of paper forms",
    "reported": "form being completed",
    "reporting": "form being completed",
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
    "landlords": "commercial property owners",
    "freeholder": "commercial property owner",
    "freeholders": "commercial property owners",
    "leaseholder": "property lease documents",
    "leaseholders": "property lease documents",
    "tenant": "empty retail unit",
    "tenants": "retail tenant meeting",
    "lease": "lease contract signing",
    "leases": "lease contract signing",
    "sub lease": "stacked lease documents",
    "licence": "business licence document",
    "licences": "business licence documents",
    "licensees": "business licence documents",
    "rent": "for lease sign",
    "shop front": "shop front exterior",
    "shopfront": "retail storefront exterior",
    "storefront": "retail storefront exterior",
    "storefront signage": "shopfront signage",
    "retail unit": "vacant retail unit",
    "retail units": "vacant retail units",
    "vacant unit": "vacant shop unit",
    "vacant units": "vacant shop units",
    "empty shop": "vacant shop interior",
    "high street": "british high street scene",
    "shutters": "closed shop shutters",
    "forecourt": "petrol station forecourt",
    "restaurant": "fast food counter",
    "restaurants": "fast food counter",
    "bakery": "bakery bread counter",
    "burger": "burger and fries",
    "kitchen": "commercial kitchen staff",
    "counter": "shop service counter",
    "till": "cash register till",
    "cash register": "cash register counter",
    "shops": ("british high street shops", "shopfront windows row", "retail units street"),
    "shop": ("shop front exterior", "retail counter interior", "storefront signage"),
    "sweet shop": "colourful sweet shop",
    "sweet shops": "colourful sweet shops",
    "candy store": "candy store shelves",
    "candy stores": "candy store row",
    "sweets": ("packets of sweets", "sweet shop shelves", "colourful candy jars"),
    "confectionery": "confectionery display shelves",
    "souvenir": "souvenir shop display",
    "souvenirs": "london souvenir display",
    "vape": "vape shop display",
    "vapes": "vape products display",
    "price labels": "shop shelf price labels",
    "price tags": "missing price tags",
    "lit window": "lit shop window",
    "dark windows": "dark shop windows",
    "tourist": "tourist shopping london",
    "tourists": "tourists oxford street",
    "oxford street": "oxford street london",
    "footfall": "busy shopping street",
    "pandemic": "empty pandemic high street",
    "retail frontage": "retail storefront row",
    "premises": "retail premises exterior",
    "trade": "retail trade counter",
    "paperwork": "business paperwork desk",
    "occupier": "shop tenant paperwork",
    "occupiers": "shop tenant paperwork",
    "intermediary": "business contract meeting",
    # Costs, margins, supply chain
    "margin": "calculator and receipts",
    "margins": "calculator and receipts",
    "wages": "shop staff serving",
    "staff": "shop staff serving",
    "employment": "shop staff serving",
    "packaging": "cardboard packaging boxes",
    "supply": ("warehouse logistics", "loading dock delivery", "stock room shelving"),
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
    "prices": ("shelf price labels", "supermarket price sign", "till receipt roll"),
    "pricing": ("price list document", "shelf price labels", "price tag label"),
    "customers": ("shoppers at checkout", "person paying counter", "service desk waiting"),
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
    "price": ("price tag label", "supermarket price sign", "receipt close up"),
    "business rates": "business rates bill",
    "rates bill": "commercial rates invoice",
    "rateable value": "property valuation document",
    "tax liability": "tax bill paperwork",
    "tax enforcement": "tax enforcement notice",
    "taxing authority": "tax office exterior",
    "liability": "legal liability documents",
    "no assets": "empty company accounts",
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
    "food": ("grocery shopping basket", "supermarket food shelves", "fresh produce display"),
    "multipack": "supermarket aisle trolley",
    "fridge": "kitchen fridge open",
    "boiler": "boiler repair engineer",
    "uniform": "school uniform child",
    "postcode": "uk residential street",
    "poverty": "empty kitchen cupboard",
    "car": "parked car residential street",
    # Financial crime / anti-money-laundering
    "banknotes": "stacked banknote bundles",
    "deposit": "cash deposit counter",
    "deposits": "cash deposit counter",
    "branch": "bank branch interior",
    "branches": "bank branch interior",
    "vault": "bank vault door",
    "courier": "security van transfer",
    "haulier": "lorry haulage depot",
    "jeweller": "jewellery shop window",
    "jewellery": "jewellery shop window",
    "gold": "gold bars bullion",
    "laundering": "money laundering concept",
    "launder": "money laundering concept",
    "suspicion": "red flag warning sign",
    "suspicious": "cctv security camera",
    "alert": "warning alert screen",
    "alerts": "warning alert screen",
    "monitoring": "monitoring screens wall",
    "compliance": "compliance officer desk",
    "trading standards": "trading standards inspection",
    "bailiff": "bailiff at shop doorway",
    "bailiffs": "bailiffs seizing goods",
    "raid": "police raid shop",
    "raids": "police raid storefront",
    "raided": "police outside shop",
    "raiding": "trading standards raid",
    "seized": "seized goods evidence",
    "filing": "document filing tray",
    "filed": "document filing tray",
    "queue": "long queue people",
    "backlog": "overflowing paper tray",
    "register": "company register ledger",
    "shell company": "abandoned office room",
    "shell companies": "empty registered office",
    "counterfeit": "counterfeit goods inspection",
    "fake": "fake goods evidence",
    "goods": ("boxed retail goods", "shop merchandise shelves", "seized goods table"),
    "merchandise": "shop merchandise shelves",
    "stock": ("stock room shelves", "boxed shop stock", "retail inventory shelves"),
    "refund": "cash refund counter",
    "police": "police outside shop",
    "officers": "inspection officers shop",
    "basement": "dark basement room",
    "hidden panel": "hidden wall panel",
    "secret room": "concealed basement room",
    "concealed room": "hidden storage room",
    "verification": "identity document check",
    "seizure": "police evidence bags",
    "freeze": "frozen bank card",
    "prosecution": "crown court exterior",
    "sentencing": "crown court exterior",
    "conviction": "prison corridor",
    "guilty": "courtroom dock",
    "fined": "penalty notice paperwork",
    "solicitors": "professional office meeting",
    "accountants": "professional office meeting",
    # Water, utilities and infrastructure. NB bare "water" is NOT a trigger —
    # on a script about water it appears in 48 of 166 chunks, and one mapping
    # for it would repeat ~48 times. The specific terms below carry the imagery.
    "reservoir": "reservoir dam water",
    "pipe": "underground water pipe",
    "pipes": "underground water pipe",
    "pipework": "industrial pipework valves",
    "sewer": "sewer tunnel brick",
    "sewage": "sewage treatment tanks",
    "treatment": "water treatment works",
    "river": "english river bank",
    "rivers": "english river bank",
    "tap": "running kitchen tap",
    "taps": "running kitchen tap",
    "drain": "street drain grate",
    "drains": "street drain grate",
    "overflow": "storm water outfall",
    "storm": "storm water outfall",
    "pollution": "polluted river foam",
    "leak": "leaking pipe water",
    "leaks": "leaking pipe water",
    "pumping": "pumping station machinery",
    "victorian": "victorian brick engineering",
    "infrastructure": "civil engineering works",
    "network": "pipeline network trench",
    "utility": "utility works street",
    "utilities": "utility works street",
    "ofwat": "regulator office sign",
    # Privatisation / corporate finance
    "privatisation": "stock exchange floor",
    "privatised": "stock exchange floor",
    "flotation": "share certificate document",
    "prospectus": "glossy prospectus cover",
    "shareholder": ("shareholder meeting hall", "annual report cover", "conference audience seats"),
    "shareholders": ("investor meeting room", "annual report cover", "conference audience seats"),
    "dividend": ("dividend cheque payment", "money transfer screen", "envelope of cash"),
    "dividends": ("payout cheque desk", "money transfer screen", "envelope of cash"),
    "gearing": "weighted scales metal",
    "creditor": "creditor demand notice",
    "creditors": "creditor demand notice",
    "bondholder": "bond certificate paper",
    "bondholders": "bond certificate paper",
    "nationalisation": "government building flag",
    "monopoly": "single road no exit",
    "equity": "share certificate document",
    "borrowed": "loan paperwork signature",
    "debt": ("debt spiral paperwork", "red account statement", "calculator debt figures"),
    # Retail floor / groceries
    "shelf": "supermarket shelf stocked",
    "shelves": "supermarket shelf stocked",
    "trolley": "shopping trolley aisle",
    "aisle": "long store aisle",
    "grocery": "grocery store front",
    "groceries": "grocery store front",
    "chocolate": "chocolate bar squares",
    "biscuit": "biscuits on plate",
    "biscuits": "biscuits on plate",
    "packet": "packaged goods shelf",
    # Logistics / freight
    "pallet": "stacked warehouse pallets",
    "pallets": "stacked warehouse pallets",
    "lorry": "articulated lorry motorway",
    "lorries": "articulated lorry motorway",
    "freight": "freight containers stacked",
    "container": "freight containers stacked",
    "containers": "freight containers stacked",
    "port": "container port cranes",
    "wholesaler": "wholesale cash carry",
    "wholesalers": "wholesale cash carry",
    "retailer": "high street retailer",
    "retailers": "high street retailers",
    "importer": "import documents stamp",
    "imports": "import documents stamp",
    "imported": "import documents stamp",
    "exports": "export cargo loading",
    # Borders / trade
    "border": "border checkpoint barrier",
    "borders": "border checkpoint barrier",
    "customs": "customs inspection desk",
    "tariffs": "customs duty paperwork",
    "single market": "european flags brussels",
    "commission": "eu commission building",
    "brexit": "union flag eu flag",
    "arbitrage": "two price tags compared",
    # Competition law
    "competition law": "law books and gavel",
    "antitrust": "law books and gavel",
    "cartel": "handshake under table",
    "dominance": "chess king piece",
    # Generic scene-setting
    "london": "city of london skyline",
    "kent": "english countryside town",
    "britain": ("london skyline", "union flag building", "british high street"),
    "british": ("british high street", "union flag building", "london skyline"),
    "people": ("crowd city street", "busy pavement commuters", "queue outside building"),
    "public": ("public meeting audience", "town hall interior", "notice on railings"),
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
    "aerial suburban housing",
    "motorway aerial view",
    "construction site workers",
    "countryside fields england",
    "town hall steps",
    "boardroom empty chairs",
    "business handshake deal",
    "calculator and pen",
    "filing cabinets row",
    "engineer in hard hat",
    "public notice board",
    "bridge over river",
    "terraced houses street",
    "city rooftops dusk",
    "office window blinds",
    "coins on wooden table",
    "printed spreadsheet pages",
    "warehouse loading bay",
    "empty meeting corridor",
    "clocking in machine",
]

def _variants(value) -> tuple[str, ...]:
    """A trigger's imagery may be a single phrase or a tuple of alternatives.

    Repetition scales with script length: a trigger present in 17% of chunks is
    fine across 40 chunks and becomes 20+ identical image cues across 170. Giving
    the frequent triggers several related phrases, rotated by chunk index, keeps
    long scripts visually varied without inventing unrelated imagery."""
    return (value,) if isinstance(value, str) else tuple(value)


def _assert_no_pool_overlap() -> None:
    """Enforce the invariant documented above. Cheap, and it fails loudly at
    import rather than silently skewing a 20-minute video's imagery."""
    topic = {p for v in _TOPIC_IMAGERY.values() for p in _variants(v)}
    overlap = sorted(topic & set(_GENERIC_IMAGERY))
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
    for trigger, value in _TOPIC_IMAGERY.items():
        match = re.search(rf"\b{re.escape(trigger)}\b", lowered)
        if match:
            # Multi-variant triggers rotate on chunk index, so a word that
            # recurs throughout a long script does not repeat one image cue.
            variants = _variants(value)
            hits.append((match.start(), variants[index % len(variants)]))
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
