---
name: prism-news
description: Query live Indian financial news, per-company sentiment, and trending stocks via the Prism News API. Use whenever the user asks about market reaction to a company, the day's news on a stock, what's trending, or which Indian sector is in the spotlight.
---

# /prism-news

Live Indian financial-news intelligence backed by 82 RSS feeds, OpenAI sentiment, and 8-sector tagging. Use this skill anytime the user asks:

- *"What's the news on HDFC Bank?"*  → `get_market_sentiment`
- *"How is Reliance doing today?"*  → `get_market_sentiment`
- *"What's trending in the market?"*  → `get_trending_stocks`
- *"Any banking news in the last 6 hours?"*  → `search_financial_news`
- *"Compare sentiment on HDFC, ICICI, SBI"*  → `search_financial_news` per company, summarize

## Base URL

Set once at session start:

```
PRISM_NEWS_URL = http://<your-server>:8001
```

All endpoints below are paths under this base URL.

---

## Tools (3)

### 1. `get_market_sentiment` — sentiment + verdict for ONE company

Use when the user asks about the mood/outlook/news on a specific Indian-listed company. Accepts aliases (`HDFC`, `HDFC Bank`, `HDFC Ltd` all resolve to `HDFC Bank`; `Reliance` → `Reliance Industries`; `TCS` → `Tata Consultancy Services`).

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
    "confidence": 0.25,                    // 0.0 - 1.0, |pos% - neg%|
    "trend": "neutral",                    // recent half vs older half of window
    "avg_score": 0.45,
    "total_articles": 12,
    "breakdown": {"positive": 8, "negative": 1, "neutral": 3},
    "top_positive": [{"title":"...","source":"...","link":"..."}],
    "top_negative": [{"title":"...","source":"...","link":"..."}]
  }
}
```

**Answer pattern:** Lead with the verdict + confidence in plain English, then cite 1–2 of the strongest headlines from `top_positive` / `top_negative` with the source. Example: *"HDFC Bank looks bullish today (8 of 12 articles positive). The standout: 'HDFC Bank Q4 profit beats estimates' (Economic Times)."*

**Latency note:** First call for a fresh company can take 5–10s (OpenAI fires per-article). Subsequent calls cached in DB → sub-second.

---

### 2. `get_trending_stocks` — most-mentioned companies right now

Use when the user asks what's hot, what's moving, or what's in the news. Returns top N by mention count over the window, each with aggregate sentiment.

```bash
curl -X POST "$PRISM_NEWS_URL/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"get_trending_stocks","arguments":{"hours":24,"limit":10}}'
```

**Returns:**
```json
{
  "result": {
    "hours": 24,
    "trending": [
      {"company":"Reliance Industries","mentions":19,"sentiment":"positive","sector":"ENERGY",
       "sentiment_breakdown":{"positive":12,"negative":2,"neutral":5}},
      {"company":"HDFC Bank","mentions":5,"sentiment":"neutral","sector":"BANKING", ...}
    ]
  }
}
```

**Answer pattern:** Bullet the top 5–7 with mention count and sentiment. Group by sector if the list crosses 3+ sectors. Example: *"Most-talked stocks in the last 24h: **Reliance** (19 mentions, positive) leads, followed by NTPC and SBI in energy/banking…"*

---

### 3. `search_financial_news` — list articles, filter by company or sector

Use when the user wants a list of headlines, not aggregates. Filter by `company` (single name), `sector` (one of `BANKING | TECH | AUTO | PHARMA | ENERGY | FMCG | METALS | REALTY`), or neither (all news).

```bash
curl -X POST "$PRISM_NEWS_URL/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"search_financial_news","arguments":{"sector":"BANKING","hours":12,"limit":10}}'

curl -X POST "$PRISM_NEWS_URL/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"search_financial_news","arguments":{"company":"Tata Motors","hours":48,"limit":5}}'
```

**Returns:**
```json
{
  "result": {
    "total": 64,
    "returned": 10,
    "articles": [
      {"title":"…","source":"Economic Times","published_ist":"2026-05-24 13:48:48 IST",
       "link":"https://economictimes.indiatimes.com/…",
       "companies":["HDFC Bank"],"sector":"BANKING",
       "sentiment":{"label":"positive","score":0.81,"provider":"openai"}}
    ]
  }
}
```

**Answer pattern:** Quote the headline + source + when, link inline. For >5 results, bullet the headlines and offer to drill into any one. *"Recent banking headlines: 1. 'HDFC Bank Q4 profit beats…' (ET, 12m ago). 2. 'ICICI raises home loan rate…' (Mint, 1h ago)…"*

---

## When NOT to use this skill

- **Live stock prices / quotes** — this API is news, not prices. Send the user to NSE/BSE or a quote API.
- **Pre-IPO / private companies** — coverage is biased toward NSE/BSE-listed names (~80 canonical companies tracked).
- **Non-Indian markets** — there's some US/global coverage in the feed but the alias map is India-only. Use a different tool for US/EU equities.
- **Multi-day historical research** — max window is 240 hours (10 days). Older data exists in MongoDB but isn't exposed via these tools.

## Direct REST (if you don't want to use MCP)

If your chat framework doesn't speak MCP, hit the REST equivalents:

| MCP tool | REST equivalent |
|---|---|
| `get_market_sentiment` | `GET /news/summary?company=...&hours=...` |
| `get_trending_stocks` | `GET /news/trending?hours=...&limit=...` |
| `search_financial_news` | `GET /news?company=...&sector=...&hours=...&limit=...` |

Plus extras not in MCP:
- `GET /news/sources` — per-source uptime + article counts
- `GET /news/compare?companies=HDFC,ICICI,SBI&hours=48` — side-by-side sentiment
- `GET /news/companies` — directory of 80 canonical names
- `GET /news/sectors` — list of 8 sector codes

## Error handling

- Unknown company → API still tries title/description regex; if zero results, response has `total_articles: 0` and empty breakdown. Tell the user "no recent news on X" and suggest checking the spelling or trying `get_trending_stocks`.
- API down / 5xx → fall back gracefully, don't fabricate sentiment.
- Rate limit on OpenAI → sentiment falls through to heuristic; `provider` field will say `"heuristic"` instead of `"openai"`. Verdicts are still valid but lower confidence.

## Discovery

```bash
curl "$PRISM_NEWS_URL/mcp"     # returns tool schemas
curl "$PRISM_NEWS_URL/health"  # ops: total articles, last fetch, active sources, provider
```
