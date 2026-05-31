# Prism Financial News API — Integration Guide

FastAPI service that aggregates **73 RSS feeds** of Indian financial news, tags articles by **company** + **sector** (4,149-company alias master), runs **OpenAI sentiment** lazily, and exposes the data via REST + an **MCP endpoint** for Claude / agent tool integration.

> **Note:** Corporate-filings coverage (NSE / BSE / SEBI / RBI / PIB direct RSS) is now a **separate standalone service**: `prism-filings`. This repo is news-only.

**Base URL (prod):** `http://<gcp-server>:8001`
**Swagger UI:** `http://<gcp-server>:8001/docs`
**OpenAPI spec:** `http://<gcp-server>:8001/openapi.json`

---

## Architecture at a glance

```
┌── 10 min ──┐
│ NEWS       │
│ scheduler  │
│ 73 feeds   │
│ (ET, Mint, │
│  MC via    │
│  Google,   │
│  CNBC,     │
│  Bloomberg,│
│  ...)      │
└─────┬──────┘
      │
      ▼
  ┌──────────────────────────────────┐
  │ MongoDB articles collection      │
  └──────┬──────────────────┬────────┘
         │                  │
    ┌────┴────┐         ┌───┴────┐
    ▼         ▼         ▼        ▼
 /news    /news/...    /mcp (3 tools)
```

**Corporate filings** (results / dividends / board meetings / AGM/EGM / M&A / IPOs from NSE / BSE / SEBI / RBI / PIB) → see the **`prism-filings`** service (separate repo + own MongoDB + own /mcp).

---

## Quick start for frontend integration

### 1. Headline feed (newsroom UI)

```http
GET /news?sector=BANKING&hours=24&limit=50&fuzzy=true
```

Response shape:
```json
{
  "success": true,
  "query": {"company": null, "sector": "BANKING", "hours": 24, "page": 1, "limit": 50},
  "meta": {
    "total_results": 64, "returned": 50, "total_pages": 2, "current_page": 1,
    "feeds_configured": 80, "response_time_ms": 92,
    "last_full_fetch_ist": "2026-05-31 13:48:48 IST",
    "data_age_min": 4,                          // ← UI: "Updated 4 min ago"
    "refresh_interval_min": 10,                 // ← UI: "next refresh in 6 min"
    "new_articles": {"company_search": 0, "full_fetch": 0},
    "sentiment_analyzed_this_request": 0,
    "sentiment_provider": "openai",
    "cache_status": "fresh -> DB only"
  },
  "articles": [
    {
      "title": "HDFC Bank Q4 profit beats estimates",
      "description": "...",
      "source": "Economic Times",
      "published_ist": "2026-05-31 13:48:48 IST",
      "published_dt": "2026-05-31T08:18:48Z",
      "link": "https://economictimes.indiatimes.com/...",
      "original_link": "https://news.google.com/...",
      "companies": ["HDFC Bank"],
      "sector": "BANKING",
      "sentiment": {"label": "positive", "score": 0.81, "provider": "openai"}
    }
  ]
}
```

**Query params:**

| Param | Default | Notes |
|---|---|---|
| `company` | — | CSV: `HDFC,ICICI,TCS`. Aliases accepted (`HDFC` → `HDFC Bank`, `TCS` → `Tata Consultancy Services`). |
| `sector` | — | One of `BANKING\|TECH\|AUTO\|PHARMA\|ENERGY\|FMCG\|METALS\|REALTY` |
| `hours` | 24 | 1–240 |
| `page`, `limit` | 1, 50 | pagination; `limit` ≤ 500 |
| `fuzzy` | `true` | collapse near-duplicate titles (recommended) |
| `resolve_links` | `true` | unwrap `news.google.com` URLs to publisher URLs |

**Refresh behavior:**
- 10-min scheduler ingests the 73 news feeds. **Users searching never trigger a full fetch.**
- `?company=X` does fire a small per-company Google News fetch (3 URLs) for that ticker → ~1s extra latency, fresher data for that specific name.
- `sentiment` is `null` on most articles — populated only on company-specific queries.

### 2. Per-company summary card

```http
GET /news/summary?company=HDFC%20Bank&hours=24
```

```json
{
  "company": "HDFC Bank", "total_articles": 45,
  "sentiment_breakdown": {"positive": 28, "negative": 8, "neutral": 9},
  "avg_score": 0.72, "trend": "bullish",
  "top_positive": [...], "top_negative": [...],
  "provider": "openai"
}
```

First call: 5–10s (OpenAI analyzes fresh articles). Subsequent calls: sub-second (cached).

### 3. Trending companies sidebar

```http
GET /news/trending?hours=24&limit=10
```

```json
{
  "trending": [
    {"company":"Reliance Industries","mentions":19,"sentiment":"positive",
     "sector":"ENERGY","sentiment_breakdown":{...}},
    ...
  ]
}
```

### 4. Multi-company comparison

```http
GET /news/compare?companies=HDFC,ICICI,SBI&hours=48
```

Returns each company's summary side-by-side, ranked by `avg_score`.

### 5. Source reliability strip

```http
GET /news/sources?hours=24
```

### 6. Directory endpoints (for dropdowns)

```http
GET /news/companies   → {"total": 4149, "companies": [...]}
GET /news/sectors     → {"sectors": ["BANKING","TECH","AUTO","PHARMA","ENERGY","FMCG","METALS","REALTY"]}
```

### 7. Health

```http
GET /health
```

```json
{
  "status": "running", "collecting_24_7": true,
  "llm_provider": "openai",
  "total_articles": 215000,
  "articles_with_sentiment": 8400,
  "articles_with_companies_tagged": 150000,
  "last_24h": 5100, "last_1h": 80,
  "sources_active": 220,
  "feeds": 73,
  "last_fetch": "2026-05-31 14:18:33 IST"
}
```

---

## Frontend wire-up cheat-sheet (matches the newsroom UI screenshot)

| UI element | Endpoint |
|---|---|
| Sector chips (`ALL / BANKING / TECH / …`) | `GET /news/sectors`, then `GET /news?sector=…` per chip |
| `N headlines` count | `meta.total_results` from `/news` |
| `Updated 4 min ago · next in 6` indicator | `meta.data_age_min` + `meta.refresh_interval_min` |
| Hero BREAKING card | `articles[0]` — render `title`, `source`, `companies[0]`, `sector`, "ago" |
| Headline list rows | `articles[1..N]` — `companies[0]` as ticker prefix, `source` as small caps |
| `ROUTED TO` cards | Top 3 sectors from `articles[]` grouped by `.sector` |
| `Trending now` sidebar | `GET /news/trending` |
| Volume sparkline | 20 buckets × 3 min from `articles[].published_ist` |
| `HEADLINES TODAY` stat | `meta.total_results` from `/news?hours=24` |
| Bottom ticker | `articles[].title` joined, marquee-scrolled |
| Auto-refresh | Re-call `/news` every 30s — no extra server load (DB read) |

---

## Chat / agent integration via MCP

The API exposes a Model Context Protocol endpoint at `/mcp` so LLM-driven chat systems can call it as a tool without per-endpoint plumbing.

### Discovery

```http
GET /mcp
```

```json
{
  "protocol": "mcp", "version": "2024-11-05",
  "server": {"name": "prism-news", "version": "5.2.0"},
  "tools": [
    {"name": "search_financial_news",  "description": "...", "inputSchema": {...}},
    {"name": "get_market_sentiment",   "description": "...", "inputSchema": {...}},
    {"name": "get_trending_stocks",    "description": "...", "inputSchema": {...}}
  ]
}
```

### Invocation

```http
POST /mcp
Content-Type: application/json

{"tool": "get_market_sentiment", "arguments": {"company": "HDFC Bank", "hours": 24}}
```

### The 3 tools

| Tool | Use when | Returns |
|---|---|---|
| `search_financial_news` | "Show me banking news" / "What's the news on Wipro?" | article list + sentiment per item |
| `get_market_sentiment` | "How is HDFC Bank doing today?" | `{verdict, confidence, breakdown, top_positive, top_negative}` |
| `get_trending_stocks` | "What's hot right now?" | top N companies + aggregate sentiment |

For corporate **filings** (results / dividends / board meetings / etc.) → call the **`prism-filings`** MCP server (separate `/mcp` endpoint at its own base URL).

### Drop into Claude Desktop / Claude Code

`~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "prism-news": {
      "transport": "http",
      "url": "http://<gcp-server>:8001/mcp"
    }
  }
}
```

A pre-written **Claude Code skill** (`prism-news.skill.md`) is included in the repo — drop it into the dev's `~/.claude/skills/` and Claude will know when and how to call all 4 tools.

---

## Data shapes you'll get in articles

### `sentiment` (when populated; `null` otherwise)

```json
{
  "label": "positive | negative | neutral",
  "score": 0.0 - 1.0,
  "provider": "openai | heuristic"
}
```

### `companies` (always present, may be `[]`)

Array of canonical company names. **Aliases normalized**: `HDFC`, `HDFC Bank`, `HDFC Ltd`, `hdfcbank` all show as `"HDFC Bank"`. **4,149 canonical NSE/BSE-listed companies** tracked.

### `sector` (always present, may be `null`)

One of 8 codes: `BANKING | TECH | AUTO | PHARMA | ENERGY | FMCG | METALS | REALTY`.

**Sector resolution priority** (most to least confident):
1. **Company → sector** (CURATED_SECTOR / SECTOR_BY_COMPANY map)
2. **URL-path hint** — feed URL contains `/banking-and-finance/` → `BANKING`, `/tech/` → `TECH`, `/commodities/` → `ENERGY`, etc. (~28 path patterns, see `URL_SECTOR_HINTS` in `feeds_config.py`)
3. **Keyword fallback** — text scan against 8 sector keyword lists

After this layering, ~80% of articles get a sector tag.

### Fine-grained industry / ISIN / NSE symbol

**Not stored on each article** — derivable from `companies[0]` via the bundled `company_aliases.py` helpers:

```python
from company_aliases import get_industry, get_isin, get_nse_symbol
get_industry("Reliance Industries")  # → "Refineries & Marketing"  (screener.in taxonomy)
get_isin("Reliance Industries")      # → "INE002A01018"
get_nse_symbol("Reliance Industries")# → "RELIANCE"
```

### `link` vs `original_link`

`news.google.com` URLs are unwrapped to the real publisher URL when possible. When unwrap succeeded, `link` is the publisher URL and `original_link` keeps the Google URL.

---

## Company taxonomy (4,149 companies — built from canonical sources)

The alias master is generated by `build_company_master.py` from:

| Source | What it provides |
|---|---|
| Postgres `stock_chat.filings_index` | `company_name` + `industry` + `isin` (4,150 distinct pairs) |
| SQLite `nse_scrips` / `scrips` | NSE symbol, BSE code, ISIN, market cap |
| `on_demand_router._SECTOR_INDUSTRIES` | screener.in industry → prism 8-sector chip |

Output: `company_master.json` (~1.3 MB, bundled with the repo).

### Defense-in-depth against false positives

The auto-loaded long-tail uses **strict** matching to avoid the false-positive class (e.g. "Bank of America" wrongly tagging Bank of Baroda):

1. **NSE symbols are NOT added as aliases** — `GLOBAL`, `FOCUS`, `IPL`, `WEALTH` lowercased would collide with English words.
2. **Stopword list (~70 words)** — `india`, `global`, `focus`, `bank`, `of`, `in`, etc. can never become aliases.
3. **2-word prefix** for 3+ word names requires **both words ≥ 3 chars** AND non-stopword. "Wardwizard Innovations & Mobility" gets prefix `"wardwizard innovations"` (works); "Bank of Baroda" does NOT get `"bank of"` (`"of"` is stopword + < 3 chars).
4. **Case-sensitive short acronyms** — `IOC`, `TCS`, `HDFC`, `BOB` only match when written in **uppercase** in the source. Lowercase `"ioc"` in random text does NOT match.
5. **Finance-context filter** for auto-master matches — long-tail (non-curated) names only count if the article also contains a finance signal word: `Q[1-4]|FY|earnings|profit|shares|stock|NSE|BSE|SEBI|RBI|IPO|sensex|nifty|target|dividend|...` Curated 80 are exempt — they're top names that always appear in financial conversations.

### To refresh (run when upstream changes)

```bash
cd NewsRSS
# .env must have POSTGRES_URL and STATE_DB
python build_company_master.py
```

After updating, re-tag existing articles:

```bash
docker compose exec newsapi python load_backup.py --retag-only --batch 2000
```

---

## Practical notes for the frontend dev

- **CORS is wide-open** (`*`) — call from any origin.
- **No auth headers required** — protect with Nginx basic-auth / API gateway at the infra layer if needed.
- **Searches don't trigger heavy fetches** — `/news` without `company` is pure DB (sub-100ms). With `company`, adds a tiny 3-URL Google fetch (~1s).
- **Pagination**: prefer increasing `limit` over walking `page`.
- **Fuzzy dedup is on by default** — turn off with `?fuzzy=false` only if you want every near-duplicate (rare).
- **`hours=24` is cheapest** because of warm index. `hours=168` works but slower.
- **Refresh cadence**: back-end re-fetches every 10 min. Polling `/news` from the UI every 30s is fine — DB cache hits.
- **Hindi articles are filtered at ingest** — Devanagari titles never enter the DB.

## Practical notes for the chat-system dev

- **Use MCP, not raw REST** — the 3 MCP tools cover 90%+ of chat use cases.
- **First-call OpenAI latency is real** — `get_market_sentiment` on a never-queried company takes 5–10s. Subsequent calls are sub-second.
- **When to use which tool:**
  - User asks about a specific stock → `get_market_sentiment`
  - User asks what's moving / trending → `get_trending_stocks`
  - User asks for a list of articles → `search_financial_news`
- **User asks about corporate filings (results / dividends / board meetings)** → route to the `prism-filings` MCP server (separate service).
- **The skill file (`prism-news.skill.md`)** has decision rules + answer-shaping templates.
- **`sentiment` is `null`** in `search_financial_news` results unless the article was previously seen via a company-specific query. Don't claim a verdict if sentiment is null.

---

## API surface summary

| Endpoint | Purpose |
|---|---|
| `GET /` | API info |
| `GET /health` | ops status |
| `GET /stats` | last-24h rollups |
| `GET /news` | main feed; `company`, `sector`, `hours`, `page`, `limit`, `fuzzy` |
| `GET /news/summary` | per-company sentiment + trend |
| `GET /news/trending` | most-mentioned companies |
| `GET /news/sources` | source reliability |
| `GET /news/compare` | multi-company side-by-side |
| `GET /news/companies` | 4,149-company directory |
| `GET /news/sectors` | 8 sector codes |
| `GET /mcp` | tool discovery (3 tools) |
| `POST /mcp` | invoke tool by name |
| `GET /docs` | Swagger UI |
| `GET /openapi.json` | full OpenAPI 3.0 spec |

---

## Files in this repo

| File | Purpose |
|---|---|
| `main.py` | FastAPI app, scheduler (10 min), all endpoints |
| `feeds_config.py` | 73 news RSS feeds + URL_SECTOR_HINTS |
| `llm_provider.py` | OpenAI sentiment client + heuristic fallback chain |
| `company_aliases.py` | curated 80 + auto-loaded master (4,149 names), 5-layer defense against false positives, helpers `get_industry`/`get_isin`/`get_nse_symbol` |
| `company_master.json` | bundled 1.3MB data file |
| `build_company_master.py` | one-shot script to rebuild the master from PG + SQLite |
| `dedup.py` | fuzzy dedup, Google URL resolver, Hindi filter |
| `load_backup.py` | bulk loader / re-tagger for historical data |
| `test_feeds.py` | RSS feed health checker (CI) |
| `prism-news.skill.md` | drop-in Claude Code skill |
| `INTEGRATION_INTAKE.md` | tool-registry intake form |
| `docker-compose.yml` | Mongo + API stack |
| `requirements.txt` | python deps |

---

## Ops / server admin (handover)

### Deploy a new build

```bash
git pull origin main
docker compose build newsapi
docker compose up -d newsapi
docker compose logs -f newsapi
```

Expected first-fetch logs:

```
=== FULL FETCH: 73 feeds ===
=== DONE: 15 new in 95s ===          (every 10 min)
```

### Re-tag after taxonomy / sector-mapping changes

```bash
docker compose exec newsapi python load_backup.py --retag-only --batch 2000
```

### Article count sanity check

```bash
docker compose exec mongodb mongosh -u newsadmin -p "$MONGO_PASSWORD" \
  --eval 'db.getSiblingDB("financial_news").articles.countDocuments()'
```

### Watch feed-fetch health

```bash
docker compose logs -f newsapi 2>&1 | grep --line-buffered -E "FULL FETCH|DONE|feed timeout|feed http|feed error"
```

Steady-state expectations:
- Every 10 min: fetch lands 65–75 of 73 feeds in ~95s, ~10-30 new docs
- `Fetched X articles from Y/73 feeds (8 waves × 10)` — the chunked-wave fetcher

### Required env in `.env`

| Var | Required | Default | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | ✅ | — | sentiment + company extraction |
| `MONGO_URI` | — | `mongodb://localhost:27017` | full URI w/ auth |
| `MONGO_PASSWORD` | ✅ for docker-compose | — | seeded into Mongo container on first boot |
| `DB_NAME` | — | `financial_news` | |
| `OPENAI_BASE_URL` | — | `https://api.openai.com/v1` | override for Azure/Groq/Together |
| `OPENAI_MODEL` | — | `gpt-4o-mini` | |
| `LLM_PROVIDER` | — | `auto` | `auto\|openai\|heuristic` |
| `POSTGRES_URL` | only for `build_company_master.py` | — | not needed at runtime |
| `STATE_DB` | only for `build_company_master.py` | — | path to NSE/BSE `state.db` |

### Tuning knobs (top of `main.py`)

```python
FETCH_INTERVAL_MIN = 10    # scheduler cadence
MIN_FETCH_GAP_MIN  = 5
FEED_TIMEOUT_SEC   = 12    # per-feed HTTP cap
MAX_CONCURRENT     = 30    # TCPConnector total simultaneous connections
MAX_PER_HOST       = 3     # TCPConnector per-host cap
CHUNK_SIZE         = 10    # feeds per wave inside fetch_parallel
CHUNK_PAUSE_SEC    = 3     # gap between waves; smooths outbound load
```

If success rate drops, try `CHUNK_SIZE=5, CHUNK_PAUSE_SEC=5` for an even gentler profile.

### Rollback

```bash
git reset --hard <prev-sha> && docker compose up -d --build newsapi
```

---

## Questions for the integrating dev to ask

- **Auth boundary** — sit behind your Nginx? API gateway? Token check?
- **Refresh cadence** — UI poll every 30s OK, or do you need SSE / websockets?
- **Caching layer** — fronting with CDN/Redis? Most responses are already <200ms from Mongo cache.
- **Custom companies** — add a private/unlisted company → one-line edit in `CURATED_ALIASES` of `company_aliases.py` + retag.
- **Custom sector** — edit `SECTORS` + `CURATED_SECTOR` + `URL_SECTOR_HINTS` + `PRISM_SECTOR_BY_INDUSTRY` in `build_company_master.py`, rebuild master, retag.
- **Custom filing category** — handled by the `prism-filings` service (separate repo).
- **Sentiment freshness** — if you need *all* articles sentiment-scored at ingest (not lazy), one-line change in `parse_feed_bytes` but adds OpenAI cost (~$0.0002 per article × 5k articles/day = ~$1/day).
