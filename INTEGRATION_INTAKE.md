# Part B — Per-tool intake: prism-news

## Identity & purpose

- **Name:** `prism-news`
- **Version:** `5.2.0`
- **One-line purpose:** Indian financial-news intelligence — live headlines + per-company sentiment + trending stocks + sector activity — backed by 73 RSS feeds (10-min cadence), OpenAI sentiment, and a 4,149-company alias master. The agent should call this any time a user asks about market reaction to an Indian company, today's news on a stock, what's trending, sector activity, or a sentiment verdict on an Indian-listed name.
- **Type:** `openapi`  *(FastAPI auto-generates an OpenAPI 3.0 spec at `/openapi.json`. An MCP-compatible endpoint is also exposed at `/mcp` — see "MCP secondary interface" below.)*
- **Owner / contact + repo:** [showtimeapp/NewsRSS](https://github.com/showtimeapp/NewsRSS) — internal team. Codeowner: aditya (mh.soulcentral@showtimeconsulting.in).

> **Companion service (separate intake):** `prism-filings` handles corporate filings (results, dividends, board meetings, AGM/EGM, M&A, IPOs) from NSE / BSE / SEBI / RBI / PIB official RSS. Different repo, different base URL, different `/mcp`. Do not duplicate filings coverage in this intake.

---

## Interface  (openapi)

- **Spec URL:** `{BASE_URL}/openapi.json` (OpenAPI 3.0, auto-generated)
- **Swagger UI:** `{BASE_URL}/docs`
- **Base URL(s):**
  - **prod:** `http://<gcp-host>:8001`  *(GCP instance IP; Nginx fronting recommended for TLS)*
  - **staging:** not yet provisioned
  - **dev (local):** `http://localhost:8000`

### Key endpoints (full list in `/openapi.json`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/news` | Main feed; filter by `?company=` or `?sector=` |
| GET | `/news/summary` | Per-company sentiment aggregate + trend |
| GET | `/news/trending` | Most-mentioned companies in window |
| GET | `/news/compare` | Multi-company side-by-side sentiment |
| GET | `/news/sources` | Source reliability stats |
| GET | `/news/companies` | Canonical company directory (4,149 entries) |
| GET | `/news/sectors` | 8 sector chip codes |
| GET | `/mcp` | MCP tool discovery (returns 3 tool schemas) |
| POST | `/mcp` | MCP tool invocation (`{tool, arguments}`) |
| GET | `/health` | Ops: counts, last_fetch, sources active, LLM provider |
| GET | `/stats` | Source / sentiment / sector rollups (last 24h) |

### MCP secondary interface

- **Transport:** HTTP — `POST {BASE_URL}/mcp` with body `{"tool": "<name>", "arguments": {...}}`
- **Discovery:** `GET {BASE_URL}/mcp`
- **Tools exposed (3):**
  - `search_financial_news` — articles + sentiment (filters: company, sector, hours, limit)
  - `get_market_sentiment` — bullish/bearish/neutral verdict + confidence for a company
  - `get_trending_stocks` — top-N mentioned companies in window
- **Claude Desktop config:** see `prism-news.skill.md` for the drop-in.

---

## Auth & secrets

- **Method (client → API):** **none currently** (CORS wide-open). Gate at the infra layer if you need access control.
- **Method (API → upstreams):** internal only — `OPENAI_API_KEY` (server-side).
- **Where to obtain credentials:** local dev — set `OPENAI_API_KEY=sk-...` in `NewsRSS/.env`.
- **Shared service credential or per-user?** Service-level only.
- **Env var names** (server, not client):
  - `OPENAI_API_KEY` — sentiment + company-extraction LLM
  - `MONGO_URI` — full URI w/ auth
  - `MONGO_PASSWORD` — seeded into Mongo container on first boot
  - `LLM_PROVIDER` — `auto | openai | heuristic` (default: `auto`)
  - `OPENAI_BASE_URL` — override for Azure/Groq/Together (default `https://api.openai.com/v1`)
  - `OPENAI_MODEL` — default `gpt-4o-mini`
  - `POSTGRES_URL` *(build-time only)* — only for `build_company_master.py`
  - `STATE_DB` *(build-time only)* — path to `state.db`

---

## Operational & safety

- **Read-only?** Yes from the client's perspective — every public endpoint is `GET` except `POST /mcp`, which still **only reads from MongoDB**.
- **Charges incurred?** Indirectly — the **first** call to `/news/summary?company=X` or `/news?company=X` for a fresh window can trigger OpenAI sentiment on up to 30 articles (~$0.006/call worst case via `gpt-4o-mini`). Once analyzed, results are persisted and reused. `/news/trending` and headline-list endpoints make zero LLM calls.
- **Typical latency:**

  | Endpoint | Typical | Worst-case |
  |---|---|---|
  | `/news` (no company) | 40–100 ms | 500 ms |
  | `/news?company=X` (cached sentiment) | 100–300 ms | 1.5 s |
  | `/news?company=X` (cold OpenAI) | 1.5–4 s | 10 s |
  | `/news/summary?company=X` cold | 3–8 s | 12 s |
  | `/news/trending` | 80–200 ms | 1 s |
  | `/news/sources` / `/news/sectors` / `/news/companies` | <100 ms | 300 ms |
  | `/mcp` GET (discovery) | <50 ms | — |
  | `/mcp` POST (invoke) | matches underlying endpoint | — |

- **Rate limits / quotas / timeout:**
  - Server-side: no rate limiting on the API itself.
  - Internal feed-fetch concurrency: capped at 30 outbound TLS, 3 per host, 6 Google News.
  - Chunked-wave fetcher: 10 feeds per wave, 3s pause.
  - OpenAI quota: governed by `OPENAI_API_KEY` tier. Falls back to a heuristic on rate-limit (sentiment.provider becomes `"heuristic"`).
  - Per-request timeout: uvicorn default; recommend client-side timeout of 15s.
- **Data sensitivity:** **public** — every article is from a public RSS feed. No PII, no MNPI, no client data ever flows through.
- **Which environment to wire first:** **prod** is the only operational instance today.

### Golden example call (REST)

```bash
curl -s "${BASE_URL}/news/trending?hours=24&limit=5" | jq
```

Expected shape (counts vary):

```json
{
  "hours": 24,
  "trending": [
    {
      "company": "Reliance Industries",
      "mentions": 19,
      "sentiment": "positive",
      "sentiment_breakdown": {"positive": 12, "negative": 2, "neutral": 5},
      "sector": "ENERGY"
    }
  ]
}
```

Pass criteria: HTTP 200; `trending` is non-empty; each item has `company` (string), `mentions` (int ≥ 1), `sentiment` (`positive|negative|neutral`), `sector` (8-chip code or null).

### Second golden test (MCP sentiment tool)

```bash
curl -s -X POST "${BASE_URL}/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"get_market_sentiment","arguments":{"company":"HDFC Bank","hours":24}}' | jq
```

Expected shape: `{"tool":"get_market_sentiment","result":{"company":"HDFC Bank","verdict":"bullish|bearish|neutral","confidence":0.0-1.0,"breakdown":{...},"total_articles":int,"top_positive":[...],"top_negative":[...]}}`.

---

## Agent guidance

### Example user questions that should trigger this tool

- "What's the news on HDFC Bank?" → `/news/summary?company=HDFC%20Bank` or MCP `get_market_sentiment`
- "How is Reliance doing today?" → same
- "Is TCS bullish?" → `/news/summary?company=TCS`
- "What's trending in the markets right now?" → `/news/trending?hours=24`
- "Show me banking sector news from the last 6 hours" → `/news?sector=BANKING&hours=6`
- "Compare sentiment on HDFC, ICICI, and SBI" → `/news/compare?companies=HDFC,ICICI,SBI&hours=48`
- "Any pharma news today?" → `/news?sector=PHARMA&hours=24`
- "Latest headlines on Adani group" → `/news?company=Adani%20Enterprises,Adani%20Ports,Adani%20Green%20Energy`

### What the agent must route to `prism-filings` instead

- "What did Reliance file last week?" → `prism-filings`
- "Q4 results today?" → `prism-filings`
- "Any dividends announced recently?" → `prism-filings`
- "Board meetings in pharma sector?" → `prism-filings`
- "Recent IPOs / FPOs?" → `prism-filings`
- "Has SEBI issued any notices?" → `prism-filings`

### Inputs the agent **must** supply vs optional/defaulted

| Endpoint | Required | Optional (with default) |
|---|---|---|
| `/news` | (none — works empty) | `company` (csv), `sector` (one of 8), `hours` (24), `page` (1), `limit` (50), `fuzzy` (true), `resolve_links` (true) |
| `/news/summary` | `company` (single name) | `hours` (24) |
| `/news/trending` | (none) | `hours` (24), `limit` (20) |
| `/news/compare` | `companies` (csv, ≥1) | `hours` (48) |
| `/news/sources` | (none) | `hours` (24) |
| `/mcp` POST | `tool`, `arguments` matching tool schema | per-tool defaults match REST defaults |

### What the agent should NOT call this for

- **Live stock prices / quotes** — this API serves news, not prices.
- **Corporate filings** — that's `prism-filings`, a separate service.
- **Non-Indian markets** — alias map is India-only; coverage of US/EU equities is thin.
- **Pre-IPO / private companies** — only ~4,000 NSE/BSE-listed names tagged.
- **Historical research older than 240 hours (10 days)** — older docs exist but aren't exposed.

### Answer-shaping templates for the LLM

| Query | Template |
|---|---|
| "How is X doing?" | "X looks **{verdict}** today (**{breakdown.positive}** of **{total_articles}** articles positive). Strongest: '{top_positive[0].title}' ({top_positive[0].source}, {ago})." |
| "What's trending?" | Bullet top 5 with mention count and sentiment. Group by sector if 3+ sectors. |
| "List X's news" | Quote headline + source + ago. Offer to drill into any item. |
| Empty result | "No news found for X in the last {hours}h. Try a wider window or check spelling." |

---

## Quality guarantees

The company tagger (`companies: [...]` field on every article) is hardened against 5 classes of false positives:

1. **NSE symbols are not aliased** — `GLOBAL`, `FOCUS`, `IPL`, `WEALTH` no longer falsely tag random text
2. **Short uppercase acronyms are case-sensitive** — `BOB` matches "BOB Q4" but NOT "Bob the carpenter"
3. **Stopword filter** — `india`, `global`, `bank`, `of`, `in`, ~70 common English words can't become aliases
4. **Long-tail companies need finance context** — auto-master matches require a finance signal word; curated 80 are exempt
5. **"Bank of X" disambiguation** — Bank of America / Korea / England no longer wrongly tag Bank of Baroda

The sector tagger (`sector: "..."` field) uses 3-layer resolution:
1. Company → sector map (most reliable)
2. RSS URL-path hint (`/banking-and-finance/` → `BANKING`, `/tech/` → `TECH`, ~28 patterns)
3. Keyword fallback (least reliable)

Result: ~80% sector tag coverage on freshly-ingested articles.

---

## Pre-flight checklist before wiring

- [ ] Confirm `{BASE_URL}/health` returns `status:"running"`, `llm_provider:"openai"`, and `last_fetch` is recent.
- [ ] Run golden tests #1 (trending) + #2 (MCP sentiment) against staging/prod — both expect HTTP 200 + non-empty results.
- [ ] Verify `/openapi.json` is reachable and parses.
- [ ] Add `BASE_URL=http://<gcp-host>:8001` to whichever client config holds integration URLs.
- [ ] Decide retry policy: prod is occasionally bouncy mid-fetch (75–80% feed success on a cold cycle); recommend client-side 1 retry with 2s backoff.
- [ ] If your agent should also handle filings questions, separately wire the `prism-filings` integration.

---

## After intake

1. ✅ Add a ~6-line entry to `config/integrations.yml`
2. ✅ `IntegrationRegistry` builds the right ADK adapter at startup from `{BASE_URL}/openapi.json`
3. ✅ Tool assigned to relevant `PrismAgent(integrations=[..., "prism-news"])`
4. ✅ Appears in `GET /api/v1/integrations` with live health from `/health` heartbeat
5. ✅ Golden example calls wired as registry integration tests (CI gate)

### Suggested entry for `config/integrations.yml`

```yaml
prism-news:
  type: openapi
  spec_url: "${PRISM_NEWS_BASE_URL}/openapi.json"
  base_url: "${PRISM_NEWS_BASE_URL}"
  health_path: /health
  golden_tests:
    - name: trending
      method: GET
      path: /news/trending?hours=24&limit=5
      expect_status: 200
      expect_json_path: $.trending[0].company
    - name: mcp_sentiment
      method: POST
      path: /mcp
      body: '{"tool":"get_market_sentiment","arguments":{"company":"HDFC Bank","hours":24}}'
      expect_status: 200
      expect_json_path: $.result.verdict
  description: |
    Indian financial news + per-company sentiment + trending stocks +
    sector activity. Call for any question about Indian-listed companies,
    market reaction, sector activity, or what's moving today. For
    corporate filings (results/dividends/board meetings/etc.), route to
    the separate prism-filings integration.
  mcp_tools:
    - search_financial_news
    - get_market_sentiment
    - get_trending_stocks
  auth: none
  rate_limit_hint: "no server-side limit; OpenAI cost only on company-specific sentiment queries"
  owner: showtimeapp/NewsRSS
  version: "5.2.0"
```
