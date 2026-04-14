"""
RSS Feed Health Checker v4
Usage:
  python test_feeds.py              → test active feeds
  python test_feeds.py --retest     → also test removed feeds
"""

import asyncio, json, re, sys, time
from datetime import datetime, timedelta, timezone

import aiohttp
import feedparser
from feeds_config import ALL_FEEDS, get_company_feeds, FEEDS_TO_RETEST, REFERER_MAP

IST = timezone(timedelta(hours=5, minutes=30))

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

google_sem = asyncio.Semaphore(10)
general_sem = asyncio.Semaphore(80)

def clean_html(text):
    if not text: return ""
    c = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", c).strip()

def extract_description(entry):
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
    return best[:300]


async def test_single(session, source_name, url):
    sem = google_sem if "news.google.com" in url else general_sem
    result = {
        "source": source_name, "url": url, "status": "FAIL",
        "http_code": None, "articles": 0, "with_description": 0,
        "sample_title": "", "sample_desc": "", "sample_link": "",
        "sample_date": "", "error": None, "response_time_ms": 0,
    }
    start = time.monotonic()
    async with sem:
        try:
            headers = dict(BROWSER_HEADERS)
            for domain, referer in REFERER_MAP.items():
                if domain in url:
                    headers["Referer"] = referer
                    break
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=8),
                                   ssl=False) as resp:
                result["http_code"] = resp.status
                result["response_time_ms"] = int((time.monotonic() - start) * 1000)
                if resp.status != 200:
                    result["error"] = f"HTTP {resp.status}"
                    return result
                body = await resp.read()
                feed = feedparser.parse(body)
                if feed.bozo and not feed.entries:
                    result["error"] = f"Parse error: {feed.bozo_exception}"
                    return result
                result["articles"] = len(feed.entries)
                desc_count = 0
                for entry in feed.entries:
                    d = extract_description(entry)
                    if d and len(d) > 10: desc_count += 1
                result["with_description"] = desc_count
                if feed.entries:
                    first = feed.entries[0]
                    result["sample_title"] = clean_html(first.get("title", ""))[:100]
                    result["sample_desc"] = extract_description(first)[:200]
                    result["sample_link"] = (first.get("link") or first.get("id") or "")[:200]
                    result["sample_date"] = first.get("published") or first.get("updated") or ""
                    result["status"] = "OK"
                else:
                    result["status"] = "EMPTY"
                    result["error"] = "0 articles"
        except asyncio.TimeoutError:
            result["response_time_ms"] = int((time.monotonic() - start) * 1000)
            result["error"] = "TIMEOUT (8s)"
        except Exception as e:
            result["response_time_ms"] = int((time.monotonic() - start) * 1000)
            result["error"] = str(e)[:200]
    return result


async def run_tests(include_retest=False):
    test_feeds = list(ALL_FEEDS) + get_company_feeds("Reliance Industries")
    label = "ACTIVE FEEDS"
    if include_retest:
        test_feeds += FEEDS_TO_RETEST
        label = "ACTIVE + PREVIOUSLY REMOVED"

    print("=" * 80)
    print(f"  RSS FEED HEALTH CHECK v4 — {label}")
    print(f"  Testing {len(test_feeds)} feeds | Timeout: 8s | Google limit: 10 concurrent")
    print(f"  {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
    print("=" * 80)
    print()

    connector = aiohttp.TCPConnector(limit=80, ttl_dns_cache=300, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        start = time.monotonic()
        tasks = [test_single(session, n, u) for n, u in test_feeds]
        results = await asyncio.gather(*tasks)
        total_time = time.monotonic() - start

    ok = [r for r in results if r["status"] == "OK"]
    empty = [r for r in results if r["status"] == "EMPTY"]
    fail = [r for r in results if r["status"] == "FAIL"]
    total_arts = sum(r["articles"] for r in results)
    total_desc = sum(r["with_description"] for r in results)

    # Estimate unique articles (dedup by link)
    all_links = set()
    # We can't get all links from test, but we can estimate from sample
    unique_est = int(total_arts * 0.4)  # ~40% unique based on typical overlap

    print(f"{'#':<4} {'STATUS':<7} {'MS':>5} {'ARTS':>5} {'DESC':>5}  {'SOURCE':<25} {'URL'}")
    print("-" * 120)
    for i, r in enumerate(results, 1):
        icon = {"OK": "✅", "EMPTY": "⚠️ ", "FAIL": "❌"}[r["status"]]
        url_short = r["url"][:55] + "..." if len(r["url"]) > 58 else r["url"]
        print(f"{i:<4} {icon}{r['status']:<5} {r['response_time_ms']:>5} "
              f"{r['articles']:>5} {r['with_description']:>5}  "
              f"{r['source']:<25} {url_short}")

    print()
    print("=" * 80)
    print(f"  ✅ Working: {len(ok)}   ⚠️ Empty: {len(empty)}   ❌ Failed: {len(fail)}")
    print(f"  Total articles: {total_arts}   With description: {total_desc}/{total_arts}")
    print(f"  Estimated unique (after dedup): ~{unique_est}")
    print(f"  Fetch time: {total_time:.2f}s")
    print("=" * 80)

    if fail:
        print("\n  ── FAILED ──")
        for r in fail:
            print(f"  ❌ [{r['source']}] {r['url'][:70]}")
            print(f"     {r['error']}")

    if empty:
        print("\n  ── EMPTY ──")
        for r in empty:
            print(f"  ⚠️  [{r['source']}] {r['url'][:70]}")

    # Sample with link
    print("\n  ── SAMPLES (title + link + description) ──")
    shown = set()
    for r in ok:
        if r["source"] in shown: continue
        shown.add(r["source"])
        if len(shown) > 15: break
        desc = r["sample_desc"][:100] + "..." if r["sample_desc"] else "(no desc)"
        print(f"  📰 [{r['source']}]")
        print(f"     Title: {r['sample_title'][:80]}")
        print(f"     Link:  {r['sample_link'][:100]}")
        print(f"     Desc:  {desc}")
        print()

    sources = sorted(set(r["source"] for r in ok))
    print(f"  ── WORKING SOURCES ({len(sources)}) ──")
    print(f"  {', '.join(sources)}")

    with open("test_results.json", "w") as f:
        json.dump({"tested_at": datetime.now(IST).isoformat(),
                    "ok": len(ok), "empty": len(empty), "fail": len(fail),
                    "total_articles": total_arts, "descriptions": total_desc,
                    "estimated_unique": unique_est,
                    "fetch_time": round(total_time, 2), "results": results},
                   f, indent=2, default=str)
    print(f"\n  Saved: test_results.json")
    return len(fail)


if __name__ == "__main__":
    retest = "--retest" in sys.argv
    fails = asyncio.run(run_tests(include_retest=retest))
    exit(0 if fails == 0 else 1)