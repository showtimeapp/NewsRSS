"""
Financial News Aggregator API v4.0
===================================
✅ 82 feeds, 24 sources, ~6000 articles/fetch
✅ FinBERT sentiment via HuggingFace API (company search only)
✅ Sentiment cached in MongoDB — never re-analyzed
✅ 24/7 collection, 10-min scheduler, 5-min cache threshold
✅ Link-based dedup, IST timestamps

Env vars:
  MONGO_URI    — default: mongodb://localhost:27017
  DB_NAME      — default: financial_news
  HF_TOKEN     — HuggingFace API token (free account: https://huggingface.co/settings/tokens)
"""

import os, re, logging, asyncio, time as _time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import aiohttp
import feedparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from feeds_config import ALL_FEEDS, FEED_COUNT, get_company_feeds, REFERER_MAP

# ═══════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════
MONGO_URI          = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME            = os.getenv("DB_NAME", "financial_news")
HF_TOKEN           = os.getenv("HF_TOKEN", "")
COLLECTION         = "articles"
FETCH_INTERVAL_MIN = 10
MIN_FETCH_GAP_MIN  = 5
FEED_TIMEOUT_SEC   = 8
MAX_CONCURRENT     = 80

# FinBERT model on HuggingFace Inference API
# ProsusAI/finbert is the standard financial sentiment model
# Labels: positive, negative, neutral
HF_MODEL_URL = "https://router.huggingface.co/hf-inference/models/ProsusAI/finbert"

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
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8,hi;q=0.7",
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
        if not title: continue
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
        articles.append({
            "title": title,
            "description": description,
            "source": source,
            "published_ist": ist_str,
            "published_dt": dt,
            "link": link,
            "fetched_at": now,
            "sentiment": None,  # filled later for company searches
        })
    return articles


# ═══════════════════════════════════════════════════
# ASYNC FETCHER — per-domain concurrency limits
# ═══════════════════════════════════════════════════
google_sem = asyncio.Semaphore(10)
general_sem = asyncio.Semaphore(80)


async def fetch_one(session, source_name, url):
    sem = google_sem if "news.google.com" in url else general_sem
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
                if resp.status != 200: return []
                body = await resp.read()
                return parse_feed_bytes(body, source_name, url)
        except: return []


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
# FINBERT SENTIMENT via HuggingFace Inference API
# Only runs for company-specific searches
# Results cached in MongoDB — never re-analyzed
# ═══════════════════════════════════════════════════
async def analyze_sentiments(titles: list[str]) -> list[Optional[dict]]:
    """
    Send ALL titles to FinBERT in ONE API call.
    Titles are short (~50-100 chars) so batch always works.
    Returns list of {"label": "positive/negative/neutral", "score": 0.95}
    """
    if not HF_TOKEN or not titles:
        return [None] * len(titles)

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with http_session.post(
            HF_MODEL_URL,
            headers=headers,
            json={"inputs": titles},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.warning(f"HF API {resp.status}: {(await resp.text())[:200]}")
                return [None] * len(titles)

            results = await resp.json()

            sentiments = []
            for result in results:
                if isinstance(result, list) and result:
                    top = max(result, key=lambda x: x.get("score", 0))
                    sentiments.append({
                        "label": top["label"],
                        "score": round(top["score"], 4),
                    })
                else:
                    sentiments.append(None)
            return sentiments

    except Exception as e:
        log.warning(f"HF API failed: {e}")
        return [None] * len(titles)


# ═══════════════════════════════════════════════════
# MONGODB
# ═══════════════════════════════════════════════════
async def store_articles(articles):
    if not articles: return 0
    coll = db[COLLECTION]
    seen = set(); unique = []
    for a in articles:
        if a["link"] not in seen:
            seen.add(a["link"]); unique.append(a)
    existing = set()
    async for doc in coll.find({"link": {"$in": list(seen)}}, {"link": 1}):
        existing.add(doc["link"])
    new = [a for a in unique if a["link"] not in existing]
    if not new: return 0
    try:
        r = await coll.insert_many(new, ordered=False)
        ins = len(r.inserted_ids)
    except: ins = len(new)
    log.info(f"Stored {ins} new (skipped {len(articles)-ins} dupes)")
    return ins


async def query_articles_with_sentiment(company=None, hours=24, page=1, limit=50):
    """
    Query articles. If company specified, also run sentiment on the
    EXACT articles being returned (not random ones).
    Returns (articles, total, sentiment_count).
    """
    coll = db[COLLECTION]
    q = {"fetched_at": {"$gte": datetime.now(timezone.utc) - timedelta(hours=hours)}}

    if company:
        regex = {"$regex": re.escape(company), "$options": "i"}
        q["$or"] = [{"title": regex}, {"description": regex}]

    total = await coll.count_documents(q)

    # Step 1: Fetch the paginated articles (with _id for updating)
    skip = (page - 1) * limit
    cursor = coll.find(
        q,
        {"title": 1, "description": 1, "source": 1,
         "published_ist": 1, "link": 1, "sentiment": 1},
    ).sort([("published_dt", -1), ("fetched_at", -1)]).skip(skip).limit(limit)

    articles = []
    async for doc in cursor:
        articles.append(doc)

    # Step 2: If company search, find articles in THIS page without sentiment
    sentiment_count = 0
    if company and HF_TOKEN and articles:
        # Collect articles that need sentiment
        need_sentiment = []
        for doc in articles:
            if doc.get("sentiment") is None:
                need_sentiment.append(doc)

        if need_sentiment:
            # ONE API call with all titles
            titles = [doc.get("title", "")[:150] for doc in need_sentiment]
            sentiments = await analyze_sentiments(titles)

            # Update MongoDB + in-memory docs
            coll = db[COLLECTION]
            for doc, sentiment in zip(need_sentiment, sentiments):
                if sentiment:
                    await coll.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"sentiment": sentiment}},
                    )
                    doc["sentiment"] = sentiment
                    sentiment_count += 1

            log.info(f"Sentiment: {sentiment_count}/{len(need_sentiment)} via title-only batch")

    # Step 5: Clean output — remove _id, remove null sentiment for general queries
    cleaned = []
    for doc in articles:
        doc.pop("_id", None)
        if not company and doc.get("sentiment") is None:
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
    await db[COLLECTION].create_index("link", unique=True, background=True)
    await db[COLLECTION].create_index("fetched_at", background=True)
    await db[COLLECTION].create_index("published_dt", background=True)
    await db[COLLECTION].create_index(
        [("title", "text"), ("description", "text")], background=True)
    # Index for finding articles without sentiment
    await db[COLLECTION].create_index("sentiment", background=True)
    log.info(f"MongoDB: {MONGO_URI}/{DB_NAME}")
    log.info(f"HF Token: {'configured' if HF_TOKEN else 'NOT SET — sentiment disabled'}")

    connector = aiohttp.TCPConnector(
        limit=MAX_CONCURRENT, ttl_dns_cache=300, enable_cleanup_closed=True, ssl=False)
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


app = FastAPI(title="Financial News Aggregator", version="4.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/news")
async def get_news(
    company: Optional[str] = Query(None, description="Company name (triggers sentiment analysis)"),
    hours: int = Query(24, ge=1, le=240),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
):
    """
    ## Single endpoint for all news

    **Without company:** Returns general financial news from DB (no sentiment).
    **With company:** Fetches Google News for company + runs FinBERT sentiment
    on articles that haven't been analyzed yet. Sentiment is cached forever.

    ### Response article format:
    ```json
    {
      "title": "Wipro Q4: Net profit rises 15%",
      "description": "IT major Wipro reported...",
      "source": "Economic Times",
      "published_ist": "2026-04-14 14:22:00 IST",
      "link": "https://economictimes.indiatimes.com/...",
      "sentiment": {"label": "positive", "score": 0.9521}
    }
    ```
    Sentiment is null if not yet analyzed or HF_TOKEN not set.
    """
    t = _time.monotonic()
    stale = needs_full()
    cn, fn, sn = 0, 0, 0

    if company:
        ca = await fetch_parallel(get_company_feeds(company), http_session)
        cn = await store_articles(ca)
        if stale: fn = await do_full_fetch()
    else:
        if stale: fn = await do_full_fetch()

    articles, total, sn = await query_articles_with_sentiment(company, hours, page, limit)
    elapsed = _time.monotonic() - t

    return {
        "success": True,
        "query": {"company": company, "hours": hours, "page": page, "limit": limit},
        "meta": {
            "total_results": total,
            "returned": len(articles),
            "total_pages": max(1, (total + limit - 1) // limit),
            "current_page": page,
            "feeds_configured": FEED_COUNT,
            "response_time_ms": int(elapsed * 1000),
            "last_full_fetch_ist": last_full_fetch.astimezone(IST).strftime(
                "%Y-%m-%d %H:%M:%S IST") if last_full_fetch else None,
            "new_articles": {"company_search": cn, "full_fetch": fn},
            "sentiment_analyzed_this_request": sn,
            "sentiment_enabled": bool(HF_TOKEN),
            "cache_status": "stale -> fetched" if stale else "fresh -> DB only",
        },
        "articles": articles,
    }


@app.get("/health")
async def health():
    now = datetime.now(timezone.utc)
    coll = db[COLLECTION]
    total = await coll.count_documents({})
    h24 = await coll.count_documents({"fetched_at": {"$gte": now - timedelta(hours=24)}})
    h1 = await coll.count_documents({"fetched_at": {"$gte": now - timedelta(hours=1)}})
    with_sentiment = await coll.count_documents({"sentiment": {"$ne": None}})
    pipeline = [
        {"$match": {"fetched_at": {"$gte": now - timedelta(hours=24)}}},
        {"$group": {"_id": "$source"}}, {"$sort": {"_id": 1}},
    ]
    sources = [d["_id"] async for d in coll.aggregate(pipeline)]
    return {
        "status": "running" if last_full_fetch else "initializing",
        "collecting_24_7": True,
        "sentiment_enabled": bool(HF_TOKEN),
        "total_articles": total,
        "articles_with_sentiment": with_sentiment,
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
    # Sentiment stats
    sent_pipeline = [
        {"$match": {"sentiment": {"$ne": None}}},
        {"$group": {"_id": "$sentiment.label", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    s = [{"label": d["_id"], "count": d["count"]}
         async for d in db[COLLECTION].aggregate(sent_pipeline)]
    return {
        "period": "last_24h",
        "sources": r,
        "total_sources": len(r),
        "total_articles": sum(x["articles"] for x in r),
        "sentiment_distribution": s,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)