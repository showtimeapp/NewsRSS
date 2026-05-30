---
name: prism-news
description: Query live Indian financial news, per-company sentiment (OpenAI-backed), trending stocks, corporate filings (Q4 results / dividends / board meetings / AGM/EGM / M&A / IPOs), and sector activity for 4,149 NSE/BSE-listed companies. Use whenever the user asks about market reaction to an Indian company, today's news on a stock, what's trending, sector activity, recent corporate filings, or wants a sentiment verdict on an Indian-listed name.
---

# /prism-news

Live Indian financial-news intelligence backed by **80 RSS feeds** across two pipelines:

- **News pipeline** (73 feeds, refreshed every 10 min): Economic Times, Mint, Moneycontrol via Google, CNBC TV18, Business Standard, The Hindu, Indian Express, Bloomberg, MarketWatch, etc.
- **Filings pipeline** (7 feeds, refreshed every 30 min): RBI press releases / notifications / speeches / publications, SEBI, BSE notices, PIB Ministry of Finance.

Plus **OpenAI sentiment** (`gpt-4o-mini`), **4,149 canonical companies** with screener.in industry tags, 8-sector chips, and a `/filings` view that extracts material corporate events (Q4 results, dividends, board meetings, AGM/EGM, M&A, IPOs) from both pipelines.

## When to invoke this skill

| User question | Tool |
|---|---|
| *"What's the news on HDFC Bank?"* | `get_market_sentiment` |
| *"How is Reliance doing today?"* | `get_market_sentiment` |
| *"Is TCS bullish?"* | `get_market_sentiment` |
| *"What's trending in the market?"* | `get_trending_stocks` |
| *"Any banking news in the last 6 hours?"* | `search_financial_news` |
| **"What did Reliance file last week?"** | **`list_company_filings`** |
| **"Q4 results from any pharma stock today?"** | **`list_company_filings`** with `filing_type=Result` |
| **"Has any company declared dividends recently?"** | **`list_company_filings`** with `filing_type=Corp Action`** |
| **"Latest board meetings in banking sector?"** | **`list_company_filings`** with `sector=BANKING&filing_type=Board Meeting`** |
| *"Compare sentiment on HDFC, ICICI, SBI"* | `search_financial_news` per company, summarize |
| *"What industry is Persistent Systems in?"* | `search_financial_news` for the company, read `companies[0]` + `sector`; for fine-grained industry, use REST `/news/companies` once at session start |

## Base URL

Set once at session start:

```
PRISM_NEWS_URL = http://<your-server>:8001
```

All endpoints below are paths under this base URL.

---

## Tools (4)

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
       "pipeline":"news",
       "sentiment":{"label":"positive","score":0.81,"provider":"openai"}}
    ]
  }
}
```

**Important:** `sentiment` is `null` on most articles in `search_financial_news` output. It only gets populated when a company-specific query (`get_market_sentiment`) flows through OpenAI. Don't claim sentiment if it's null.

---

### 4. `list_company_filings` — corporate-event filings (NEW)

Use when the user asks about material corporate events: results, annual reports, board meetings, dividends, AGM/EGM, M&A, IPOs, listings, target-price changes, etc.

```bash
# All filings for a company in the last 48 hours
curl -X POST "$PRISM_NEWS_URL/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"list_company_filings","arguments":{"company":"Reliance","hours":48,"limit":10}}'

# Filter to one filing type
curl -X POST "$PRISM_NEWS_URL/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"list_company_filings","arguments":{"filing_type":"Result","hours":24,"limit":20}}'
```

**`filing_type` enum (12 categories + 1 prefix):**

| Category | Triggers on |
|---|---|
| `Result` | Q1/Q2/Q3/Q4, quarterly results, earnings, net profit, PAT, EBITDA |
| `Annual Report` | annual report, FYxx annual |
| `Board Meeting` | board meeting, board approves/considers/recommends |
| `AGM/EGM` | AGM, EGM, annual general meeting, EGM |
| `Corp Action` | dividend, bonus, stock split, buyback, rights issue |
| `Insider Trading` | insider trading, SAST, promoter stake |
| `M&A` | merger, acquisition, takeover, divestment, demerger |
| `IPO` | IPO, FPO, QIP, DRHP, RHP, oversubscribed |
| `Listing` | listed on, delisted, new listing, listing gains |
| `Allotment` | allotment, preferential issue, equity allotment |
| `Guidance` | guidance, outlook, forecast, management commentary |
| `Rating/Target` | target price, upgrade/downgrade to buy/sell |
| `Official` *(prefix only)* | article came from RBI/SEBI/BSE/PIB direct |

**Returns** (lean for chat):
```json
{
  "result": {
    "company": "Reliance",
    "filing_type": "Result",
    "total": 8, "returned": 8,
    "data_age_min": 4,
    "filings": [
      {
        "title": "Reliance Industries Q4 profit jumps 18% to Rs 19,407 crore",
        "source": "Economic Times",
        "published_ist": "2026-05-31 16:30:00 IST",
        "link": "...",
        "companies": ["Reliance Industries"],
        "sector": "ENERGY",
        "filing_types": ["Result"]                  // can be multi-category
      },
      {
        "title": "Reliance to consider buyback at board meeting June 5",
        "filing_types": ["Board Meeting", "Corp Action"]
      },
      {
        "title": "RBI issues notification on agency bank pension disbursement",
        "filing_types": ["Official"]               // from regulator direct
      }
    ]
  }
}
```

**Answer pattern:** Lead with the most material filing (results > dividends > board meeting > guidance). Quote source + when. Example: *"Reliance had two material filings this week: Q4 profit jumped 18% to ₹19,407 cr (Economic Times, 2h ago); also a board meeting June 5 to consider buyback (Mint, 1d ago)."*

**Latency:** ~200ms (DB filter, no LLM call).

---

## When NOT to use this skill

- **Live stock prices / quotes** — this API is news, not prices. Send the user to NSE/BSE or a quote API.
- **Pre-IPO / private companies** — alias map covers 4,149 NSE/BSE-listed names; unlisted startups won't resolve.
- **Non-Indian markets** — some US/global wire coverage in the feed (Bloomberg, Yahoo, MarketWatch) but company alias map is India-only.
- **Multi-day historical research** — max window is 240 hours (10 days). Older data exists but isn't exposed.
- **Sentiment on never-queried companies in `search_financial_news` / `list_company_filings`** — `sentiment` will be `null`. Call `get_market_sentiment` if user needs a verdict.

## Direct REST (if your chat framework doesn't speak MCP)

| MCP tool | REST equivalent |
|---|---|
| `get_market_sentiment` | `GET /news/summary?company=...&hours=...` |
| `get_trending_stocks` | `GET /news/trending?hours=...&limit=...` |
| `search_financial_news` | `GET /news?company=...&sector=...&hours=...&limit=...` |
| **`list_company_filings`** | **`GET /filings?company=...&sector=...&filing_type=...&hours=...&limit=...`** |

Extras not in MCP:
- `GET /news/sources` — per-source uptime + article counts
- `GET /news/compare?companies=HDFC,ICICI,SBI&hours=48` — side-by-side sentiment
- `GET /news/companies` — directory of 4,149 canonical names
- `GET /news/sectors` — 8 sector codes
- `GET /filings/categories` — 12 filing categories
- `GET /health` — `total_articles`, `last_news_fetch`, `last_filings_fetch`, `llm_provider`
- `GET /openapi.json` — full OpenAPI 3.0 spec

## Refresh model — important to understand

- **News pipeline:** 10-min background scheduler re-fetches 73 news feeds.
- **Filings pipeline:** 30-min separate scheduler re-fetches 7 official regulator/exchange feeds (RBI/SEBI/BSE/PIB) — polite cadence for gov.in domains.
- **User searches never trigger a full fetch** (avoids overwhelming the network); they hit MongoDB directly.
- `GET /news?company=X` does fire a small per-company Google News fetch (3 URLs).
- Every `/news` and `/filings` response includes pipeline-specific `age_min` + `refresh_interval_min` so you can tell the user how stale the data is.

## Error handling

- **Unknown company** → API still tries title/description regex; if zero results, response has `total_articles: 0` / `total: 0`. Tell the user "no recent news on X" — don't fabricate.
- **API 5xx** → fall back gracefully, don't fabricate. Typical steady-state success is 65–75/73 feeds.
- **OpenAI rate-limited** → LLM chain falls back to heuristic; `sentiment.provider` will say `"heuristic"`. Verdicts directional but lower confidence — convey uncertainty.
- **HTTP 403 on Moneycontrol** → publisher-side IP block, expected; Google News query covers Moneycontrol content via `site:moneycontrol.com`.

## Discovery & health

```bash
curl "$PRISM_NEWS_URL/mcp"                            # returns the 4 tool schemas
curl "$PRISM_NEWS_URL/health"                         # ops: counts, last_fetch (both pipelines), llm_provider
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
  "articles_from_filings_pipeline": 47,
  "feeds_total": 80,
  "feeds_news_pipeline": 73,
  "feeds_filings_pipeline": 7,
  "last_news_fetch":    "2026-05-31 13:48:33 IST",
  "last_filings_fetch": "2026-05-31 13:30:12 IST",
  "news_interval_min":    10,
  "filings_interval_min": 30
}
```

## Quality guarantees the tagger gives you

Recent work (May 2026) hardened the company tagger against 4 classes of false positives. When you call any of the tools, the `companies: [...]` array on each article is now reliable because:

1. **NSE symbols are not blindly aliased** — `GLOBAL`, `FOCUS`, `IPL`, `WEALTH` no longer tag random text
2. **Short uppercase acronyms are case-sensitive** — `BOB` matches "BOB Q4" but NOT "Bob the carpenter"
3. **Common English words can't become aliases** — `india`, `global`, `bank`, `of`, etc. are stopword'd
4. **Long-tail companies need finance context** — "Persistent Systems is a great workplace" doesn't tag; "Persistent Systems Q4 earnings" does
5. **"Bank of X" disambiguation** — Bank of America headlines no longer tag Bank of Baroda

If you ever notice a wrongly-tagged company in `search_financial_news` or `list_company_filings` output, flag it — it's likely a new false-positive class worth adding to the stopword list.
