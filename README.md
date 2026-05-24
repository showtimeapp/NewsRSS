# Prism Financial News API — Integration Guide

Live REST API for Indian financial news with OpenAI sentiment, company normalization, sector tagging, and an MCP endpoint for chat/agent integration.

**Base URL (production):** `http://<gcp-server>:8001`
**Swagger UI:** `http://<gcp-server>:8001/docs`

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
    "feeds_configured": 82, "response_time_ms": 142,
    "last_full_fetch_ist": "2026-05-24 13:48:48 IST",
    "sentiment_provider": "openai",
    "cache_status": "fresh -> DB only"
  },
  "articles": [
    {
      "title": "HDFC Bank Q4 profit beats estimates",
      "description": "...",
      "source": "Economic Times",
      "published_ist": "2026-05-24 13:48:48 IST",
      "published_dt": "2026-05-24T08:18:48Z",
      "link": "https://economictimes.indiatimes.com/...",
      "original_link": "https://news.google.com/...",   // present only if Google redirect was resolved
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
| `resolve_links` | `true` | unwrap `news.google.com/rss/articles/...` → publisher URL |

**Sentiment is lazy:** `sentiment` is `null` on most articles. It's populated when the article is returned via a company-specific query. Once analyzed, the result is persisted to Mongo and reused on every subsequent request.

### 2. Per-company summary card

```http
GET /news/summary?company=HDFC%20Bank&hours=24
```

```json
{
  "company": "HDFC Bank",
  "input": "HDFC Bank",
  "total_articles": 45,
  "sentiment_breakdown": {"positive": 28, "negative": 8, "neutral": 9},
  "avg_score": 0.72,
  "trend": "bullish",
  "trend_detail": {
    "recent_half": {"positive": 16, "negative": 3},
    "older_half":  {"positive": 12, "negative": 5}
  },
  "top_positive": [{"title":"…","source":"…","link":"…","sentiment":{…}}],
  "top_negative": [...],
  "provider": "openai"
}
```

**First-call latency**: 5–10s while OpenAI analyzes fresh articles in parallel. Subsequent calls for the same company in the same window: sub-second (Mongo-cached).

### 3. Trending companies sidebar

```http
GET /news/trending?hours=24&limit=10
```

```json
{
  "hours": 24,
  "trending": [
    {"company":"Reliance Industries","mentions":19,"sentiment":"positive","sector":"ENERGY",
     "sentiment_breakdown":{"positive":12,"negative":2,"neutral":5}},
    {"company":"NTPC","mentions":11,"sentiment":"neutral","sector":"ENERGY", ...}
  ]
}
```

### 4. Multi-company comparison

```http
GET /news/compare?companies=HDFC,ICICI,SBI&hours=48
```

Returns each company's summary side-by-side, ranked by `avg_score` (best → worst). Useful for side-by-side charts.

### 5. Source reliability strip

```http
GET /news/sources?hours=24
```

Per-source article count, description coverage %, minutes since last article, fresh/stale flag. Use this to render a "data sources" footer or admin dashboard.

### 6. Directory endpoints (for dropdowns)

```http
GET /news/companies   → {"total": 83, "companies": ["HDFC Bank", "ICICI Bank", ...]}
GET /news/sectors     → {"sectors": ["BANKING","TECH","AUTO","PHARMA","ENERGY","FMCG","METALS","REALTY"]}
```

### 7. Health

```http
GET /health
```

```json
{
  "status": "running",
  "collecting_24_7": true,
  "llm_provider": "openai",
  "total_articles": 205474,
  "articles_with_sentiment": 1247,
  "articles_with_companies_tagged": 15319,
  "last_24h": 5141, "last_1h": 800,
  "sources_active": 229,
  "feeds": 82,
  "last_fetch": "2026-05-24 13:48:48 IST"
}
```

---

## Frontend wire-up cheat-sheet (matches the newsroom UI screenshot)

| UI element | Endpoint |
|---|---|
| Sector chips (`ALL / BANKING / TECH / …`) | `GET /news/sectors` then `GET /news?sector=…` per chip |
| `N headlines` count | `meta.total_results` from `/news` |
| Hero BREAKING card | `articles[0]` — render `title`, `source`, `companies[0]`, `sector`, "ago" from `published_ist` |
| Headline list rows | `articles[1..N]` — `published_ist` → ago, `companies[0]` → ticker prefix, `source` → right-side small caps |
| `ROUTED TO` cards | Top 3 sectors from `articles[]` grouped by `.sector` (e.g. BANKING DESK, SECTOR TRACKER, ENERGY LEAD) |
| Volume sparkline | 20 buckets × 3 min from `articles[].published_ist` over the last hour |
| `HEADLINES TODAY` stat | `meta.total_results` from `GET /news?hours=24` |
| `MEDIAN LATENCY` stat | `meta.response_time_ms / 1000` (or roll your own histogram from `/news/sources`) |
| Bottom ticker | `articles[].title` joined, marquee-scrolled |
| Auto-refresh | Re-call `/news` every 30s |

---

## Chat / agent integration via MCP

The API exposes a Model Context Protocol endpoint at `/mcp` so LLM-driven chat systems can call it as a tool without you writing per-endpoint plumbing.

### Discovery

```http
GET /mcp
```

```json
{
  "protocol": "mcp", "version": "2024-11-05",
  "server": {"name": "prism-news", "version": "5.0.0"},
  "tools": [
    {"name": "search_financial_news", "description": "...", "inputSchema": {...}},
    {"name": "get_market_sentiment",  "description": "...", "inputSchema": {...}},
    {"name": "get_trending_stocks",   "description": "...", "inputSchema": {...}}
  ]
}
```

### Invocation

```http
POST /mcp
Content-Type: application/json

{"tool": "get_market_sentiment", "arguments": {"company": "HDFC Bank", "hours": 24}}
```

```json
{"tool": "get_market_sentiment", "result": {"verdict":"bullish", "confidence":0.42, ...}}
```

### The three tools

| Tool | Use when | Returns |
|---|---|---|
| `search_financial_news` | "Show me banking news" / "What's the news on Wipro?" | article list + sentiment per item |
| `get_market_sentiment` | "How is HDFC Bank doing today?" | `{verdict, confidence, breakdown, top_positive, top_negative}` |
| `get_trending_stocks` | "What's hot right now?" | top N companies + aggregate sentiment |

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

### Drop into a custom chat system

Whenever your LLM calls one of the three tool names, forward the call to:

```javascript
async function callPrismTool(toolName, args) {
  const r = await fetch(`${PRISM_NEWS_URL}/mcp`, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({tool: toolName, arguments: args}),
  });
  if (!r.ok) throw new Error(`prism-news ${r.status}`);
  return (await r.json()).result;
}
```

A pre-written **Claude Code skill** (`prism-news.skill.md`) is included in the repo — drop it into the dev's `~/.claude/skills/` directory and Claude will know when and how to call the API.

---

## Data shapes you'll get in articles

### `sentiment` (when populated)

```json
{
  "label": "positive | negative | neutral",
  "score": 0.0 - 1.0,
  "provider": "openai | heuristic"
}
```

- `provider: "openai"` — analyzed by gpt-4o-mini, high quality
- `provider: "heuristic"` — fell back to keyword scoring (OpenAI was down or rate-limited); verdicts still directional but lower confidence

### `companies` (always present, may be `[]`)

Array of canonical company names. Aliases are normalized: `HDFC`, `HDFC Bank`, `HDFC Ltd` all show as `"HDFC Bank"`. ~80 Indian-listed companies tracked — see `company_aliases.py` for the full list, or `GET /news/companies`.

### `sector` (always present, may be `null`)

One of 8 codes: `BANKING | TECH | AUTO | PHARMA | ENERGY | FMCG | METALS | REALTY`. Detected via company → sector mapping first, keyword fallback otherwise. ~60% of articles get a sector tag.

### `link` vs `original_link`

Most `news.google.com` redirect URLs are unwrapped to the real publisher URL. When that happens, `link` contains the publisher URL and `original_link` keeps the Google URL for reference. If unwrap failed or wasn't a Google URL to begin with, only `link` is present.

---

## Practical notes for the frontend dev

- **CORS is wide-open** (`*`) — call from any origin.
- **No auth headers required** — protect with nginx basic-auth / API gateway at the infra layer if needed.
- **Pagination**: prefer increasing `limit` over walking `page` for the newsroom UI (most users only see the top 30).
- **Fuzzy dedup is on by default** — turn off with `?fuzzy=false` only if you want every near-duplicate (rare; usually noise).
- **`hours=24` is the cheapest query** because it uses the warm index. Asking for `hours=168` works but is slower.
- **Refresh cadence**: the API back-end re-fetches every 10 min internally. Polling `/news` from the UI every 30s is fine — most responses serve from cache (`cache_status: "fresh -> DB only"`).
- **Article counts**: at any given time there are ~200k articles in the DB across all time, ~5k fetched in the last 24h.
- **Hindi articles are filtered at ingest** — you'll never see Devanagari in the feed.

---

## Practical notes for the chat-system dev

- **Use MCP, not raw REST** — the three MCP tools cover 90% of chat use cases and give consistent shapes.
- **First-call OpenAI latency is real** — `get_market_sentiment` on a never-queried company takes 5–10s while OpenAI scores ~10 articles. Cache hit on second call.
- **When to use which tool:**
  - User asks about a specific stock → `get_market_sentiment`
  - User asks what's moving / trending → `get_trending_stocks`
  - User asks for a list of articles → `search_financial_news`
- **The skill file (`prism-news.skill.md`)** has decision rules + answer-shaping templates. Hand it to anyone configuring a Claude-based agent.
- **Don't fabricate** — if a query returns `total_articles: 0`, tell the user "no news found", don't invent.

---

## API surface summary

| Endpoint | Purpose |
|---|---|
| `GET /` | API info |
| `GET /health` | ops status |
| `GET /stats` | last-24h rollups (sources, sentiment, sectors) |
| `GET /news` | main feed; `company`, `sector`, `hours`, `page`, `limit`, `fuzzy`, `resolve_links` |
| `GET /news/summary` | per-company sentiment + trend |
| `GET /news/trending` | most-mentioned companies |
| `GET /news/sources` | source reliability |
| `GET /news/compare` | multi-company side-by-side |
| `GET /news/companies` | canonical company directory |
| `GET /news/sectors` | sector code list |
| `GET /mcp` | tool discovery |
| `POST /mcp` | invoke tool by name |
| `GET /docs` | Swagger UI |

---

## Files in this repo

| File | Purpose |
|---|---|
| `main.py` | FastAPI app, all endpoints |
| `feeds_config.py` | 82 RSS feed URLs + Google News queries |
| `llm_provider.py` | OpenAI sentiment client + heuristic fallback |
| `company_aliases.py` | 80+ canonical names, aliases, sector mapping |
| `dedup.py` | fuzzy dedup, Google URL resolver, Hindi filter |
| `load_backup.py` | bulk loader / re-tagger for historical data |
| `test_feeds.py` | RSS feed health checker (CI) |
| `prism-news.skill.md` | Drop-in Claude Code skill for chat integration |
| `docker-compose.yml` | Mongo + API stack |
| `requirements.txt` | python deps |

---

## Ops / server admin (handover)

```bash
# Deploy a new build
git pull origin main
docker compose build newsapi
docker compose up -d newsapi              # in-place swap, mongo untouched
docker compose logs -f newsapi            # confirm "LLM provider chain active: openai"

# Backfill / re-tag existing rows after an alias-map change
docker compose exec newsapi python load_backup.py --retag-only --batch 2000

# Article count sanity check
docker compose exec mongodb mongosh -u newsadmin -p "$MONGO_PASSWORD" \
  --eval 'db.getSiblingDB("financial_news").articles.countDocuments()'
```

**Required env in `.env`:**

| Var | Required | Default | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | ✅ | — | sentiment + company extraction |
| `MONGO_URI` | — | `mongodb://localhost:27017` | full URI w/ auth |
| `MONGO_PASSWORD` | ✅ for docker-compose | — | seeded into Mongo container on first boot |
| `DB_NAME` | — | `financial_news` | |
| `OPENAI_BASE_URL` | — | `https://api.openai.com/v1` | override for Azure/Groq/Together/etc. |
| `OPENAI_MODEL` | — | `gpt-4o-mini` | |
| `LLM_PROVIDER` | — | `auto` | `auto\|openai\|heuristic` |

**Rollback:** `git reset --hard <prev-sha> && docker compose up -d --build newsapi`.

---

## Questions for the integrating dev to ask

- **Auth boundary** — should I sit behind your nginx? An API gateway? Token check?
- **Refresh cadence** — UI poll every 30s OK, or do you need server-sent events / websockets?
- **Caching layer** — fronting with CDN/Redis? Most responses are already <200ms from Mongo cache.
- **Custom companies** — need to add a private/unlisted company to the alias map? One-line edit in `company_aliases.py` + retag.
- **Custom sector** — same: edit `SECTORS` + `SECTOR_BY_COMPANY` + `SECTOR_KEYWORDS`, retag.
