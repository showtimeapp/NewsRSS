"""
LLM Provider Abstraction
========================
Pluggable sentiment + entity extraction with provider fallback chain.

Default chain: OpenAI -> heuristic.
Switch via LLM_PROVIDER env var: openai | heuristic | auto.

All providers expose the same async surface:
    analyze_sentiment(text) -> {"label": "positive|negative|neutral", "score": 0.0..1.0}
    extract_companies(text) -> ["Reliance Industries", "TCS", ...]
"""
import os
import json
import asyncio
import logging
from typing import Optional

import aiohttp

log = logging.getLogger("llm-provider")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()

_openai_sem = asyncio.Semaphore(int(os.getenv("OPENAI_CONCURRENCY", "8")))


# ────────────────────────────────────────────────────────────────
# Heuristic fallback — always available, never fails
# ────────────────────────────────────────────────────────────────
_POSITIVE_WORDS = {
    "rises", "rise", "gains", "gain", "surge", "soars", "jumps", "rallies", "rally",
    "upgrade", "beat", "beats", "growth", "profit", "record", "strong", "bullish",
    "outperform", "buy", "rebound", "recovery", "expansion", "raised", "raises",
    "approved", "approval", "wins", "won", "boost", "high", "highs", "expand",
}
_NEGATIVE_WORDS = {
    "falls", "fall", "drop", "drops", "plunge", "plunges", "crash", "crashes",
    "decline", "declines", "loss", "losses", "downgrade", "miss", "missed",
    "weak", "bearish", "sell", "tumble", "tumbles", "slump", "slumps",
    "concern", "concerns", "fraud", "probe", "raid", "scam", "fine", "penalty",
    "warn", "warns", "warning", "low", "lows", "cuts", "cut",
}


def _heuristic_sentiment(text: str) -> dict:
    t = (text or "").lower()
    pos = sum(1 for w in _POSITIVE_WORDS if w in t)
    neg = sum(1 for w in _NEGATIVE_WORDS if w in t)
    if pos > neg:
        return {"label": "positive", "score": round(min(0.95, 0.5 + (pos - neg) * 0.1), 4)}
    if neg > pos:
        return {"label": "negative", "score": round(min(0.95, 0.5 + (neg - pos) * 0.1), 4)}
    return {"label": "neutral", "score": 0.5}


# ────────────────────────────────────────────────────────────────
# OpenAI provider
# ────────────────────────────────────────────────────────────────
async def _openai_sentiment(session: aiohttp.ClientSession, text: str) -> Optional[dict]:
    if not OPENAI_API_KEY:
        return None
    prompt = (
        "Classify the financial sentiment of this headline as positive, negative, or neutral. "
        "Reply with JSON only: {\"label\": \"positive|negative|neutral\", \"score\": 0.0-1.0}. "
        f"Headline: {text[:300]}"
    )
    async with _openai_sem:
        try:
            async with session.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 60,
                    "response_format": {"type": "json_object"},
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    log.debug(f"openai sentiment HTTP {resp.status}")
                    return None
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                label = parsed.get("label", "neutral").lower()
                if label not in {"positive", "negative", "neutral"}:
                    label = "neutral"
                score = float(parsed.get("score", 0.5))
                return {"label": label, "score": round(max(0.0, min(1.0, score)), 4)}
        except Exception as e:
            log.debug(f"openai sentiment error: {e}")
            return None


async def _openai_extract_companies(session: aiohttp.ClientSession, text: str) -> Optional[list]:
    if not OPENAI_API_KEY:
        return None
    prompt = (
        "Extract Indian publicly-listed companies mentioned in this financial news. "
        "Reply ONLY with JSON: {\"companies\": [\"...\"]}. Use canonical names "
        "(e.g. 'HDFC Bank', 'Reliance Industries', 'Tata Consultancy Services'). "
        "Empty list if none. "
        f"Text: {text[:500]}"
    )
    async with _openai_sem:
        try:
            async with session.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 200,
                    "response_format": {"type": "json_object"},
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                return [c.strip() for c in parsed.get("companies", []) if c and isinstance(c, str)]
        except Exception as e:
            log.debug(f"openai company extract error: {e}")
            return None


# ────────────────────────────────────────────────────────────────
# Public API — provider chain
# ────────────────────────────────────────────────────────────────
def _chain() -> list:
    if LLM_PROVIDER == "openai":
        return ["openai"]
    if LLM_PROVIDER == "heuristic":
        return ["heuristic"]
    # auto: OpenAI if key present, else heuristic
    chain = []
    if OPENAI_API_KEY:
        chain.append("openai")
    chain.append("heuristic")
    return chain


async def analyze_sentiment(session: aiohttp.ClientSession, text: str) -> dict:
    """Returns {label, score, provider}. Never fails — falls back to heuristic."""
    if not text:
        return {"label": "neutral", "score": 0.5, "provider": "heuristic"}
    for provider in _chain():
        if provider == "openai":
            r = await _openai_sentiment(session, text)
            if r:
                r["provider"] = "openai"
                return r
        elif provider == "heuristic":
            r = _heuristic_sentiment(text)
            r["provider"] = "heuristic"
            return r
    return {"label": "neutral", "score": 0.5, "provider": "heuristic"}


async def extract_companies(session: aiohttp.ClientSession, text: str) -> list:
    """Extract company mentions. Falls back to alias-based regex matching."""
    if not text:
        return []
    if OPENAI_API_KEY and LLM_PROVIDER in ("auto", "openai"):
        r = await _openai_extract_companies(session, text)
        if r is not None:
            return r
    from company_aliases import detect_companies
    return detect_companies(text)


def active_provider() -> str:
    return _chain()[0]
