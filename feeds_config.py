"""
RSS Feed Config — FINANCE-ONLY, AUTHENTIC SOURCES
==================================================
Every feed listed here points at a finance/business/markets section of a
publisher we've verified — no general-news roots, no entertainment, no sport.

Coverage strategy:
  - Direct RSS feeds for finance subsections from top Indian publishers
  - Google News `site:` queries for publishers without working direct feeds
  - Official-source coverage via Google News (NSE, BSE, SEBI press releases)
  - Global wire (Bloomberg, MarketWatch, Yahoo Finance, CNBC US, Seeking Alpha)

WHAT'S NEW vs the prior version:
  - Dropped Times of India general top stories (was producing macro / political
    content that confused the company tagger).
  - Dropped India Today /rss/1206550 (mixed-quality coverage).
  - Added official-source coverage via Google News site: queries for
    NSE, BSE, SEBI, RBI press releases (regulator filings).
  - Added Financial Express direct + Forbes India direct.
"""

# ═══════════════════════════════════════════════════════════════
# DIRECT RSS FEEDS — explicit finance subsections only
# ═══════════════════════════════════════════════════════════════

ET_FEEDS = {
    "Economic Times": [
        "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
        "https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms",
        "https://economictimes.indiatimes.com/news/economy/policy/rssfeeds/1373380680.cms",
        "https://economictimes.indiatimes.com/industry/banking/finance/rssfeeds/13358259.cms",
        "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms",
        "https://economictimes.indiatimes.com/markets/ipos/rssfeeds/2146843.cms",
        "https://economictimes.indiatimes.com/markets/commodities/rssfeeds/1808152121.cms",
        "https://economictimes.indiatimes.com/News/rssfeeds/1715249553.cms",
        "https://economictimes.indiatimes.com/news/economy/indicators/rssfeeds/1373380680.cms",
    ],
}

# Direct Moneycontrol RSS endpoints return HTTP 403 from server IPs (Cloudflare
# bot-protection on moneycontrol.com).  Coverage preserved via Google site:
# query in AGGREGATOR_FEEDS.
MC_FEEDS = {}

MINT_FEEDS = {
    "Livemint": [
        "https://www.livemint.com/rss/news",
        "https://www.livemint.com/rss/markets",
        "https://www.livemint.com/rss/companies",
        "https://www.livemint.com/rss/money",
        "https://www.livemint.com/rss/economy",
        "https://www.livemint.com/rss/industry",
        "https://www.livemint.com/rss/technology",
        "https://www.livemint.com/rss/opinion",
        "https://www.livemint.com/rss/insurance",
    ],
}

NDTV_FEEDS = {"NDTV Profit": ["https://feeds.feedburner.com/ndtvprofit-latest"]}

HINDU_FEEDS = {
    "The Hindu": [
        "https://www.thehindu.com/business/feeder/default.rss",
        "https://www.thehindu.com/business/markets/feeder/default.rss",
        "https://www.thehindu.com/business/Industry/feeder/default.rss",
        "https://www.thehindu.com/business/Economy/feeder/default.rss",
    ],
    "Hindu BusinessLine": [
        "https://www.thehindubusinessline.com/companies/feeder/default.rss",
        "https://www.thehindubusinessline.com/markets/feeder/default.rss",
        "https://www.thehindubusinessline.com/money-and-banking/feeder/default.rss",
        "https://www.thehindubusinessline.com/economy/feeder/default.rss",
    ],
}

CNBC_FEEDS = {
    "CNBC TV18": [
        "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml",
        "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/business.xml",
        "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/economy.xml",
        "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/companies.xml",
        "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/personal-finance.xml",
    ],
}

IE_FEEDS = {
    "Indian Express": [
        "https://indianexpress.com/section/business/feed/",
        "https://indianexpress.com/section/business/market/feed/",
        "https://indianexpress.com/section/business/economy/feed/",
        "https://indianexpress.com/section/business/banking-and-finance/feed/",
        "https://indianexpress.com/section/business/companies/feed/",
    ],
}

# Times of India direct feeds dropped — none of the published feed IDs are
# strictly business-only, and the polluted general-news content was the main
# source of mis-tagging.  Coverage preserved via Google News
# `site:timesofindia.com business` in AGGREGATOR_FEEDS.
TOI_FEEDS = {}

BT_FEEDS = {"Business Today": ["https://www.businesstoday.in/rssfeeds/?id=home"]}

# India Today /rss/1206550 dropped — mixed-quality (entertainment + sport
# bleeding into "business").  Coverage preserved via Google News site:
# query in AGGREGATOR_FEEDS.
IT_FEEDS = {}

# News18 — only the business subsection is included
NEWS18_FEEDS = {"News18": ["https://www.news18.com/commonfeeds/v1/eng/rss/business.xml"]}

# Smaller specialist publishers — explicitly finance/markets focused
OTHER_FEEDS = {
    "Trade Brains":     ["https://tradebrains.in/feed/"],
    "Tickertape Blog":  ["https://www.tickertape.in/blog/feed/"],
    "Financial Express":["https://www.financialexpress.com/feed/"],
    "Forbes India":     ["https://forbesindia.com/rssfeed/rss_all.xml"],
}

# ═══════════════════════════════════════════════════════════════
# GOOGLE NEWS + AGGREGATORS
# Used for:
#   - Publishers without working direct feeds (Moneycontrol, Business Standard,
#     Zee Business, etc.) — pulled via `site:` queries
#   - Official-source coverage (NSE, BSE, SEBI, RBI press releases)
#   - Thematic queries (macro, sectors)
# ═══════════════════════════════════════════════════════════════
AGGREGATOR_FEEDS = {
    "Zerodha Pulse": ["https://pulse.zerodha.com/feed.php"],
    "Investing.com": ["https://www.investing.com/rss/news.rss"],
    "Google News": [
        # ── Broad market (3 queries) ──
        "https://news.google.com/rss/?hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=sensex+nifty+stock+market+india&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=IPO+OR+quarterly+results+OR+earnings+india&hl=en-IN&gl=IN&ceid=IN:en",

        # ── Macro & policy ──
        "https://news.google.com/rss/search?q=RBI+OR+SEBI+OR+budget+india+policy&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=GDP+OR+inflation+OR+rupee+OR+forex+india+economy&hl=en-IN&gl=IN&ceid=IN:en",

        # ── Commodities ──
        "https://news.google.com/rss/search?q=gold+OR+silver+OR+crude+oil+price+india&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=cryptocurrency+OR+bitcoin+india+FII+DII&hl=en-IN&gl=IN&ceid=IN:en",

        # ── Sectors ──
        "https://news.google.com/rss/search?q=india+banking+OR+insurance+OR+NBFC+sector&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=india+IT+OR+pharma+OR+auto+sector+stocks&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=india+real+estate+OR+FMCG+OR+metal+OR+power+OR+telecom&hl=en-IN&gl=IN&ceid=IN:en",

        # ── Blocked-publisher coverage via site: queries ──
        "https://news.google.com/rss/search?q=site:moneycontrol.com&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=site:business-standard.com&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=site:zeenews.india.com+business&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=site:ndtvprofit.com+OR+site:news18.com+business&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=site:deccanherald.com+business+OR+site:hindustantimes.com+business&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=site:indiatoday.in+business+OR+site:theprint.in+business&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=site:timesofindia.indiatimes.com+business+OR+site:timesofindia.indiatimes.com+markets&hl=en-IN&gl=IN&ceid=IN:en",

        # NOTE: Google News `site:` queries for nseindia.com / bseindia.com /
        # sebi.gov.in / rbi.org.in / pib.gov.in were tried and dropped — the
        # feeds were dominated by 2014-2020 archived pages with poor recency
        # and lots of static documentation polluting the trending list.
        # Direct official RSS endpoints are used instead (see OFFICIAL_FEEDS).
    ],
}

# ═══════════════════════════════════════════════════════════════
# OFFICIAL INDIAN FINANCIAL SOURCES — direct RSS
# Each URL has been verified live (content recent within last week, schema
# parsed cleanly by feedparser).  These are the gold-standard authentic feeds
# for regulatory + central-bank news.
# ═══════════════════════════════════════════════════════════════
OFFICIAL_FEEDS = {
    # ── RBI (Reserve Bank of India) — 4 official feeds ──
    # https://www.rbi.org.in/Scripts/rss.aspx lists these directly.
    "RBI Press Releases": ["https://rbi.org.in/pressreleases_rss.xml"],
    "RBI Notifications":  ["https://rbi.org.in/notifications_rss.xml"],
    "RBI Speeches":       ["https://rbi.org.in/speeches_rss.xml"],
    "RBI Publications":   ["https://rbi.org.in/Publication_rss.xml"],

    # ── SEBI (Securities and Exchange Board of India) ──
    # Verified: 30 items, refreshed within last hour, enforcement orders +
    # circulars + adjudication notices.
    "SEBI": ["https://www.sebi.gov.in/sebirss.xml"],

    # ── BSE (Bombay Stock Exchange) — market notices ──
    # Verified: market schedule, mutual-fund availability, regulatory updates.
    # Few items per day but very high signal.
    "BSE Notices": ["https://www.bseindia.com/data/xml/notices.xml"],

    # ── PIB Ministry of Finance press releases ──
    # Standard PIB RSS pattern — needs browser User-Agent to bypass the basic
    # bot filter; main.py's BROWSER_HEADERS already provides this. If this
    # 403s in test_feeds.py, the live fetcher will still likely succeed.
    "PIB Finance": [
        "https://pib.gov.in/PressReleseRSSXmlMRSS.aspx?RegId=3&LangId=1",
    ],

    # NSE (National Stock Exchange) does NOT publish a public RSS — their
    # corporate announcements are behind a session-authenticated API.
    # Coverage via news aggregator: Google News for individual companies
    # picks up NSE filings via secondary republication.
}

# ── Global wire ────────────────────────────────────────────────
GLOBAL_FEEDS = {
    "CNBC US": [
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    ],
    "MarketWatch": [
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
    ],
    "Yahoo Finance": ["https://finance.yahoo.com/news/rssindex"],
    "Seeking Alpha": ["https://seekingalpha.com/market_currents.xml", "https://seekingalpha.com/feed.xml"],
    "Bloomberg": ["https://feeds.bloomberg.com/markets/news.rss"],
}


# ── Re-test list ──────────────────────────────────────────────
# Feeds that failed in earlier health checks and may not work; included here
# so the periodic `test_feeds.py --retest` can probe them.
FEEDS_TO_RETEST = [
    ("Economic Times", "https://economictimes.indiatimes.com/mf/rssfeeds/4521498.cms"),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/forex/rssfeeds/1808152117.cms"),
    ("Groww Blog", "https://groww.in/blog/feed/"),
    ("Equitymaster", "https://www.equitymaster.com/rss/"),
    ("Deccan Herald", "https://www.deccanherald.com/rss/business.rss"),
    ("VCCircle", "https://www.vccircle.com/feed/"),
    ("Outlook Business", "https://www.outlookbusiness.com/rss"),
]


# ── Domain → Referer mapping ─────────────────────────────────
REFERER_MAP = {
    "news18.com": "https://www.news18.com/",
    "moneycontrol.com": "https://www.moneycontrol.com/",
    "financialexpress.com": "https://www.financialexpress.com/",
    "pib.gov.in": "https://pib.gov.in/",
    "rbi.org.in": "https://www.rbi.org.in/",
    "sebi.gov.in": "https://www.sebi.gov.in/",
    "bseindia.com": "https://www.bseindia.com/",
}


# URL-path sector hints — when an article comes from a feed whose URL contains
# one of these substrings, we use that as a high-confidence sector signal.
# Examples that match:
#   indianexpress.com/section/business/banking-and-finance/feed/    -> BANKING
#   livemint.com/rss/insurance                                       -> BANKING
#   thehindubusinessline.com/money-and-banking/feeder/default.rss   -> BANKING
#   et.com/industry/banking/finance/rssfeeds/...                    -> BANKING
#   et.com/tech/rssfeeds/...                                        -> TECH
#   livemint.com/rss/technology                                     -> TECH
#   et.com/markets/commodities/rssfeeds/...                         -> ENERGY (commodity proxy)
# Returns None if no hint matched. detect_sector() uses this BEFORE its
# keyword-fallback scan.
URL_SECTOR_HINTS = (
    # ── BANKING (incl. insurance + finance) ──
    ("/banking-and-finance/", "BANKING"),
    ("/banking/finance/",     "BANKING"),
    ("/money-and-banking/",   "BANKING"),
    ("/banking/",             "BANKING"),
    ("/banks/",               "BANKING"),
    ("/insurance",            "BANKING"),
    ("/personal-finance",     "BANKING"),
    ("/nbfc",                 "BANKING"),
    ("/finance/",             "BANKING"),
    # ── TECH ──
    ("/technology",           "TECH"),
    ("/tech/",                "TECH"),
    ("/it/",                  "TECH"),
    ("/software",             "TECH"),
    # ── AUTO ──
    ("/auto/",                "AUTO"),
    ("/automobile",           "AUTO"),
    ("/automotive",           "AUTO"),
    # ── PHARMA / HEALTHCARE ──
    ("/pharma",               "PHARMA"),
    ("/healthcare",           "PHARMA"),
    ("/health/",              "PHARMA"),
    # ── ENERGY ──
    ("/energy",               "ENERGY"),
    ("/oil-and-gas",          "ENERGY"),
    ("/power/",               "ENERGY"),
    ("/commodities",          "ENERGY"),
    # ── FMCG ──
    ("/fmcg",                 "FMCG"),
    ("/consumer",             "FMCG"),
    # ── METALS ──
    ("/metals",               "METALS"),
    ("/steel",                "METALS"),
    ("/mining",               "METALS"),
    # ── REALTY ──
    ("/real-estate",          "REALTY"),
    ("/realty",               "REALTY"),
    ("/property",             "REALTY"),
    ("/housing",              "REALTY"),
)


def sector_hint_from_url(url: str) -> str | None:
    """Return the sector chip (or None) implied by the feed URL path."""
    if not url:
        return None
    url_l = url.lower()
    for needle, sector in URL_SECTOR_HINTS:
        if needle in url_l:
            return sector
    return None


def _flatten(groups: list[dict]) -> list[tuple[str, str]]:
    result = []
    for group in groups:
        for source_name, urls in group.items():
            for url in urls:
                result.append((source_name, url))
    return result


# News-pipeline feeds — fetched every 10 min by the live scheduler.
_NEWS_GROUPS = [
    ET_FEEDS, MC_FEEDS, MINT_FEEDS, NDTV_FEEDS, HINDU_FEEDS,
    CNBC_FEEDS, IE_FEEDS, TOI_FEEDS, BT_FEEDS, IT_FEEDS,
    NEWS18_FEEDS, OTHER_FEEDS, AGGREGATOR_FEEDS, GLOBAL_FEEDS,
]

# Filings-pipeline feeds — fetched every 30 min by a separate scheduler.
# These are official Indian regulator + exchange RSS endpoints (RBI, SEBI,
# BSE notices, PIB Finance). Lower cadence is intentional:
#   - regulator endpoints publish slowly (a few items/day)
#   - we want to be polite to gov.in domains
#   - filings news doesn't expire in 10 min the way market commentary does
_FILINGS_GROUPS = [OFFICIAL_FEEDS]


def get_all_feeds() -> list[tuple[str, str]]:
    """All feeds across both pipelines — kept for back-compat / test_feeds.py."""
    return _flatten(_NEWS_GROUPS + _FILINGS_GROUPS)


def get_news_feeds() -> list[tuple[str, str]]:
    """News pipeline feeds — fast cadence (every 10 min)."""
    return _flatten(_NEWS_GROUPS)


def get_filings_feeds() -> list[tuple[str, str]]:
    """Filings pipeline feeds — slow cadence (every 30 min), official sources."""
    return _flatten(_FILINGS_GROUPS)


def get_company_feeds(company_name: str) -> list[tuple[str, str]]:
    safe = company_name.replace(" ", "+")
    return [
        (f"Google News ({company_name})",
         f"https://news.google.com/rss/search?q={safe}+stock&hl=en-IN&gl=IN&ceid=IN:en"),
        (f"Google News ({company_name})",
         f"https://news.google.com/rss/search?q={safe}+quarterly+results&hl=en-IN&gl=IN&ceid=IN:en"),
        # English-only — Hindi pulls Devanagari which the is_hindi filter
        # would then drop, so it added latency for zero output.
        (f"Google News ({company_name})",
         f"https://news.google.com/rss/search?q={safe}+NSE+OR+BSE&hl=en-IN&gl=IN&ceid=IN:en"),
    ]


ALL_FEEDS = get_all_feeds()
NEWS_FEEDS = get_news_feeds()
FILINGS_FEEDS = get_filings_feeds()
FEED_COUNT = len(ALL_FEEDS)
NEWS_FEED_COUNT = len(NEWS_FEEDS)
FILINGS_FEED_COUNT = len(FILINGS_FEEDS)

if __name__ == "__main__":
    gn = sum(1 for _, u in ALL_FEEDS if "news.google.com" in u)
    direct = FEED_COUNT - gn
    print(f"Total feeds: {FEED_COUNT}")
    print(f"  News pipeline    (10 min cadence): {NEWS_FEED_COUNT}")
    print(f"  Filings pipeline (30 min cadence): {FILINGS_FEED_COUNT}")
    print(f"  Direct RSS: {direct} | Google News: {gn}")
    print(f"  Target latency: ~3s per wave (chunked-wave fetcher)")
