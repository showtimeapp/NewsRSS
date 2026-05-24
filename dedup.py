"""
Article Dedup & URL Resolution
==============================
- normalized_title_key: stricter md5 key (strips numbers, punctuation, suffix noise)
- fuzzy_dedup: in-memory near-duplicate collapse using token-set overlap
- resolve_google_url: follows google.news redirect to the underlying publisher URL

Token-set ratio chosen over Levenshtein because headlines often add/drop a
single qualifier ("Q4" / "FY26") — that's a 5-char edit on a 70-char string
which would slip past a 5% Levenshtein gate but a 0.92 token overlap catches it.
"""
import re
import hashlib
import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

import aiohttp

log = logging.getLogger("dedup")

_STOP = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and", "or",
    "is", "are", "was", "were", "be", "by", "from", "as", "it", "its", "this",
    "that", "these", "those", "s", "rs", "cr", "lakh", "crore",
}
_NOISE_SUFFIXES = re.compile(
    r"\s*[-—|:]\s*(reuters|bloomberg|moneycontrol|economic times|et|mint|business standard|bs|the hindu|cnbc tv18|cnbc|ndtv profit|ndtv|business today|forbes india|the print)\s*$",
    re.IGNORECASE,
)


def _normalize(title: str) -> str:
    if not title:
        return ""
    t = title.lower()
    t = _NOISE_SUFFIXES.sub("", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\b\d{1,4}\b", "", t)  # drop standalone numbers (article IDs, dates)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _tokens(title: str) -> set:
    return {w for w in _normalize(title).split() if w and w not in _STOP and len(w) > 2}


def title_key(title: str) -> str:
    """Stable hash for exact-after-normalization dedup."""
    return hashlib.md5(_normalize(title).encode()).hexdigest()


def is_fuzzy_duplicate(a: str, b: str, threshold: float = 0.85) -> bool:
    """True if title-token Jaccard >= threshold."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    return (inter / union) >= threshold if union else False


def fuzzy_dedup(articles: list, threshold: float = 0.85) -> list:
    """
    Collapse near-duplicate titles. Keeps the article with the longest description
    (proxy for richest content), or earliest published_dt as tiebreaker.
    O(n²) — fine for a single response page (<=500 items).
    """
    if not articles:
        return []
    kept = []
    kept_tokens = []
    for a in articles:
        title = a.get("title", "")
        toks = _tokens(title)
        dup_idx = -1
        for i, prev in enumerate(kept_tokens):
            if not toks or not prev:
                continue
            inter = len(toks & prev)
            union = len(toks | prev)
            if union and (inter / union) >= threshold:
                dup_idx = i
                break
        if dup_idx == -1:
            kept.append(a)
            kept_tokens.append(toks)
        else:
            existing = kept[dup_idx]
            # Prefer the one with a non-google.com link, then longer description
            existing_is_google = "news.google.com" in (existing.get("link") or "")
            new_is_google = "news.google.com" in (a.get("link") or "")
            if existing_is_google and not new_is_google:
                kept[dup_idx] = a
                kept_tokens[dup_idx] = toks
            elif len(a.get("description", "") or "") > len(existing.get("description", "") or ""):
                kept[dup_idx] = a
                kept_tokens[dup_idx] = toks
    return kept


# ────────────────────────────────────────────────────────────────
# Google News URL resolver
# ────────────────────────────────────────────────────────────────
_resolve_sem = asyncio.Semaphore(8)
_resolve_cache: dict = {}


def is_google_news_url(url: str) -> bool:
    return "news.google.com" in (url or "")


async def resolve_google_url(session: aiohttp.ClientSession, url: str, timeout: float = 6.0) -> str:
    """
    Follow a news.google.com redirect chain to the publisher URL.
    Returns original URL on failure. Cached in-process.
    """
    if not is_google_news_url(url):
        return url
    if url in _resolve_cache:
        return _resolve_cache[url]
    async with _resolve_sem:
        try:
            async with session.get(
                url,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=timeout),
                ssl=False,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                final = str(resp.url)
                if "news.google.com" in final:
                    # Some chains keep us on google — try parsing url= query param
                    qs = parse_qs(urlparse(final).query)
                    if "url" in qs and qs["url"]:
                        final = unquote(qs["url"][0])
                _resolve_cache[url] = final
                return final
        except Exception as e:
            log.debug(f"resolve_google_url failed for {url[:60]}: {e}")
            _resolve_cache[url] = url
            return url


# ────────────────────────────────────────────────────────────────
# Hindi / Devanagari filter
# ────────────────────────────────────────────────────────────────
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def is_hindi(text: str, threshold: float = 0.10) -> bool:
    """True if >=10% of chars are Devanagari (catches Hindi/Marathi/Sanskrit)."""
    if not text:
        return False
    matches = _DEVANAGARI.findall(text)
    return (len(matches) / len(text)) >= threshold


def filter_non_hindi(articles: list) -> list:
    return [a for a in articles if not is_hindi(a.get("title", "") + " " + a.get("description", ""))]
