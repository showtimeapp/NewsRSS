"""
Prism Financial News Aggregator API v5.0
========================================
- 82 feeds across 24 Indian + global publishers
- Sentiment via pluggable LLM (OpenAI primary, FinBERT fallback, heuristic last resort)
- Company normalization with aliases + sector tagging (8 sectors)
- Fuzzy dedup, Google News URL resolution, Hindi filter
- New endpoints: /news/summary, /news/trending, /news/sources, /news/compare, /news/companies
- MCP endpoint at /mcp for Claude tool integration (3 tools)
- Newsroom Live UI at /

Env vars:
  MONGO_URI         — default: mongodb://localhost:27017
  DB_NAME           — default: financial_news
  OPENAI_API_KEY    — sentiment / company-extraction LLM
  OPENAI_BASE_URL   — default: https://api.openai.com/v1 (override for compatible APIs)
  OPENAI_MODEL      — default: gpt-4o-mini
  LLM_PROVIDER      — auto|openai|heuristic (default: auto)
"""

import os, re, logging, asyncio, time as _time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from typing import Optional, List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import aiohttp
import feedparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from feeds_config import ALL_FEEDS, FEED_COUNT, get_company_feeds, REFERER_MAP
from llm_provider import analyze_sentiment, active_provider
from company_aliases import (
    detect_companies, normalize_company, detect_sector,
    SECTORS, ALL_COMPANIES, COMPANY_ALIASES,
)
from dedup import (
    title_key, fuzzy_dedup, resolve_google_url, is_google_news_url,
    is_hindi, filter_non_hindi,
)

# ═══════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════
MONGO_URI          = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME            = os.getenv("DB_NAME", "financial_news")
COLLECTION         = "articles"
FETCH_INTERVAL_MIN = 10
MIN_FETCH_GAP_MIN  = 5
FEED_TIMEOUT_SEC   = 12   # was 8; US wire services from GCP Mumbai egress need ~10s headroom
MAX_CONCURRENT     = 30   # was 80; smaller pool prevents outbound TLS thundering-herd
MAX_PER_HOST       = 3    # NEW; was unlimited. Stops 9 Livemint/etc feeds from stampeding one publisher

IST = timezone(timedelta(hours=5, minutes=30))

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("news-api")

db = None
http_session: Optional[aiohttp.ClientSession] = None
last_full_fetch: Optional[datetime] = None
fetch_lock = asyncio.Lock()
scheduler = AsyncIOScheduler()

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}


# ═══════════════════════════════════════════════════
# PARSERS
# ═══════════════════════════════════════════════════
def clean_html(text: str) -> str:
    if not text: return ""
    c = re.sub(r"<[^>]+>", "", text)
    c = re.sub(r"\s+", " ", c).strip()
    for old, new in [("&amp;","&"),("&lt;","<"),("&gt;",">"),("&#039;","'"),
        ("&quot;",'"'),("&#x27;","'"),("&nbsp;"," "),("&#8217;","'"),
        ("&#8220;",'"'),("&#8221;",'"'),("&#8211;","–"),("&rsquo;","'"),
        ("&lsquo;","'"),("&rdquo;",'"'),("&ldquo;",'"'),
        ("&mdash;","—"),("&ndash;","–"),("&#8230;","...")]:
        c = c.replace(old, new)
    return c


def extract_description(entry: dict) -> str:
    candidates = []
    s = entry.get("summary", "")
    if s: candidates.append(s)
    content = entry.get("content")
    if content and isinstance(content, list):
        for c in content:
            val = c.get("value", "")
            if val: candidates.append(val)
    d = entry.get("description", "")
    if d and d not in candidates: candidates.append(d)
    best = ""
    for c in candidates:
        cleaned = clean_html(c)
        if len(cleaned) > len(best): best = cleaned
    return best[:2000]


def parse_pub_date(entry):
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        try:
            dt = datetime(*struct[:6], tzinfo=timezone.utc)
            return dt, dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        except: pass
    raw = entry.get("published") or entry.get("updated") or ""
    if raw:
        for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                     "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"]:
            try:
                dt = datetime.strptime(raw.strip(), fmt)
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                return dt, dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            except ValueError: continue
        return None, raw
    return None, ""


def extract_google_source(title):
    if " - " in title:
        parts = title.rsplit(" - ", 1)
        if len(parts) == 2 and len(parts[1]) < 60:
            return parts[0].strip(), parts[1].strip()
    return title, ""


def parse_feed_bytes(raw_bytes, source_name, url):
    is_google = "news.google.com" in url
    feed = feedparser.parse(raw_bytes)
    articles = []
    now = datetime.now(timezone.utc)
    for entry in feed.entries:
        title = clean_html(entry.get("title", "")).strip()
        if not title:
            continue
        # Hindi filter at ingest time — saves storage + sentiment cost
        if is_hindi(title):
            continue
        link = (entry.get("link") or entry.get("id") or "").strip()
        if not link: continue
        description = extract_description(entry)
        source = source_name
        es = entry.get("source", {})
        if isinstance(es, dict) and es.get("title"): source = es["title"]
        pub = entry.get("publisher")
        if isinstance(pub, dict): pub = pub.get("title") or pub.get("name") or ""
        elif not isinstance(pub, str): pub = ""
        if pub: source = str(pub)
        if is_google:
            title, gs = extract_google_source(title)
            if gs: source = gs
        dt, ist_str = parse_pub_date(entry)

        # Company + sector enrichment from title+description
        text_blob = f"{title}. {description}"
        companies = detect_companies(text_blob)
        sector = detect_sector(text_blob, companies)

        articles.append({
            "title": title,
            "description": description,
            "source": source,
            "published_ist": ist_str,
            "published_dt": dt,
            "link": link,
            "fetched_at": now,
            "sentiment": None,
            "companies": companies,
            "sector": sector,
        })
    return articles


# ═══════════════════════════════════════════════════
# ASYNC FETCHER
# ═══════════════════════════════════════════════════
google_sem = asyncio.Semaphore(6)    # was 10
general_sem = asyncio.Semaphore(30)  # was 80


async def fetch_one(session, source_name, url):
    sem = google_sem if "news.google.com" in url else general_sem
    t0 = _time.monotonic()
    async with sem:
        try:
            headers = dict(BROWSER_HEADERS)
            for domain, referer in REFERER_MAP.items():
                if domain in url:
                    headers["Referer"] = referer
                    break
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=FEED_TIMEOUT_SEC),
                                   ssl=False) as resp:
                if resp.status != 200:
                    log.warning(f"feed http {resp.status} [{source_name}] {url[:70]}")
                    return []
                body = await resp.read()
                # parse_feed_bytes runs detect_companies (~1.4ms/article over 13k
                # aliases); off-loop'ing it keeps HTTP I/O from starving other feeds.
                arts = await asyncio.to_thread(parse_feed_bytes, body, source_name, url)
                if not arts:
                    log.warning(f"feed empty after parse [{source_name}] {url[:70]} (body={len(body)}b)")
                return arts
        except asyncio.TimeoutError:
            log.warning(f"feed timeout {FEED_TIMEOUT_SEC}s [{source_name}] {url[:70]} elapsed={_time.monotonic()-t0:.1f}s")
            return []
        except Exception as e:
            log.warning(f"feed error [{source_name}] {url[:70]} {type(e).__name__}: {str(e)[:120]}")
            return []


async def fetch_parallel(feeds, session):
    tasks = [fetch_one(session, n, u) for n, u in feeds]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_arts = []
    ok = 0
    for r in results:
        if isinstance(r, list) and r:
            all_arts.extend(r)
            ok += 1
    log.info(f"Fetched {len(all_arts)} articles from {ok}/{len(feeds)} feeds")
    return all_arts


# ═══════════════════════════════════════════════════
# SENTIMENT (via llm_provider chain)
# ═══════════════════════════════════════════════════
async def analyze_one_title(title: str) -> Optional[dict]:
    if not title or not http_session:
        return None
    r = await analyze_sentiment(http_session, title)
    return r


# ═══════════════════════════════════════════════════
# MONGODB
# ═══════════════════════════════════════════════════
import hashlib


def make_dedup_key(title: str) -> str:
    norm = re.sub(r"\s+", " ", title.lower().strip())
    return hashlib.md5(norm.encode()).hexdigest()


async def store_articles(articles):
    if not articles: return 0
    coll = db[COLLECTION]
    for a in articles:
        a["dedup_key"] = make_dedup_key(a["title"])
        a["title_key"] = title_key(a["title"])

    seen = set(); unique = []
    for a in articles:
        if a["dedup_key"] not in seen:
            seen.add(a["dedup_key"]); unique.append(a)

    existing = set()
    async for doc in coll.find({"dedup_key": {"$in": list(seen)}}, {"dedup_key": 1}):
        existing.add(doc["dedup_key"])

    new = [a for a in unique if a["dedup_key"] not in existing]
    if not new: return 0
    try:
        r = await coll.insert_many(new, ordered=False)
        ins = len(r.inserted_ids)
    except: ins = len(new)
    log.info(f"Stored {ins} new (skipped {len(articles)-ins} dupes)")
    return ins


async def _resolve_google_links(articles: list, cap: int = 20) -> None:
    """In-place: resolve up to `cap` Google News URLs concurrently."""
    targets = [a for a in articles if is_google_news_url(a.get("link", ""))][:cap]
    if not targets or not http_session:
        return
    tasks = [resolve_google_url(http_session, a["link"]) for a in targets]
    resolved = await asyncio.gather(*tasks, return_exceptions=True)
    for a, r in zip(targets, resolved):
        if isinstance(r, str) and r and r != a["link"]:
            a["original_link"] = a["link"]
            a["link"] = r


async def query_articles_with_sentiment(
    companies=None,
    sector=None,
    hours=24,
    page=1,
    limit=50,
    apply_fuzzy_dedup: bool = True,
    resolve_links: bool = True,
):
    coll = db[COLLECTION]
    q = {"published_dt": {"$gte": datetime.now(timezone.utc) - timedelta(hours=hours), "$ne": None}}

    if companies:
        # Match canonical companies field first, fall back to title/description regex for legacy rows
        canonical = [normalize_company(c) or c for c in companies]
        or_conditions = [{"companies": {"$in": canonical}}]
        for c in companies:
            regex = {"$regex": re.escape(c), "$options": "i"}
            or_conditions.append({"title": regex})
            or_conditions.append({"description": regex})
        q["$or"] = or_conditions

    if sector:
        q["sector"] = sector.upper()

    total = await coll.count_documents(q)

    # Over-fetch when fuzzy dedup is on — we'll trim back to `limit` after dedup
    skip = (page - 1) * limit
    fetch_limit = limit * 3 if apply_fuzzy_dedup else limit
    cursor = coll.find(
        q,
        {"title": 1, "description": 1, "source": 1,
         "published_ist": 1, "published_dt": 1, "link": 1, "original_link": 1,
         "sentiment": 1, "companies": 1, "sector": 1},
    ).sort([("published_dt", -1), ("fetched_at", -1)]).skip(skip).limit(fetch_limit)

    articles = []
    async for doc in cursor:
        articles.append(doc)

    if apply_fuzzy_dedup:
        articles = fuzzy_dedup(articles)[:limit]

    sentiment_count = 0
    if companies and articles:
        need_sentiment = [doc for doc in articles if doc.get("sentiment") is None]
        if need_sentiment:
            titles = [doc.get("title", "")[:200] for doc in need_sentiment]
            tasks = [analyze_one_title(t) for t in titles]
            sentiments = await asyncio.gather(*tasks)
            for doc, sentiment in zip(need_sentiment, sentiments):
                if sentiment:
                    await coll.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"sentiment": sentiment}},
                    )
                    doc["sentiment"] = sentiment
                    sentiment_count += 1
            log.info(f"Sentiment: {sentiment_count}/{len(need_sentiment)} analyzed via {active_provider()}")

    if resolve_links:
        await _resolve_google_links(articles, cap=15)

    cleaned = []
    for doc in articles:
        doc.pop("_id", None)
        doc.pop("title_key", None)
        if not companies and doc.get("sentiment") is None:
            doc.pop("sentiment", None)
        cleaned.append(doc)

    return cleaned, total, sentiment_count


# ═══════════════════════════════════════════════════
# FULL FETCH
# ═══════════════════════════════════════════════════
async def do_full_fetch():
    global last_full_fetch
    if fetch_lock.locked():
        log.info("Fetch already running, skip"); return 0
    async with fetch_lock:
        t = _time.monotonic()
        log.info(f"=== FULL FETCH: {FEED_COUNT} feeds ===")
        arts = await fetch_parallel(ALL_FEEDS, http_session)
        ins = await store_articles(arts)
        last_full_fetch = datetime.now(timezone.utc)
        log.info(f"=== DONE: {ins} new in {_time.monotonic()-t:.1f}s ===")
        return ins


def needs_full():
    if not last_full_fetch: return True
    return (datetime.now(timezone.utc) - last_full_fetch) > timedelta(minutes=MIN_FETCH_GAP_MIN)


# ═══════════════════════════════════════════════════
# FASTAPI
# ═══════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, http_session
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    try:
        await db[COLLECTION].drop_index("link_1")
    except Exception:
        pass
    await db[COLLECTION].create_index("dedup_key", unique=True, background=True)
    await db[COLLECTION].create_index("link", background=True)
    await db[COLLECTION].create_index("fetched_at", background=True)
    await db[COLLECTION].create_index("published_dt", background=True)
    await db[COLLECTION].create_index([("title", "text"), ("description", "text")], background=True)
    await db[COLLECTION].create_index("sentiment", background=True)
    await db[COLLECTION].create_index("companies", background=True)
    await db[COLLECTION].create_index("sector", background=True)
    log.info(f"MongoDB: {MONGO_URI}/{DB_NAME}")
    log.info(f"LLM provider chain active: {active_provider()}")

    connector = aiohttp.TCPConnector(
        limit=MAX_CONCURRENT,
        limit_per_host=MAX_PER_HOST,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
        ssl=False,
    )
    http_session = aiohttp.ClientSession(connector=connector)

    await do_full_fetch()

    scheduler.add_job(do_full_fetch, "interval", minutes=FETCH_INTERVAL_MIN,
                      id="full_fetch", replace_existing=True, max_instances=1)
    scheduler.start()
    log.info(f"24/7 scheduler: every {FETCH_INTERVAL_MIN}m")
    yield
    scheduler.shutdown(wait=False)
    await http_session.close()
    client.close()


app = FastAPI(title="Prism Financial News API", version="5.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


# ═══════════════════════════════════════════════════
# Helpers shared by multiple endpoints
# ═══════════════════════════════════════════════════
def _parse_companies_csv(s: Optional[str]) -> Optional[List[str]]:
    if not s: return None
    return [c.strip() for c in s.split(",") if c.strip()]


async def _articles_for(companies=None, sector=None, hours=24, limit=200):
    """Internal: get raw articles for analytics endpoints (no sentiment trigger)."""
    coll = db[COLLECTION]
    q = {"published_dt": {"$gte": datetime.now(timezone.utc) - timedelta(hours=hours), "$ne": None}}
    if companies:
        canonical = [normalize_company(c) or c for c in companies]
        or_conditions = [{"companies": {"$in": canonical}}]
        for c in companies:
            regex = {"$regex": re.escape(c), "$options": "i"}
            or_conditions += [{"title": regex}, {"description": regex}]
        q["$or"] = or_conditions
    if sector:
        q["sector"] = sector.upper()
    cursor = coll.find(q, {
        "title": 1, "description": 1, "source": 1, "published_ist": 1,
        "published_dt": 1, "link": 1, "sentiment": 1, "companies": 1, "sector": 1,
    }).sort([("published_dt", -1)]).limit(limit)
    out = []
    async for doc in cursor:
        doc.pop("_id", None)
        out.append(doc)
    return out


async def _bulk_sentiment(articles: list) -> int:
    """Run sentiment on articles missing it, persist results. Returns count analyzed."""
    if not articles: return 0
    coll = db[COLLECTION]
    need = [a for a in articles if a.get("sentiment") is None][:30]  # cap per request
    if not need: return 0
    tasks = [analyze_one_title(a.get("title", "")[:200]) for a in need]
    sentiments = await asyncio.gather(*tasks)
    n = 0
    for a, s in zip(need, sentiments):
        if s:
            await coll.update_one({"dedup_key": a.get("dedup_key", "")} if a.get("dedup_key") else {"title": a["title"]},
                                  {"$set": {"sentiment": s}})
            a["sentiment"] = s
            n += 1
    return n


# ═══════════════════════════════════════════════════
# /news — main feed (back-compat)
# ═══════════════════════════════════════════════════
@app.get("/news")
async def get_news(
    company: Optional[str] = Query(None, description="Company name(s), comma-separated"),
    sector: Optional[str] = Query(None, description="One of BANKING|TECH|AUTO|PHARMA|ENERGY|FMCG|METALS|REALTY"),
    hours: int = Query(24, ge=1, le=240),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    resolve_links: bool = Query(True, description="Resolve google.news redirects"),
    fuzzy: bool = Query(True, description="Collapse near-duplicate titles"),
):
    """
    Feed query.

    - WITHOUT `company`: pure DB query, instant.
    - WITH `company`: also fetches the 3 per-company Google News feeds so the
      response includes the very latest headlines for that ticker.

    The full 81-feed catalog is refreshed ONLY by the 10-min background
    scheduler — searches never trigger a full fetch (avoids the outbound
    thundering-herd that crushed feed success rate).
    """
    t = _time.monotonic()
    stale = needs_full()
    cn, fn, sn = 0, 0, 0
    companies = _parse_companies_csv(company)

    if companies:
        # Targeted per-company fetch only (3 Google News queries per company —
        # cheap, doesn't stampede unrelated publishers).
        all_company_feeds = []
        for c in companies:
            all_company_feeds.extend(get_company_feeds(c))
        ca = await fetch_parallel(all_company_feeds, http_session)
        cn = await store_articles(ca)

    articles, total, sn = await query_articles_with_sentiment(
        companies, sector, hours, page, limit,
        apply_fuzzy_dedup=fuzzy, resolve_links=resolve_links,
    )
    elapsed = _time.monotonic() - t

    # How fresh is the data the user is seeing?
    age_min = None
    if last_full_fetch:
        age_min = int((datetime.now(timezone.utc) - last_full_fetch).total_seconds() / 60)

    # cache_status semantics:
    #   "stale -> fetched"   user-search triggered a per-company fetch and we have fresh data
    #   "fresh -> DB only"   DB was fresh enough; no fetch happened on this request
    if companies:
        cache_status = "stale -> fetched" if stale else "fresh -> DB only"
    else:
        cache_status = "fresh -> DB only"

    return {
        "success": True,
        "query": {"company": companies, "sector": sector, "hours": hours, "page": page, "limit": limit},
        "meta": {
            "total_results": total,
            "returned": len(articles),
            "total_pages": max(1, (total + limit - 1) // limit),
            "current_page": page,
            "feeds_configured": FEED_COUNT,
            "response_time_ms": int(elapsed * 1000),
            "last_full_fetch_ist": last_full_fetch.astimezone(IST).strftime(
                "%Y-%m-%d %H:%M:%S IST") if last_full_fetch else None,
            "data_age_min": age_min,
            "refresh_interval_min": FETCH_INTERVAL_MIN,
            "new_articles": {"company_search": cn, "full_fetch": fn},
            "sentiment_analyzed_this_request": sn,
            "sentiment_provider": active_provider(),
            "cache_status": cache_status,
        },
        "articles": articles,
    }


# ═══════════════════════════════════════════════════
# /news/summary — per-company sentiment summary
# ═══════════════════════════════════════════════════
@app.get("/news/summary")
async def news_summary(
    company: str = Query(..., description="Single company name"),
    hours: int = Query(24, ge=1, le=240),
):
    canonical = normalize_company(company) or company
    arts = await _articles_for(companies=[canonical], hours=hours, limit=300)
    if not arts:
        return {
            "company": canonical, "input": company,
            "total_articles": 0, "sentiment_breakdown": {"positive": 0, "negative": 0, "neutral": 0},
            "avg_score": 0.0, "trend": "no_data", "top_positive": [], "top_negative": [],
        }
    await _bulk_sentiment(arts)

    pos = neg = neu = 0
    score_sum = 0.0
    score_n = 0
    pos_arts, neg_arts = [], []
    for a in arts:
        s = a.get("sentiment") or {}
        label = (s.get("label") or "").lower()
        score = s.get("score", 0.0) or 0.0
        if label == "positive":
            pos += 1; pos_arts.append((score, a)); score_sum += score; score_n += 1
        elif label == "negative":
            neg += 1; neg_arts.append((score, a)); score_sum -= score; score_n += 1
        elif label == "neutral":
            neu += 1

    pos_arts.sort(key=lambda x: x[0], reverse=True)
    neg_arts.sort(key=lambda x: x[0], reverse=True)

    # Trend = compare current half vs previous half of the window
    half = datetime.now(timezone.utc) - timedelta(hours=hours / 2)
    recent_pos = recent_neg = older_pos = older_neg = 0
    for a in arts:
        s = (a.get("sentiment") or {}).get("label", "")
        dt = a.get("published_dt")
        if not isinstance(dt, datetime): continue
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        bucket_pos, bucket_neg = (recent_pos, recent_neg) if dt >= half else (older_pos, older_neg)
        if s == "positive":
            if dt >= half: recent_pos += 1
            else: older_pos += 1
        elif s == "negative":
            if dt >= half: recent_neg += 1
            else: older_neg += 1

    recent_net = recent_pos - recent_neg
    older_net = older_pos - older_neg
    if recent_net > older_net + 2: trend = "bullish"
    elif recent_net < older_net - 2: trend = "bearish"
    else: trend = "neutral"

    def _clean(a):
        return {
            "title": a["title"], "source": a.get("source"),
            "published_ist": a.get("published_ist"),
            "link": a.get("link"),
            "sentiment": a.get("sentiment"),
        }

    return {
        "company": canonical,
        "input": company,
        "total_articles": len(arts),
        "sentiment_breakdown": {"positive": pos, "negative": neg, "neutral": neu},
        "avg_score": round(score_sum / score_n, 3) if score_n else 0.0,
        "trend": trend,
        "trend_detail": {
            "recent_half": {"positive": recent_pos, "negative": recent_neg},
            "older_half": {"positive": older_pos, "negative": older_neg},
        },
        "top_positive": [_clean(a) for _, a in pos_arts[:3]],
        "top_negative": [_clean(a) for _, a in neg_arts[:3]],
        "provider": active_provider(),
    }


# ═══════════════════════════════════════════════════
# /news/trending — most mentioned companies
# ═══════════════════════════════════════════════════
@app.get("/news/trending")
async def news_trending(
    hours: int = Query(24, ge=1, le=72),
    limit: int = Query(20, ge=1, le=100),
):
    coll = db[COLLECTION]
    pipeline = [
        {"$match": {"published_dt": {"$gte": datetime.now(timezone.utc) - timedelta(hours=hours)},
                    "companies": {"$exists": True, "$ne": []}}},
        {"$unwind": "$companies"},
        {"$group": {
            "_id": "$companies",
            "mentions": {"$sum": 1},
            "positive": {"$sum": {"$cond": [{"$eq": ["$sentiment.label", "positive"]}, 1, 0]}},
            "negative": {"$sum": {"$cond": [{"$eq": ["$sentiment.label", "negative"]}, 1, 0]}},
            "neutral":  {"$sum": {"$cond": [{"$eq": ["$sentiment.label", "neutral"]}, 1, 0]}},
        }},
        {"$sort": {"mentions": -1}},
        {"$limit": limit},
    ]
    rows = [r async for r in coll.aggregate(pipeline)]
    trending = []
    for r in rows:
        pos, neg = r["positive"], r["negative"]
        if pos > neg * 1.5: sentiment = "positive"
        elif neg > pos * 1.5: sentiment = "negative"
        else: sentiment = "neutral"
        trending.append({
            "company": r["_id"],
            "mentions": r["mentions"],
            "sentiment": sentiment,
            "sentiment_breakdown": {"positive": pos, "negative": neg, "neutral": r["neutral"]},
            "sector": next((s for c, s in __import__("company_aliases").SECTOR_BY_COMPANY.items() if c == r["_id"]), None),
        })
    return {"hours": hours, "trending": trending}


# ═══════════════════════════════════════════════════
# /news/sources — source reliability stats
# ═══════════════════════════════════════════════════
@app.get("/news/sources")
async def news_sources(hours: int = Query(24, ge=1, le=240)):
    coll = db[COLLECTION]
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    pipeline = [
        {"$match": {"fetched_at": {"$gte": since}}},
        {"$group": {
            "_id": "$source",
            "articles": {"$sum": 1},
            "with_description": {"$sum": {"$cond": [{"$gt": [{"$strLenCP": {"$ifNull": ["$description", ""]}}, 10]}, 1, 0]}},
            "last_seen": {"$max": "$fetched_at"},
            "first_seen": {"$min": "$fetched_at"},
        }},
        {"$sort": {"articles": -1}},
    ]
    rows = [r async for r in coll.aggregate(pipeline)]
    now = datetime.now(timezone.utc)
    sources = []
    for r in rows:
        last = r["last_seen"]
        if isinstance(last, datetime) and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        minutes_since = int((now - last).total_seconds() / 60) if isinstance(last, datetime) else None
        sources.append({
            "source": r["_id"],
            "articles": r["articles"],
            "with_description": r["with_description"],
            "description_pct": round(r["with_description"] / r["articles"] * 100, 1) if r["articles"] else 0,
            "minutes_since_last": minutes_since,
            "status": "fresh" if (minutes_since is not None and minutes_since < 30) else "stale",
        })
    return {"hours": hours, "total_sources": len(sources), "sources": sources}


# ═══════════════════════════════════════════════════
# /news/compare — multi-company side-by-side
# ═══════════════════════════════════════════════════
@app.get("/news/compare")
async def news_compare(
    companies: str = Query(..., description="CSV list, e.g. 'HDFC,ICICI,SBI'"),
    hours: int = Query(48, ge=1, le=240),
):
    names = _parse_companies_csv(companies) or []
    if not names:
        raise HTTPException(400, "companies cannot be empty")
    results = []
    summaries = await asyncio.gather(*[news_summary(name, hours) for name in names])
    for s in summaries:
        results.append({
            "company": s["company"],
            "total_articles": s["total_articles"],
            "sentiment_breakdown": s["sentiment_breakdown"],
            "avg_score": s["avg_score"],
            "trend": s["trend"],
        })
    # Rank by avg_score so the UI can color winners/losers
    ranked = sorted(results, key=lambda r: r["avg_score"], reverse=True)
    return {"hours": hours, "companies": ranked}


# ═══════════════════════════════════════════════════
# /news/companies, /news/sectors — directory endpoints
# ═══════════════════════════════════════════════════
@app.get("/news/companies")
async def list_companies():
    return {"total": len(ALL_COMPANIES), "companies": ALL_COMPANIES}


@app.get("/news/sectors")
async def list_sectors():
    return {"sectors": SECTORS}


# ═══════════════════════════════════════════════════
# /health, /stats — operational
# ═══════════════════════════════════════════════════
@app.get("/health")
async def health():
    now = datetime.now(timezone.utc)
    coll = db[COLLECTION]
    total = await coll.count_documents({})
    h24 = await coll.count_documents({"fetched_at": {"$gte": now - timedelta(hours=24)}})
    h1 = await coll.count_documents({"fetched_at": {"$gte": now - timedelta(hours=1)}})
    with_sentiment = await coll.count_documents({"sentiment": {"$ne": None}})
    with_companies = await coll.count_documents({"companies": {"$exists": True, "$ne": []}})
    pipeline = [
        {"$match": {"fetched_at": {"$gte": now - timedelta(hours=24)}}},
        {"$group": {"_id": "$source"}}, {"$sort": {"_id": 1}},
    ]
    sources = [d["_id"] async for d in coll.aggregate(pipeline)]
    return {
        "status": "running" if last_full_fetch else "initializing",
        "collecting_24_7": True,
        "llm_provider": active_provider(),
        "total_articles": total,
        "articles_with_sentiment": with_sentiment,
        "articles_with_companies_tagged": with_companies,
        "last_24h": h24, "last_1h": h1,
        "sources_active": len(sources), "source_names": sources,
        "feeds": FEED_COUNT,
        "last_fetch": last_full_fetch.astimezone(IST).strftime(
            "%Y-%m-%d %H:%M:%S IST") if last_full_fetch else None,
    }


@app.get("/stats")
async def stats():
    now = datetime.now(timezone.utc)
    pipeline = [
        {"$match": {"fetched_at": {"$gte": now - timedelta(hours=24)}}},
        {"$group": {"_id": "$source", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    r = [{"source": d["_id"], "articles": d["count"]}
         async for d in db[COLLECTION].aggregate(pipeline)]
    sent_pipeline = [
        {"$match": {"sentiment": {"$ne": None}}},
        {"$group": {"_id": "$sentiment.label", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    s = [{"label": d["_id"], "count": d["count"]}
         async for d in db[COLLECTION].aggregate(sent_pipeline)]
    sec_pipeline = [
        {"$match": {"sector": {"$ne": None},
                    "fetched_at": {"$gte": now - timedelta(hours=24)}}},
        {"$group": {"_id": "$sector", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    sec = [{"sector": d["_id"], "articles": d["count"]}
           async for d in db[COLLECTION].aggregate(sec_pipeline)]
    return {
        "period": "last_24h",
        "sources": r,
        "total_sources": len(r),
        "total_articles": sum(x["articles"] for x in r),
        "sentiment_distribution": s,
        "sector_distribution": sec,
    }


# ═══════════════════════════════════════════════════
# /mcp — Model Context Protocol endpoint
# ═══════════════════════════════════════════════════
MCP_TOOLS = [
    {
        "name": "search_financial_news",
        "description": "Search Indian financial news. Optionally filter by company or sector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "Company name (e.g. 'HDFC Bank'); aliases accepted."},
                "sector": {"type": "string", "enum": SECTORS},
                "hours": {"type": "integer", "default": 24, "minimum": 1, "maximum": 240},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "get_market_sentiment",
        "description": "Get aggregated sentiment (bullish/bearish/neutral) for a specific company.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "hours": {"type": "integer", "default": 24, "minimum": 1, "maximum": 240},
            },
            "required": ["company"],
        },
    },
    {
        "name": "get_trending_stocks",
        "description": "Most-mentioned companies in the last N hours with aggregate sentiment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "default": 24, "minimum": 1, "maximum": 72},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
        },
    },
]


@app.get("/mcp")
async def mcp_describe():
    """MCP discovery — returns the tools this server exposes."""
    return {
        "protocol": "mcp",
        "version": "2024-11-05",
        "server": {"name": "prism-news", "version": "5.0.0"},
        "tools": MCP_TOOLS,
    }


async def _mcp_search(args: dict) -> dict:
    company = args.get("company")
    sector = args.get("sector")
    hours = int(args.get("hours", 24))
    limit = int(args.get("limit", 20))
    companies = [company] if company else None
    arts, total, _ = await query_articles_with_sentiment(
        companies=companies, sector=sector, hours=hours, page=1, limit=limit,
    )
    return {"total": total, "returned": len(arts), "articles": arts}


async def _mcp_sentiment(args: dict) -> dict:
    company = args.get("company")
    if not company:
        raise HTTPException(400, "company required")
    hours = int(args.get("hours", 24))
    s = await news_summary(company, hours)
    # Map trend -> bullish/bearish/neutral with confidence
    breakdown = s["sentiment_breakdown"]
    total = sum(breakdown.values()) or 1
    pos_pct = breakdown["positive"] / total
    neg_pct = breakdown["negative"] / total
    if pos_pct > 0.5: verdict = "bullish"
    elif neg_pct > 0.5: verdict = "bearish"
    elif pos_pct > neg_pct + 0.15: verdict = "bullish"
    elif neg_pct > pos_pct + 0.15: verdict = "bearish"
    else: verdict = "neutral"
    confidence = round(abs(pos_pct - neg_pct), 3)
    return {
        "company": s["company"],
        "verdict": verdict,
        "confidence": confidence,
        "trend": s["trend"],
        "avg_score": s["avg_score"],
        "total_articles": s["total_articles"],
        "breakdown": breakdown,
        "top_positive": s["top_positive"],
        "top_negative": s["top_negative"],
    }


async def _mcp_trending(args: dict) -> dict:
    return await news_trending(
        hours=int(args.get("hours", 24)),
        limit=int(args.get("limit", 10)),
    )


@app.post("/mcp")
async def mcp_call(request: Request):
    """
    Invoke an MCP tool.
    Body: {"tool": "<name>", "arguments": {...}}
    """
    body = await request.json()
    tool = body.get("tool") or body.get("name")
    args = body.get("arguments") or body.get("args") or {}
    if not tool:
        raise HTTPException(400, "missing 'tool'")

    handlers = {
        "search_financial_news": _mcp_search,
        "get_market_sentiment": _mcp_sentiment,
        "get_trending_stocks": _mcp_trending,
    }
    handler = handlers.get(tool)
    if not handler:
        raise HTTPException(404, f"unknown tool: {tool}. Available: {list(handlers)}")
    result = await handler(args)
    return {"tool": tool, "result": result}


# ═══════════════════════════════════════════════════
# Root
# ═══════════════════════════════════════════════════
@app.get("/")
async def api_root():
    return {
        "name": "Prism Financial News API",
        "version": "5.0.0",
        "endpoints": {
            "/news": "main feed, filter by ?company= or ?sector=",
            "/news/summary": "?company= aggregated sentiment summary",
            "/news/trending": "most-mentioned companies",
            "/news/sources": "source reliability stats",
            "/news/compare": "?companies=a,b,c side-by-side",
            "/news/companies": "directory of canonical names",
            "/news/sectors": "supported sector chips",
            "/mcp": "GET=discover tools, POST=invoke (Claude/MCP clients)",
            "/health": "operational",
            "/stats": "rollups",
            "/docs": "swagger",
        },
        "llm_provider": active_provider(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
