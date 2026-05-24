"""
Build company_master.json from canonical sources.

Inputs (one-time pull at build time, NOT runtime):
  - Postgres `filings_index` (company_name + industry, ~4,150 distinct pairs)
  - Postgres `company_industry` (code + isin + industry_rank for de-dup)
  - SQLite `nse_scrips` (symbol + company_name + isin)
  - SQLite `scrips` (BSE scrip_cd + name + isin + mktcap)
  - on_demand_router's `_SECTOR_INDUSTRIES` (screener-industry -> prism-sector)

Output: NewsRSS/company_master.json — bundled with the repo, loaded by
company_aliases.py at runtime so prism-news doesn't have to talk to
external DBs to know what 'TCS' or 'Adani Power' is.

Run once when the upstream taxonomy changes:
    python build_company_master.py

Requires:
    pip install psycopg2-binary
    POSTGRES_URL env var (or hardcode below).
"""
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

try:
    import psycopg2
except ImportError:
    sys.exit("pip install psycopg2-binary first")


# ── coarse sector mapping (8 chips in prism-news UI) ───────────────
# Maps screener.in industry names to prism's 8 sector codes.
# Built from on_demand_router._SECTOR_INDUSTRIES + extras for industries
# the router doesn't classify (mapped to closest prism bucket or None).
PRISM_SECTOR_BY_INDUSTRY = {
    # ── BANKING ──
    "Private Sector Bank": "BANKING",
    "Public Sector Bank": "BANKING",
    "Other Bank": "BANKING",
    "Non Banking Financial Company (NBFC)": "BANKING",
    "Housing Finance Company": "BANKING",
    "Other Financial Services": "BANKING",
    "Stockbroking & Allied": "BANKING",
    "Investment Company": "BANKING",
    "Holding Company": "BANKING",
    "Life Insurance": "BANKING",
    "General Insurance": "BANKING",
    "Insurance Distributors": "BANKING",
    "Asset Management Company": "BANKING",
    "Specialized Finance": "BANKING",

    # ── TECH ──
    "Computers - Software & Consulting": "TECH",
    "Software Products": "TECH",
    "IT Enabled Services": "TECH",
    "Computers Hardware & Equipments": "TECH",
    "Business Process Outsourcing (BPO)/ Knowledge Process Outsourcing (KPO)": "TECH",
    "Data Processing Services": "TECH",
    "Internet & Catalogue Retail": "TECH",
    "Online Services": "TECH",

    # ── AUTO ──
    "2/3 Wheelers": "AUTO",
    "Passenger Cars & Utility Vehicles": "AUTO",
    "Commercial Vehicles": "AUTO",
    "Tractors": "AUTO",
    "Construction Vehicles": "AUTO",
    "Auto Components & Equipments": "AUTO",
    "Tyres & Rubber Products": "AUTO",
    "Auto Dealer": "AUTO",
    "Dealers-Commercial Vehicles, Tractors, Construction Vehicles": "AUTO",
    "Trading - Auto components": "AUTO",

    # ── PHARMA ──
    "Pharmaceuticals": "PHARMA",
    "Pharmacy Retail": "PHARMA",
    "Biotechnology": "PHARMA",
    "Hospital": "PHARMA",
    "Healthcare Service Provider": "PHARMA",
    "Medical Equipment & Supplies": "PHARMA",
    "Healthcare Research, Analytics & Technology": "PHARMA",

    # ── ENERGY ──
    "Integrated Power Utilities": "ENERGY",
    "Power Generation": "ENERGY",
    "Power Distribution": "ENERGY",
    "Power - Transmission": "ENERGY",
    "Power Trading": "ENERGY",
    "Refineries & Marketing": "ENERGY",
    "Exploration & Production": "ENERGY",
    "LPG/CNG/PNG/LNG Suppliers": "ENERGY",
    "Storage & Transportation": "ENERGY",
    "Oil Storage & Transportation": "ENERGY",
    "Trading - Gas": "ENERGY",
    "Renewable Energy Equipment & Services": "ENERGY",

    # ── FMCG ──
    "Diversified FMCG": "FMCG",
    "Packaged Foods": "FMCG",
    "Personal Care": "FMCG",
    "Household Products": "FMCG",
    "Dairy Products": "FMCG",
    "Other Food Products": "FMCG",
    "Tea & Coffee": "FMCG",
    "Edible Oil": "FMCG",
    "Cigarettes & Tobacco Products": "FMCG",
    "Breweries & Distilleries": "FMCG",
    "Other Beverages": "FMCG",
    "Diversified consumer products": "FMCG",

    # ── METALS ──
    "Iron & Steel": "METALS",
    "Iron & Steel Products": "METALS",
    "Sponge Iron": "METALS",
    "Pig Iron": "METALS",
    "Ferro & Silica Manganese": "METALS",
    "Aluminium": "METALS",
    "Copper": "METALS",
    "Zinc": "METALS",
    "Diversified Metals": "METALS",
    "Precious Metals": "METALS",
    "Mining": "METALS",
    "Coal": "METALS",
    "Cement & Cement Products": "METALS",

    # ── REALTY ──
    "Residential, Commercial Projects": "REALTY",
    "Real Estate related services": "REALTY",
    "Real Estate Investment Trusts (REITs)": "REALTY",
    "Civil Construction": "REALTY",
}

# Suffix tokens we strip when building aliases for a company name.
# These are noise that prevents "Reliance Industries Ltd" matching "Reliance".
_NAME_SUFFIX_STRIP = re.compile(
    r"\s*(?:limited|ltd\.?|private limited|pvt\.?\s*ltd\.?|inc\.?|corporation|corp\.?|company|co\.?)\s*$",
    re.IGNORECASE,
)
_AMPERSAND_NORM = re.compile(r"\s*&\s*")


def canonical_name(raw: str) -> str:
    """Normalize a name for storage. Strips trailing 'Ltd/Limited/etc'."""
    if not raw:
        return ""
    name = raw.strip()
    name = _AMPERSAND_NORM.sub(" & ", name)
    name = _NAME_SUFFIX_STRIP.sub("", name).strip()
    return name


def build_aliases(canonical: str, nse_symbol: str | None) -> list[str]:
    """Heuristic alias generation: stripped name, full name, NSE symbol, short tokens."""
    if not canonical:
        return []
    aliases = set()
    aliases.add(canonical.lower())
    # Without spaces
    aliases.add(canonical.lower().replace(" ", ""))
    # Without "&" -> "and"
    aliases.add(canonical.lower().replace(" & ", " and "))
    # First word only IF it's distinctive (>3 chars)
    first = canonical.split()[0].lower()
    if len(first) > 3:
        aliases.add(first)
    # NSE symbol
    if nse_symbol:
        aliases.add(nse_symbol.lower())
    # Strip out empties + duplicates
    return sorted(a for a in aliases if a)


def main():
    pg_url = os.getenv(
        "POSTGRES_URL",
        "postgresql://stock_user:eygbAWxNVvi06sy3ppu25AKxSEi0RZwr@35.234.221.166:5434/stock_chat",
    )
    # asyncpg-flavored URL -> sync
    pg_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")

    sqlite_path = os.getenv(
        "STATE_DB",
        r"C:\Users\adity\Downloads\NSE_BSE_pdfs\NSE_BSE_pdfs\data\state.db",
    )

    print(f"PG     : {pg_url.split('@')[1] if '@' in pg_url else pg_url}")
    print(f"SQLite : {sqlite_path}")

    # ── pull company_name + industry from filings_index (the only place both live) ──
    pg = psycopg2.connect(pg_url, connect_timeout=15)
    pc = pg.cursor()
    pc.execute("""
        SELECT company_name, industry, isin, MAX(announcement_dt) latest_filing
        FROM filings_index
        WHERE company_name IS NOT NULL AND company_name != ''
          AND industry IS NOT NULL AND industry != ''
        GROUP BY company_name, industry, isin
    """)
    pg_rows = pc.fetchall()
    print(f"PG     : {len(pg_rows):,} (company, industry, isin) rows from filings_index")

    # ── pull NSE symbols (to enrich aliases) ──
    sc = sqlite3.connect(sqlite_path)
    scur = sc.cursor()
    scur.execute("SELECT symbol, company_name, isin FROM nse_scrips WHERE isin IS NOT NULL")
    nse_rows = scur.fetchall()
    nse_by_isin = {isin: sym for sym, _, isin in nse_rows if isin}
    print(f"NSE    : {len(nse_by_isin):,} ISIN->symbol mappings")

    # ── pull BSE master for backup name lookup ──
    scur.execute("SELECT isin, scrip_name, scrip_cd, mktcap FROM scrips WHERE isin IS NOT NULL")
    bse_rows = scur.fetchall()
    bse_by_isin = {isin: (name, code, mcap) for isin, name, code, mcap in bse_rows if isin}
    print(f"BSE    : {len(bse_by_isin):,} ISIN->(name,code,mcap) mappings")
    sc.close()

    # ── merge: choose the most-recent (company, industry) pair per company ──
    # When a company changes industry across filings, the most recent wins.
    best_pair: dict[str, tuple[str, str | None, str | None]] = {}
    # key: canonical_name lower, value: (canonical, industry, isin)
    for raw_name, industry, isin, latest in pg_rows:
        canon = canonical_name(raw_name)
        if not canon:
            continue
        key = canon.lower()
        existing = best_pair.get(key)
        if existing is None:
            best_pair[key] = (canon, industry, isin)
        # If a previous row had no ISIN and this one does, prefer this
        elif not existing[2] and isin:
            best_pair[key] = (canon, industry, isin)

    print(f"merged : {len(best_pair):,} unique canonical companies")

    # ── build the master ──
    master = []
    sector_hits = defaultdict(int)
    sector_miss = defaultdict(int)
    for canon, industry, isin in best_pair.values():
        sector = PRISM_SECTOR_BY_INDUSTRY.get(industry)
        nse_sym = nse_by_isin.get(isin) if isin else None
        bse_info = bse_by_isin.get(isin) if isin else None
        bse_code = bse_info[1] if bse_info else None
        mcap = bse_info[2] if bse_info else None
        aliases = build_aliases(canon, nse_sym)
        master.append({
            "name": canon,
            "industry": industry,
            "sector": sector,
            "isin": isin,
            "nse_symbol": nse_sym,
            "bse_code": bse_code,
            "mktcap_cr": round(mcap, 2) if mcap else None,
            "aliases": aliases,
        })
        if sector:
            sector_hits[sector] += 1
        else:
            sector_miss[industry] += 1

    # Sort by mktcap desc (largest first; None at end)
    master.sort(key=lambda r: (r["mktcap_cr"] is None, -(r["mktcap_cr"] or 0)))

    out_path = Path(__file__).parent / "company_master.json"
    out_path.write_text(json.dumps(master, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote  : {out_path} ({out_path.stat().st_size/1024:.1f} KB, {len(master):,} companies)")

    print()
    print("Sector coverage:")
    for sec in ["BANKING","TECH","AUTO","PHARMA","ENERGY","FMCG","METALS","REALTY"]:
        print(f"  {sec:<8} {sector_hits.get(sec, 0):>5}")
    print(f"  (unmapped)  {sum(sector_miss.values()):>5}  spanning {len(sector_miss)} screener industries")
    print()
    print("Top 10 unmapped industries (consider adding to PRISM_SECTOR_BY_INDUSTRY):")
    for ind, n in sorted(sector_miss.items(), key=lambda x: -x[1])[:10]:
        print(f"  {n:>4}  {ind!r}")

    pg.close()


if __name__ == "__main__":
    main()
