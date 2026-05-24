"""
Company Aliases + Sector Tagging
================================
Normalizes "HDFC Bank" / "HDFC" / "HDFC Ltd" to a canonical name and tags
each article with one of 8 sectors used by the newsroom UI.

Sources (in priority order — first match wins):
  1. CURATED_ALIASES        — hand-tuned, top-50 large caps. Authoritative.
  2. company_master.json    — auto-built from PG filings_index + NSE/BSE master + screener industry map.
                              Loaded at import; 4k+ companies; covers the long tail.

The 8 sector chips (BANKING, TECH, AUTO, PHARMA, ENERGY, FMCG, METALS, REALTY)
are stable. Industries not mapped to a chip get sector=None and fall through
to the keyword-based detect_sector() fallback.
"""
import json
import re
from pathlib import Path
from typing import Optional

# ────────────────────────────────────────────────────────────────
# CURATED: top large-caps with hand-tuned aliases. These ALWAYS win
# over the auto-built master — used when a stock has unusual aliases
# the heuristic can't generate ("M&M" for Mahindra & Mahindra, etc.)
# ────────────────────────────────────────────────────────────────
CURATED_ALIASES = {
    # Banking
    "HDFC Bank": ["hdfc bank", "hdfcbank", "hdfc"],
    "ICICI Bank": ["icici bank", "icicibank", "icici"],
    "State Bank of India": ["sbi", "state bank of india", "state bank"],
    "Axis Bank": ["axis bank", "axisbank"],
    "Kotak Mahindra Bank": ["kotak mahindra", "kotak bank", "kotak"],
    "IndusInd Bank": ["indusind bank", "indusind"],
    "Bank of Baroda": ["bank of baroda", "bob"],
    "Punjab National Bank": ["pnb", "punjab national bank"],
    "Bandhan Bank": ["bandhan bank", "bandhan"],
    "Yes Bank": ["yes bank"],

    # Tech / IT
    "Tata Consultancy Services": ["tcs", "tata consultancy services", "tata consultancy"],
    "Infosys": ["infosys", "infy"],
    "Wipro": ["wipro"],
    "HCL Technologies": ["hcl technologies", "hcl tech", "hcltech"],
    "Tech Mahindra": ["tech mahindra", "techm"],
    "LTIMindtree": ["ltimindtree", "lti mindtree", "ltim"],
    "Persistent Systems": ["persistent systems", "persistent"],
    "Coforge": ["coforge"],
    "Mphasis": ["mphasis"],

    # Auto
    "Maruti Suzuki": ["maruti suzuki", "maruti"],
    "Tata Motors": ["tata motors"],
    "Mahindra & Mahindra": ["mahindra & mahindra", "m&m", "mahindra and mahindra"],
    "Bajaj Auto": ["bajaj auto"],
    "Hero MotoCorp": ["hero motocorp", "hero moto"],
    "TVS Motor": ["tvs motor", "tvs motors"],
    "Eicher Motors": ["eicher motors", "eicher"],
    "Ashok Leyland": ["ashok leyland"],

    # Pharma
    "Sun Pharma": ["sun pharma", "sun pharmaceutical", "sun pharmaceuticals"],
    "Dr Reddy's Laboratories": ["dr reddy", "dr. reddy", "dr reddys", "dr. reddy's", "drl"],
    "Cipla": ["cipla"],
    "Divi's Laboratories": ["divi's laboratories", "divis laboratories", "divi's labs", "divis"],
    "Lupin": ["lupin"],
    "Aurobindo Pharma": ["aurobindo pharma", "aurobindo"],
    "Biocon": ["biocon"],
    "Apollo Hospitals": ["apollo hospitals", "apollo hospital"],

    # Energy / Oil & Gas
    "Reliance Industries": ["reliance industries", "reliance", "ril"],
    "Oil and Natural Gas Corporation": ["ongc", "oil and natural gas"],
    "Indian Oil Corporation": ["indian oil", "ioc", "iocl"],
    "Bharat Petroleum": ["bharat petroleum", "bpcl"],
    "Hindustan Petroleum": ["hindustan petroleum", "hpcl"],
    "NTPC": ["ntpc"],
    "Power Grid Corporation": ["power grid corporation", "power grid", "pgcil"],
    "Adani Power": ["adani power"],
    "Tata Power": ["tata power"],

    # FMCG
    "Hindustan Unilever": ["hindustan unilever", "hul"],
    "ITC": ["itc ltd", "itc limited", "itc"],
    "Nestle India": ["nestle india", "nestle"],
    "Britannia Industries": ["britannia industries", "britannia"],
    "Dabur India": ["dabur india", "dabur"],
    "Marico": ["marico"],
    "Godrej Consumer": ["godrej consumer products", "godrej consumer", "gcpl"],
    "Tata Consumer Products": ["tata consumer products", "tata consumer"],

    # Metals
    "Tata Steel": ["tata steel"],
    "JSW Steel": ["jsw steel"],
    "Hindalco Industries": ["hindalco industries", "hindalco"],
    "Vedanta": ["vedanta"],
    "Coal India": ["coal india", "cil"],
    "NMDC": ["nmdc"],
    "Steel Authority of India": ["sail", "steel authority of india"],
    "Jindal Steel & Power": ["jindal steel & power", "jindal steel", "jspl"],

    # Realty
    "DLF": ["dlf"],
    "Godrej Properties": ["godrej properties"],
    "Oberoi Realty": ["oberoi realty"],
    "Prestige Estates": ["prestige estates"],
    "Macrotech Developers": ["macrotech developers", "lodha"],
    "Phoenix Mills": ["phoenix mills"],
    "Brigade Enterprises": ["brigade enterprises"],

    # Adani group
    "Adani Enterprises": ["adani enterprises"],
    "Adani Ports": ["adani ports", "adani port", "apsez"],
    "Adani Green Energy": ["adani green energy", "adani green"],
    "Adani Total Gas": ["adani total gas"],

    # Other large caps
    "Larsen & Toubro": ["larsen & toubro", "l&t", "larsen and toubro"],
    "Asian Paints": ["asian paints"],
    "Bajaj Finance": ["bajaj finance"],
    "Bajaj Finserv": ["bajaj finserv"],
    "Bharti Airtel": ["bharti airtel", "airtel"],
    "UltraTech Cement": ["ultratech cement", "ultratech"],
    "Grasim Industries": ["grasim industries", "grasim"],
    "Titan Company": ["titan company", "titan"],
    "Zomato": ["zomato"],
    "Paytm": ["paytm", "one97 communications"],
    "PolicyBazaar": ["policybazaar", "pb fintech"],
    "Nykaa": ["nykaa", "fsn e-commerce"],
}

CURATED_SECTOR = {
    "HDFC Bank": "BANKING", "ICICI Bank": "BANKING", "State Bank of India": "BANKING",
    "Axis Bank": "BANKING", "Kotak Mahindra Bank": "BANKING", "IndusInd Bank": "BANKING",
    "Bank of Baroda": "BANKING", "Punjab National Bank": "BANKING",
    "Bandhan Bank": "BANKING", "Yes Bank": "BANKING", "Bajaj Finance": "BANKING",
    "Bajaj Finserv": "BANKING",
    "Tata Consultancy Services": "TECH", "Infosys": "TECH", "Wipro": "TECH",
    "HCL Technologies": "TECH", "Tech Mahindra": "TECH", "LTIMindtree": "TECH",
    "Persistent Systems": "TECH", "Coforge": "TECH", "Mphasis": "TECH",
    "Zomato": "TECH", "Paytm": "TECH", "PolicyBazaar": "TECH", "Nykaa": "TECH",
    "Maruti Suzuki": "AUTO", "Tata Motors": "AUTO", "Mahindra & Mahindra": "AUTO",
    "Bajaj Auto": "AUTO", "Hero MotoCorp": "AUTO", "TVS Motor": "AUTO",
    "Eicher Motors": "AUTO", "Ashok Leyland": "AUTO",
    "Sun Pharma": "PHARMA", "Dr Reddy's Laboratories": "PHARMA", "Cipla": "PHARMA",
    "Divi's Laboratories": "PHARMA", "Lupin": "PHARMA", "Aurobindo Pharma": "PHARMA",
    "Biocon": "PHARMA", "Apollo Hospitals": "PHARMA",
    "Reliance Industries": "ENERGY", "Oil and Natural Gas Corporation": "ENERGY",
    "Indian Oil Corporation": "ENERGY", "Bharat Petroleum": "ENERGY",
    "Hindustan Petroleum": "ENERGY", "NTPC": "ENERGY",
    "Power Grid Corporation": "ENERGY", "Adani Power": "ENERGY", "Tata Power": "ENERGY",
    "Adani Green Energy": "ENERGY", "Adani Total Gas": "ENERGY",
    "Hindustan Unilever": "FMCG", "ITC": "FMCG", "Nestle India": "FMCG",
    "Britannia Industries": "FMCG", "Dabur India": "FMCG", "Marico": "FMCG",
    "Godrej Consumer": "FMCG", "Tata Consumer Products": "FMCG",
    "Tata Steel": "METALS", "JSW Steel": "METALS", "Hindalco Industries": "METALS",
    "Vedanta": "METALS", "Coal India": "METALS", "NMDC": "METALS",
    "Steel Authority of India": "METALS", "Jindal Steel & Power": "METALS",
    "DLF": "REALTY", "Godrej Properties": "REALTY", "Oberoi Realty": "REALTY",
    "Prestige Estates": "REALTY", "Macrotech Developers": "REALTY",
    "Phoenix Mills": "REALTY", "Brigade Enterprises": "REALTY",
}

SECTORS = ["BANKING", "TECH", "AUTO", "PHARMA", "ENERGY", "FMCG", "METALS", "REALTY"]

# Sector keywords for articles with no detected company
SECTOR_KEYWORDS = {
    "BANKING": ["bank", "banking", "loan", "nbfc", "credit", "deposit", "rbi", "monetary"],
    "TECH": ["tech", "it services", "software", "saas", "ai", "cloud", "semiconductor", "chip"],
    "AUTO": ["auto", "automobile", "ev", "electric vehicle", "car", "two-wheeler", "suv"],
    "PHARMA": ["pharma", "drug", "vaccine", "hospital", "usfda", "fda", "biotech"],
    "ENERGY": ["oil", "gas", "crude", "power", "energy", "renewable", "solar", "coal"],
    "FMCG": ["fmcg", "consumer goods", "staples", "personal care"],
    "METALS": ["steel", "iron ore", "aluminium", "copper", "metal", "mining"],
    "REALTY": ["real estate", "realty", "property", "housing", "residential", "commercial real"],
}


# ────────────────────────────────────────────────────────────────
# Load company_master.json (auto-built from canonical sources)
# ────────────────────────────────────────────────────────────────
_MASTER_PATH = Path(__file__).parent / "company_master.json"
COMPANY_ALIASES: dict[str, list[str]] = dict(CURATED_ALIASES)
SECTOR_BY_COMPANY: dict[str, str] = dict(CURATED_SECTOR)
INDUSTRY_BY_COMPANY: dict[str, str] = {}
ISIN_BY_COMPANY: dict[str, str] = {}
NSE_SYMBOL_BY_COMPANY: dict[str, str] = {}

if _MASTER_PATH.exists():
    try:
        _master = json.loads(_MASTER_PATH.read_text(encoding="utf-8"))
        for entry in _master:
            name = entry.get("name")
            if not name:
                continue
            # Industry / ISIN / NSE symbol — always from master (more authoritative)
            if entry.get("industry"):
                INDUSTRY_BY_COMPANY[name] = entry["industry"]
            if entry.get("isin"):
                ISIN_BY_COMPANY[name] = entry["isin"]
            if entry.get("nse_symbol"):
                NSE_SYMBOL_BY_COMPANY[name] = entry["nse_symbol"]
            # Sector: curated wins; master fills gaps
            if name not in SECTOR_BY_COMPANY and entry.get("sector"):
                SECTOR_BY_COMPANY[name] = entry["sector"]
            # Aliases: merge if curated exists, else use master's
            existing = COMPANY_ALIASES.get(name, [])
            master_aliases = entry.get("aliases", [])
            merged = list(dict.fromkeys(existing + master_aliases))
            COMPANY_ALIASES[name] = merged
    except Exception as e:
        import logging
        logging.getLogger("company_aliases").warning(
            f"failed to load {_MASTER_PATH}: {e}"
        )


# ────────────────────────────────────────────────────────────────
# Detection (longest-alias-first to avoid 'hdfc' shadowing 'hdfc bank')
# ────────────────────────────────────────────────────────────────
def _build_alias_lookup() -> dict:
    lookup = {}
    for canonical, aliases in COMPANY_ALIASES.items():
        for a in aliases:
            a_low = a.lower().strip()
            if not a_low:
                continue
            # Don't let master aliases trample curated ones
            if a_low in lookup and lookup[a_low] in CURATED_ALIASES:
                continue
            lookup[a_low] = canonical
    return lookup


_ALIAS_LOOKUP = _build_alias_lookup()
# Sort by length desc — match "hdfc bank" before "hdfc"
_SORTED_ALIASES = sorted(_ALIAS_LOOKUP.keys(), key=len, reverse=True)

# Precompiled regex for fast scan. Bound on word edges to avoid mid-word matches.
# Chunked because Python's re has a group-count limit (~99 groups per alt).
_REGEX_CHUNKS: list[re.Pattern] = []
def _build_regex_chunks(aliases: list[str], chunk_size: int = 200) -> list[re.Pattern]:
    pats = []
    for i in range(0, len(aliases), chunk_size):
        chunk = aliases[i:i + chunk_size]
        # Escape, join, word-boundary-ish (allow alpha-num neighbors to block)
        # Using \b doesn't work for things like 'l&t' so we use a custom boundary
        body = "|".join(re.escape(a) for a in chunk)
        pats.append(re.compile(rf"(?<![a-z0-9])({body})(?![a-z0-9])", re.IGNORECASE))
    return pats

_REGEX_CHUNKS = _build_regex_chunks(_SORTED_ALIASES)


def detect_companies(text: str) -> list:
    """Return canonical company names mentioned in text (deduped, first-occurrence order)."""
    if not text:
        return []
    t = " " + text.lower() + " "
    found_order = []
    seen = set()
    # Scan with each chunk regex; collect by position
    matches = []
    for pat in _REGEX_CHUNKS:
        for m in pat.finditer(t):
            matches.append((m.start(), m.group(1).lower()))
    if not matches:
        return []
    # Sort by position; for overlapping matches at the same start, longest wins
    matches.sort(key=lambda x: (x[0], -len(x[1])))
    used_spans = []  # list of (start, end) already consumed
    for start, alias in matches:
        end = start + len(alias)
        # Skip if overlaps an already-consumed span (longer alias already won)
        if any(s <= start < e or s < end <= e for s, e in used_spans):
            continue
        canon = _ALIAS_LOOKUP.get(alias)
        if canon and canon not in seen:
            seen.add(canon)
            found_order.append(canon)
        used_spans.append((start, end))
    return found_order


def normalize_company(name: str) -> Optional[str]:
    """Single name -> canonical, or None if unknown."""
    if not name:
        return None
    return _ALIAS_LOOKUP.get(name.lower().strip())


def detect_sector(text: str, companies: Optional[list] = None) -> Optional[str]:
    """First sector match via company; falls back to keyword scan."""
    if companies:
        for c in companies:
            if c in SECTOR_BY_COMPANY:
                return SECTOR_BY_COMPANY[c]
    if not text:
        return None
    t = text.lower()
    best_sector = None
    best_hits = 0
    for sector, keywords in SECTOR_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in t)
        if hits > best_hits:
            best_hits = hits
            best_sector = sector
    return best_sector


def get_industry(company: str) -> Optional[str]:
    """Fine-grained screener.in industry name for a canonical company."""
    return INDUSTRY_BY_COMPANY.get(company)


def get_isin(company: str) -> Optional[str]:
    return ISIN_BY_COMPANY.get(company)


def get_nse_symbol(company: str) -> Optional[str]:
    return NSE_SYMBOL_BY_COMPANY.get(company)


ALL_COMPANIES = list(COMPANY_ALIASES.keys())
