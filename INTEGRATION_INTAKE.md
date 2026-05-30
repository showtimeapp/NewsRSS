# Part B — Per-tool intake: prism-news

## Identity & purpose

- **Name:** `prism-news`
- **Version:** `5.1.0`
- **One-line purpose:** Indian financial-news intelligence — live headlines + per-company sentiment + trending stocks + corporate filings (Q4 results, dividends, board meetings, AGM/EGM, M&A, IPOs) + sector activity — backed by 80 RSS feeds across two pipelines (news 10-min / filings 30-min), OpenAI sentiment, and a 4,149-company alias master. The agent should call this any time a user asks about market reaction to an Indian company, today's news on a stock, what's trending, sector activity, recent corporate filings, or a sentiment verdict on an Indian-listed name.
- **Type:** `openapi`  *(FastAPI auto-generates an OpenAPI 3.0 spec at `/openapi.json`. An MCP-compatible endpoint is also exposed at `/mcp` for agents that prefer that transport — see "MCP secondary interface" below.)*
- **Owner / contact + repo:** [showtimeapp/NewsRSS](https://github.com/showtimeapp/NewsRSS) — internal team. Codeowner: aditya (mh.soulcentral@showtimeconsulting.in).

---

## Interface  (openapi)

- **Spec URL:** `{BASE_URL}/openapi.json` (OpenAPI 3.0, auto-generated)
- **Swagger UI:** `{BASE_URL}/docs`
- **Base URL(s):**
  - **prod:** `http://<gcp-host>:8001`  *(GCP instance IP; Cloud Run / Nginx fronting recommended for TLS)*
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
| **GET** | **`/filings`** | **Material corporate filings (results, dividends, board meetings, AGM/EGM, M&A, IPO, etc.)** |
| **GET** | **`/filings/categories`** | **12 supported filing categories + "Official" prefix** |
| GET | `/mcp` | MCP tool discovery (returns 4 tool schemas) |
| POST | `/mcp` | MCP tool invocation (`{tool, arguments}`) |
| GET | `/health` | Ops: counts, last_news_fetch + last_filings_fetch, sources active, LLM provider |
| GET | `/stats` | Source / sentiment / sector rollups (last 24h) |

### MCP secondary interface

For agents that prefer MCP over raw REST:

- **Transport:** HTTP — `POST {BASE_URL}/mcp` with body `{"tool": "<name>", "arguments": {...}}`
- **Discovery:** `GET {BASE_URL}/mcp`
- **Tools exposed (4):**
  - `search_financial_news` — articles + sentiment (filters: company, sector, hours, limit)
  - `get_market_sentiment` — bullish/bearish/neutral verdict + confidence for a company
  - `get_trending_stocks` — top-N mentioned companies in window
  - **`list_company_filings`** — recent corporate filings (12 categories: Result, Annual Report, Board Meeting, AGM/EGM, Corp Action, Insider Trading, M&A, IPO, Listing, Allotment, Guidance, Rating/Target)
- **Claude Desktop config:** see `prism-news.skill.md` for the drop-in.

---

## Auth & secrets

- **Method (client → API):** **none currently** (CORS wide-open). Gate at the infra layer (Nginx basic-auth, API gateway, or VPC) if you need access control.
- **Method (API → upstreams):** internal only — `OPENAI_API_KEY` (server-side, never exposed to clients).
- **Where to obtain credentials:**
  - For external API auth: no credentials needed today. If a gateway is added later, generate a token from the admin panel.
  - For local dev: set `OPENAI_API_KEY=sk-...` in `NewsRSS/.env`.
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
- **Charges incurred?** Indirectly — the **first** call to `/news/summary?company=X` or `/news?company=X` for a fresh window can trigger OpenAI sentiment on up to 30 articles (~$0.006/call worst case via `gpt-4o-mini`). Once analyzed, results are persisted and reused — subsequent calls sub-second + free. `/news/trending`, `/filings`, headline-list endpoints make zero LLM calls.
- **Typical latency:**

  | Endpoint | Typical | Worst-case |
  |---|---|---|
  | `/news` (no company) | 40–100 ms | 500 ms |
  | `/news?company=X` (cached sentiment) | 100–300 ms | 1.5 s |
  | `/news?company=X` (cold OpenAI) | 1.5–4 s | 10 s |
  | `/news/summary?company=X` cold | 3–8 s | 12 s |
  | `/news/trending` | 80–200 ms | 1 s |
  | **`/filings`** | **150–400 ms** | **1 s** |
  | `/news/sources` / `/news/sectors` / `/news/companies` / `/filings/categories` | <100 ms | 300 ms |
  | `/mcp` GET (discovery) | <50 ms | — |
  | `/mcp` POST (invoke) | matches underlying endpoint | — |

- **Rate limits / quotas / timeout:**
  - **Server side:** no rate limiting on the API itself.
  - **Internal feed-fetch concurrency:** capped at 30 outbound TLS, 3 per host, 6 Google News.
  - **Chunked-wave fetcher:** 10 feeds per wave, 3s pause — smooths outbound load.
  - **OpenAI quota:** governed by `OPENAI_API_KEY`'s tier. The provider chain falls back to a heuristic on rate-limit failures (sentiment.provider becomes `"heuristic"` — responses always succeed).
  - **Per-request timeout:** uvicorn default; recommend client-side timeout of 15s.
- **Data sensitivity:** **public** — every article is from a public RSS feed. No PII, no MNPI, no client data ever flows through. The MongoDB collection contains only public headlines + descriptions + computed sentiment.
- **Which environment to wire first:** **prod** is the only operational instance today. Local dev recommended for the integration test against the same OpenAPI spec.

### Golden example call (REST)

Used as the integration test:

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
    },
    {
      "company": "HDFC Bank",
      "mentions": 5,
      "sentiment": "positive",
      "sentiment_breakdown": {"positive": 3, "negative": 0, "neutral": 2},
      "sector": "BANKING"
    }
  ]
}
```

Pass criteria: HTTP 200; `trending` is a non-empty array; each item has `company` (string), `mentions` (int ≥ 1), `sentiment` (`positive|negative|neutral`), `sector` (8-chip code or null).

### Second golden test (filings endpoint, new in 5.1)

```bash
curl -s "${BASE_URL}/filings?hours=48&limit=5" | jq '.filings[] | {title, pipeline, filing_types, source}'
```

Expected: HTTP 200; non-empty `filings` array; each item has `title`, `pipeline` (`"news"` or `"filings"`), `filing_types` (non-empty array), `source`.

### Third golden test (MCP filings tool)

```bash
curl -s -X POST "${BASE_URL}/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"list_company_filings","arguments":{"company":"Reliance","hours":72,"limit":10}}' | jq
```

Expected shape: `{"tool":"list_company_filings","result":{"company":"Reliance","total":int,"returned":int,"data_age_min":int,"filings":[...]}}` with each filing carrying `filing_types: [...]`.

### Fourth golden test (MCP sentiment tool)

```bash
curl -s -X POST "${BASE_URL}/mcp" \
  -H 'content-type: application/json' \
  -d '{"tool":"get_market_sentiment","arguments":{"company":"HDFC Bank","hours":24}}' | jq
```

Expected shape: `{"tool":"get_market_sentiment","result":{"company":"HDFC Bank","verdict":"bullish|bearish|neutral","confidence":0.0-1.0,"breakdown":{...},"total_articles":int,"top_positive":[...],"top_negative":[...]}}`.

---

## Agent guidance

### Example user questions that should trigger this tool

**General news / sentiment:**
- "What's the news on HDFC Bank?" → `/news/summary?company=HDFC%20Bank` or MCP `get_market_sentiment`
- "How is Reliance doing today?" → same
- "Is TCS bullish?" → `/news/summary?company=TCS`
- "What's trending in the markets right now?" → `/news/trending?hours=24`
- "Show me banking sector news from the last 6 hours" → `/news?sector=BANKING&hours=6`
- "Compare sentiment on HDFC, ICICI, and SBI" → `/news/compare?companies=HDFC,ICICI,SBI&hours=48`
- "Any pharma news today?" → `/news?sector=PHARMA&hours=24`
- "Latest headlines on Adani group" → `/news?company=Adani%20Enterprises,Adani%20Ports,Adani%20Green%20Energy`

**Corporate filings (NEW):**
- "What did Reliance file last week?" → `/filings?company=Reliance&hours=168`
- "Q4 results today?" → `/filings?filing_type=Result&hours=24`
- "Any dividends announced recently?" → `/filings?filing_type=Corp%20Action&hours=72`
- "Board meetings in pharma sector?" → `/filings?sector=PHARMA&filing_type=Board%20Meeting&hours=168`
- "Recent IPOs / FPOs?" → `/filings?filing_type=IPO&hours=240`
- "Has SEBI issued any notices?" → `/filings?hours=24` then filter UI for `pipeline="filings"`

### Inputs the agent **must** supply vs optional/defaulted

| Endpoint | Required | Optional (with default) |
|---|---|---|
| `/news` | (none — works empty) | `company` (csv), `sector` (one of 8), `hours` (24), `page` (1), `limit` (50), `fuzzy` (true), `resolve_links` (true) |
| `/news/summary` | `company` (single name) | `hours` (24) |
| `/news/trending` | (none) | `hours` (24), `limit` (20) |
| `/news/compare` | `companies` (csv, ≥1) | `hours` (48) |
| `/news/sources` | (none) | `hours` (24) |
| **`/filings`** | (none) | `company`, `sector`, `filing_type` (one of 12), `hours` (24), `page` (1), `limit` (50) |
| `/mcp` POST | `tool`, `arguments` matching tool schema | per-tool defaults match REST defaults |

### What the agent should NOT call this for

- **Live stock prices / quotes** — this API serves news + filings, not prices.
- **Non-Indian markets** — alias map is India-only; coverage of US/EU equities is thin.
- **Pre-IPO / private companies** — only ~4,000 NSE/BSE-listed names tagged.
- **Historical research older than 240 hours (10 days)** — older docs exist but aren't exposed.
- **Real-time intra-second filings** — there's a 10-min news lag and 30-min filings-pipeline lag; not suited for HFT.

### Answer-shaping templates for the LLM

| Query | Template |
|---|---|
| "How is X doing?" | "X looks **{verdict}** today (**{breakdown.positive}** of **{total_articles}** articles positive). Strongest: '{top_positive[0].title}' ({top_positive[0].source}, {ago})." |
| "What's trending?" | Bullet top 5 with mention count and sentiment. Group by sector if 3+ sectors. |
| "List X's news" | Quote headline + source + ago. Offer to drill into any item. |
| "What did X file?" | Most material filing first (results > dividends > board meeting > guidance). Quote source + ago + filing category chip. |
| "Any results today?" | Bullet top 5 companies that posted results, sorted by recency. |
| Empty result | "No news/filings found for X in the last {hours}h. Try a wider window or check spelling." |

---

## Quality guarantees

The company tagger (`companies: [...]` field on every article) is hardened against 5 classes of false positives:

1. **NSE symbols are not aliased** — `GLOBAL`, `FOCUS`, `IPL`, `WEALTH` no longer falsely tag random text
2. **Short uppercase acronyms are case-sensitive** — `BOB` matches "BOB Q4" but NOT "Bob the carpenter"
3. **Stopword filter** — `india`, `global`, `bank`, `of`, `in`, ~70 common English words can't become aliases
4. **Long-tail companies need finance context** — auto-master matches require a finance signal word in the article (`Q1`, `earnings`, `shares`, `NSE`, `dividend`, etc.); curated 80 are exempt
5. **"Bank of X" disambiguation** — Bank of America / Korea / England no longer wrongly tag Bank of Baroda

The sector tagger (`sector: "..."` field) uses 3-layer resolution:
1. Company → sector map (most reliable)
2. RSS URL-path hint (`/banking-and-finance/` → `BANKING`, `/tech/` → `TECH`, ~28 patterns)
3. Keyword fallback (least reliable)

Result: ~80% sector tag coverage on freshly-ingested articles.

---

## Pre-flight checklist before wiring

- [ ] Confirm `{BASE_URL}/health` returns `status:"running"`, `llm_provider:"openai"`, and both `last_news_fetch` + `last_filings_fetch` are recent.
- [ ] Run golden tests #1 (trending) + #2 (filings) + #3 (MCP filings tool) against staging/prod — all expect HTTP 200 + non-empty results.
- [ ] Verify `/openapi.json` is reachable and parses.
- [ ] Add `BASE_URL=http://<gcp-host>:8001` to whichever client config holds integration URLs.
- [ ] Decide retry policy: prod is occasionally bouncy mid-fetch (75–80% feed success on a cold cycle); a single 503 from this API is rare but possible — recommend client-side 1 retry with 2s backoff.

---

## After intake (per the playbook)

1. ✅ Add a ~6-line entry to `config/integrations.yml` (see below)
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
    - name: filings
      method: GET
      path: /filings?hours=48&limit=5
      expect_status: 200
      expect_json_path: $.filings[0].filing_types[0]
    - name: mcp_sentiment
      method: POST
      path: /mcp
      body: '{"tool":"get_market_sentiment","arguments":{"company":"HDFC Bank","hours":24}}'
      expect_status: 200
      expect_json_path: $.result.verdict
  description: |
    Indian financial news + per-company sentiment + trending stocks +
    corporate filings (Q4 results, dividends, board meetings, AGM/EGM,
    M&A, IPOs). Call for any question about Indian-listed companies,
    market reaction, sector activity, recent filings, or what's moving today.
  mcp_tools:
    - search_financial_news
    - get_market_sentiment
    - get_trending_stocks
    - list_company_filings
  auth: none
  rate_limit_hint: "no server-side limit; OpenAI cost only on company-specific sentiment queries"
  owner: showtimeapp/NewsRSS
  version: "5.1.0"
```
