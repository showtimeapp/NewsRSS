---
name: prism-news
description: Query live Indian financial news, per-company sentiment (OpenAI-backed), trending stocks, and sector activity for 4,149 NSE/BSE-listed companies. Use whenever the user asks about market reaction to an Indian company, today's news on a stock, what's trending, sector activity, or wants a sentiment verdict on an Indian-listed name. For corporate FILINGS (results / dividends / board meetings / AGM/EGM / M&A / IPOs from NSE/BSE/SEBI/RBI/PIB) call the SEPARATE `prism-filings` service instead.
---

# /prism-news

Live Indian financial-news intelligence backed by **73 RSS feeds** (Economic Times, Mint, Moneycontrol via Google, CNBC TV18, Business Standard, The Hindu, Indian Express, Bloomberg, MarketWatch, etc.), refreshed every 10 minutes.

Plus **OpenAI sentiment** (`gpt-4o-mini`), **4,149 canonical companies** with screener.in industry tags, and 8 sector chips (`BANKING | TECH | AUTO | PHARMA | ENERGY | FMCG | METALS | REALTY`).

> **Out of scope for this skill — call `prism-filings` instead:**
> Corporate filings — results, annual reports, board meetings, AGM/EGM, dividends, M&A, IPOs — are served by a separate standalone service backed by NSE / BSE / SEBI / RBI / PIB official RSS. That service has its own base URL and its own MCP endpoint.

## When to invoke this skill

| User question | Tool |
|---|---|
| *"What's the news on HDFC Bank?"* | `get_market_sentiment` |
| *"How is Reliance doing today?"* | `get_market_sentiment` |
| *"Is TCS bullish?"* | `get_market_sentiment` |
| *"What's trending in the market?"* | `get_trending_stocks` |
| *"Any banking news in the last 6 hours?"* | `search_financial_news` |
| *"Compare sentiment on HDFC, ICICI, SBI"* | `search_financial_news` per company, summarize |
| *"What did Reliance file last week?"* | **route to `prism-filings`** (NOT this skill) |
| *"Q4 results today?"* / *"Any dividends declared?"* | **route to `prism-filings`** |

## Base URL

Set once at session start:

```
PRISM_NEWS_URL = http://<your-server>:8001
```

All endpoints below are paths under this base URL.

---

## Tools (3)

### 1. `get_market_sentiment` — sentiment + verdict for ONE company

Use when the user asks about the mood / outlook / news on a specific Indian-listed company. **Accepts aliases** (`HDFC` / `HDFC Bank` / `HDFC Ltd` all resolve to `HDFC Bank`; `Reliance` → `Reliance Industries`; `TCS` → `Tata Consultancy Services`).

```bash
curl -X POST "$PRISM_NEWS_URL/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"get_market_sentiment","arguments":{"company":"HDFC Bank","hours":24}}'
```

**Returns:**
```json
{
  "tool": "get_market_sentiment",
  "result": {
    "company": "HDFC Bank",
    "verdict": "bullish",                  // bullish | bearish | neutral
    "confidence": 0.42,                    // 0.0 - 1.0 (= |pos% - neg%|)
    "trend": "bullish",                    // recent half vs older half of window
    "avg_score": 0.65,
    "total_articles": 12,
    "breakdown": {"positive": 8, "negative": 1, "neutral": 3},
    "top_positive": [{"title":"...","source":"...","link":"...","published_ist":"..."}],
    "top_negative": [...]
  }
}
```

**Answer pattern:** Lead with verdict + confidence, then cite 1–2 of the strongest headlines from `top_positive`/`top_negative`. Example: *"HDFC Bank looks bullish today (8 of 12 articles positive). Standout: 'HDFC Bank Q4 profit beats estimates' (Economic Times, 14m ago)."*

**Latency:** 5–10s on first call (OpenAI fires per-article). Cached thereafter — sub-second.

---

### 2. `get_trending_stocks` — most-mentioned companies right now

```bash
curl -X POST "$PRISM_NEWS_URL/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"get_trending_stocks","arguments":{"hours":24,"limit":10}}'
```

```json
{
  "result": {
    "hours": 24,
    "trending": [
      {"company":"Reliance Industries","mentions":19,"sentiment":"positive","sector":"ENERGY", ...},
      {"company":"HDFC Bank","mentions":5,"sentiment":"neutral","sector":"BANKING", ...}
    ]
  }
}
```

**Answer pattern:** Bullet the top 5–7 with mention count + sentiment. Group by sector if list crosses 3+ sectors.

---

### 3. `search_financial_news` — list articles, filter by company or sector

```bash
curl -X POST "$PRISM_NEWS_URL/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"search_financial_news","arguments":{"sector":"BANKING","hours":12,"limit":10}}'
```

**Sector enum:** `BANKING | TECH | AUTO | PHARMA | ENERGY | FMCG | METALS | REALTY`

```json
{
  "result": {
    "total": 64, "returned": 10,
    "articles": [
      {"title":"…","source":"Economic Times","published_ist":"...",
       "link":"...","companies":["HDFC Bank"],"sector":"BANKING",
       "sentiment":{"label":"positive","score":0.81,"provider":"openai"}}
    ]
  }
}
```

**Important:** `sentiment` is `null` on most articles in `search_financial_news` output. It only gets populated when a company-specific query (`get_market_sentiment`) flows through OpenAI. Don't claim sentiment if it's null.

---

## When NOT to use this skill

- **Live stock prices / quotes** — this API is news, not prices. Send the user to NSE/BSE or a quote API.
- **Corporate filings (results, dividends, board meetings, AGM/EGM, M&A, IPOs)** — that's the `prism-filings` service, NOT this one.
- **Pre-IPO / private companies** — alias map covers 4,149 NSE/BSE-listed names; unlisted startups won't resolve.
- **Non-Indian markets** — some US/global wire coverage in the feed (Bloomberg, Yahoo, MarketWatch) but company alias map is India-only.
- **Multi-day historical research** — max window is 240 hours (10 days). Older data exists but isn't exposed.
- **Sentiment on never-queried companies in `search_financial_news`** — `sentiment` will be `null`. Call `get_market_sentiment` if user needs a verdict.

## Direct REST (if your chat framework doesn't speak MCP)

| MCP tool | REST equivalent |
|---|---|
| `get_market_sentiment` | `GET /news/summary?company=...&hours=...` |
| `get_trending_stocks` | `GET /news/trending?hours=...&limit=...` |
| `search_financial_news` | `GET /news?company=...&sector=...&hours=...&limit=...` |

Extras not in MCP:
- `GET /news/sources` — per-source uptime + article counts
- `GET /news/compare?companies=HDFC,ICICI,SBI&hours=48` — side-by-side sentiment
- `GET /news/companies` — directory of 4,149 canonical names
- `GET /news/sectors` — 8 sector codes
- `GET /health` — `total_articles`, `last_fetch`, `llm_provider`
- `GET /openapi.json` — full OpenAPI 3.0 spec

## Refresh model — important to understand

- **10-min background scheduler** re-fetches the 73 news feeds.
- **User searches never trigger a full fetch** (avoids overwhelming the network); they hit MongoDB directly.
- `GET /news?company=X` does fire a small per-company Google News fetch (3 URLs).
- Every `/news` response includes `meta.data_age_min` + `meta.refresh_interval_min` so you can tell the user how stale the data is.

## Error handling

- **Unknown company** → API still tries title/description regex; if zero results, response has `total_articles: 0`. Tell the user "no recent news on X" — don't fabricate.
- **API 5xx** → fall back gracefully, don't fabricate. Typical steady-state success is 65–75/73 feeds.
- **OpenAI rate-limited** → LLM chain falls back to heuristic; `sentiment.provider` will say `"heuristic"`. Verdicts directional but lower confidence — convey uncertainty.

## Discovery & health

```bash
curl "$PRISM_NEWS_URL/mcp"                            # returns the 3 tool schemas
curl "$PRISM_NEWS_URL/health"                         # ops: counts, last_fetch, llm_provider
curl "$PRISM_NEWS_URL/openapi.json"                   # full OpenAPI 3.0 spec
```

Healthy `/health` looks like:
```json
{
  "status": "running",
  "collecting_24_7": true,
  "llm_provider": "openai",
  "total_articles": 215000,
  "articles_with_sentiment": 8400,
  "articles_with_companies_tagged": 150000,
  "feeds": 73,
  "last_fetch": "2026-05-31 13:48:33 IST"
}
```

## Quality guarantees the tagger gives you

The company tagger (`companies: [...]` field) is hardened against 5 classes of false positives:

1. **NSE symbols are not blindly aliased** — `GLOBAL`, `FOCUS`, `IPL`, `WEALTH` no longer tag random text
2. **Short uppercase acronyms are case-sensitive** — `BOB` matches "BOB Q4" but NOT "Bob the carpenter"
3. **Common English words can't become aliases** — `india`, `global`, `bank`, `of`, etc. are stopword'd
4. **Long-tail companies need finance context** — "Persistent Systems is a great workplace" doesn't tag; "Persistent Systems Q4 earnings" does
5. **"Bank of X" disambiguation** — Bank of America headlines no longer tag Bank of Baroda

If you ever notice a wrongly-tagged company, flag it — it's likely a new false-positive class worth adding to the stopword list.
