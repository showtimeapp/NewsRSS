# Prism Financial News API (v5.0)

A FastAPI service that aggregates **82+ RSS feeds** from 24+ publishers, tags articles by **company** and **sector**, runs **LLM-powered sentiment**, and exposes the data via REST + an **MCP endpoint** for Claude tool integration.

```
              ┌─────────── prism news api ───────────┐
              │                                       │
RSS feeds ───►│ fetch → parse → dedup → tag → store  │──► MongoDB
(82, 10min)   │                       │      │       │
              │                       ▼      ▼       │
              │             companies+sector  hindi  │
              │                       │      filter  │
              │                       ▼              │
              │             LLM (openai / hf / heur.)│
              │                       │              │
              └─────── REST + /mcp endpoint ─────────┘
                            │
                  ┌─────────┼─────────────┐
                  ▼         ▼             ▼
              your frontend  Claude (MCP)  any client
```

## What's new in v5

| Feature | Endpoint / Module |
|---|---|
| Per-company sentiment summary + trend | `GET /news/summary?company=...` |
| Most-mentioned companies | `GET /news/trending` |
| Source reliability stats | `GET /news/sources` |
| Multi-company side-by-side | `GET /news/compare?companies=a,b,c` |
| Sector filter (8 sectors) | `GET /news?sector=BANKING` |
| Company directory | `GET /news/companies`, `GET /news/sectors` |
| **MCP server** (Claude tools) | `GET /mcp`, `POST /mcp` |
| LLM provider chain (OpenAI → heuristic) | `llm_provider.py` |
| Company aliases ("HDFC" = "HDFC Bank") | `company_aliases.py` |
| Fuzzy dedup, Google News URL resolver, Hindi filter | `dedup.py` |
| Bulk loader for `financial_news_backup.json` | `load_backup.py` |

## Endpoints

### `GET /news`
Main feed. Back-compatible; new query params on top.

| Param | Default | Notes |
|---|---|---|
| `company` | — | CSV: `HDFC,ICICI,TCS`. Aliases accepted. |
| `sector` | — | `BANKING\|TECH\|AUTO\|PHARMA\|ENERGY\|FMCG\|METALS\|REALTY` |
| `hours` | 24 | 1–240 |
| `page`, `limit` | 1, 50 | pagination |
| `fuzzy` | `true` | collapse near-duplicate titles (token-set ≥ 0.85) |
| `resolve_links` | `true` | resolve `news.google.com/rss/articles/...` → publisher URL |

Each article now includes:
```json
{
  "title": "...",
  "source": "Economic Times",
  "published_ist": "2026-05-24 13:48:48 IST",
  "link": "https://economictimes.indiatimes.com/...",
  "original_link": "https://news.google.com/...",      // present only if resolved
  "companies": ["HDFC Bank"],
  "sector": "BANKING",
  "sentiment": {"label": "positive", "score": 0.81, "provider": "openai"}
}
```

### `GET /news/summary?company=HDFC Bank`
```json
{
  "company": "HDFC Bank",
  "total_articles": 45,
  "sentiment_breakdown": {"positive": 28, "negative": 8, "neutral": 9},
  "avg_score": 0.72,
  "trend": "bullish",
  "trend_detail": {"recent_half": {...}, "older_half": {...}},
  "top_positive": [...],
  "top_negative": [...]
}
```

### `GET /news/trending?hours=24&limit=20`
```json
{
  "trending": [
    {"company": "ICICI Bank", "mentions": 45, "sentiment": "positive",
     "sentiment_breakdown": {"positive": 30, "negative": 5, "neutral": 10},
     "sector": "BANKING"}
  ]
}
```

### `GET /news/sources?hours=24`
Per-source article counts, description coverage %, minutes since last article, fresh/stale flag.

### `GET /news/compare?companies=HDFC,ICICI,SBI&hours=48`
Side-by-side sentiment for multiple companies, ranked by `avg_score`.

### `GET /news/companies`, `GET /news/sectors`
Directory endpoints for dropdowns.

---

## MCP (Claude tool) integration

The `/mcp` endpoint follows MCP tool-discovery + invocation conventions. Three tools are exposed:

| Tool | Input | Output |
|---|---|---|
| `search_financial_news` | `{company?, sector?, hours?, limit?}` | articles + sentiment |
| `get_market_sentiment` | `{company, hours?}` | `{verdict: bullish\|bearish\|neutral, confidence, breakdown, top_positive, top_negative}` |
| `get_trending_stocks` | `{hours?, limit?}` | trending company list |

### Discovery
```
GET /mcp
```
```json
{
  "protocol": "mcp", "version": "2024-11-05",
  "server": {"name": "prism-news", "version": "5.0.0"},
  "tools": [...]
}
```

### Invocation
```
POST /mcp
{"tool": "get_market_sentiment", "arguments": {"company": "HDFC Bank", "hours": 24}}
```

### Claude Desktop config

```json
{
  "mcpServers": {
    "prism-news": {
      "transport": "http",
      "url": "http://your-server:8000/mcp"
    }
  }
}
```

Or wire it into your own chat system by calling `POST /mcp` whenever the LLM invokes one of the three tool names.

---

## LLM provider chain

Sentiment + company extraction use a fallback chain. Set `LLM_PROVIDER` in `.env`:

| `LLM_PROVIDER` | Behavior |
|---|---|
| `auto` (default) | OpenAI if `OPENAI_API_KEY` set → heuristic |
| `openai` | OpenAI only |
| `heuristic` | Keyword-based, no API calls |

### Use a different OpenAI-compatible API
Set `OPENAI_BASE_URL` (e.g. Azure, Together, vLLM, Groq). The model is controlled by `OPENAI_MODEL` (default `gpt-4o-mini`).

```env
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=llama-3.3-70b-versatile
```

---

## Loading the 205k-article backup

```bash
# Make sure MongoDB is up first
python load_backup.py                              # full load, with company/sector tagging
python load_backup.py --skip-tagging               # faster, leaves companies=[] / sector=null
python load_backup.py --retag-only                 # re-tag existing collection only
python load_backup.py --batch 2000                 # tune batch size
```

The loader:
1. Normalizes `{$oid}` / `{$date}` from mongoexport format
2. Drops Hindi articles
3. Detects companies + sector via alias map
4. Computes `title_key` (normalized hash for fuzzy dedup)
5. Upserts on `dedup_key` — re-runs are idempotent

---

## Environment

| Var | Default | Purpose |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB |
| `DB_NAME` | `financial_news` | DB |
| `OPENAI_API_KEY` | — | LLM key (sentiment + company extraction) |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | override for compatible APIs |
| `OPENAI_MODEL` | `gpt-4o-mini` | model name |
| `LLM_PROVIDER` | `auto` | `auto\|openai\|heuristic` |

---

## Quickstart

```bash
pip install -r requirements.txt
# put OPENAI_API_KEY in .env
docker-compose up -d mongodb                # or use a remote Mongo
python load_backup.py                       # one-time backfill of 205k articles
python main.py                              # http://localhost:8000
```

### Smoke test
```bash
curl 'http://localhost:8000/news?sector=BANKING&limit=5'
curl 'http://localhost:8000/news/summary?company=HDFC%20Bank'
curl 'http://localhost:8000/news/trending'
curl -X POST http://localhost:8000/mcp \
  -H 'content-type: application/json' \
  -d '{"tool":"get_market_sentiment","arguments":{"company":"Reliance Industries"}}'
```

---

## Architecture notes

- **Caching**: `/news` triggers a full fetch only if last fetch > 5 min ago; otherwise serves from DB. Background scheduler re-fetches every 10 min.
- **Sentiment is lazy**: only computed when an article is returned in a company-specific query. Result is persisted, never re-analyzed.
- **Concurrency**: Google News capped at 10 concurrent (anti-throttle); general feeds 80.
- **Dedup**: `dedup_key` (md5 of normalized title) for storage; `title_key` (stricter — strips numbers + source suffixes) + token-set Jaccard at query time.
- **Hindi filter**: titles with ≥10% Devanagari characters are dropped at ingest.

## EC2 deployment, systemd, nginx
(Same as v4 — see `docker-compose.yml`. Just expose port 8000 and run `python main.py`.)
