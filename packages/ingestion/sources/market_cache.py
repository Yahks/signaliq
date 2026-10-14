"""Redis-backed cache for market data.

All fetches go through here. Cache TTL is 15 minutes by default.

Why 15 minutes: Yahoo's chart endpoint is cheap but we still want to avoid
hammering it. A 15-min TTL is fresh enough for our use case (daily-scale
watchlist tracking) and 96 fetches/day per symbol is polite.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import redis

from packages.core.logging import get_logger
from packages.ingestion.sources.market_data import Quote, fetch_quote

log = get_logger(__name__)

CACHE_TTL_SECONDS = 15 * 60  # 15 minutes
CACHE_KEY_PREFIX = "market:quote:"
CACHE_KEY_MISS = "market:quote:miss:"


def _get_redis() -> redis.Redis:
    # db 5, separate from rate limits (db 4) and broker (db 1/2)
    from packages.core.config import get_settings
    _url = get_settings().redis_url.rsplit("/", 1)[0] + "/5"
    return redis.from_url(_url, decode_responses=True)


def _quote_to_json(q: Quote) -> str:
    return json.dumps(asdict(q))


def _json_to_quote(raw: str) -> Quote:
    data = json.loads(raw)
    return Quote(**data)


async def get_quote_cached(symbol: str, *, force_refresh: bool = False) -> Quote | None:
    """Fetch a quote, using cache if available and fresh."""
    if not symbol:
        return None

    r = _get_redis()
    key = CACHE_KEY_PREFIX + symbol

    if not force_refresh:
        try:
            cached = r.get(key)
            if cached:
                return _json_to_quote(cached)
            # Also check the miss cache — prevents repeated failures for bad symbols
            if r.get(CACHE_KEY_MISS + symbol):
                return None
        except Exception as exc:
            log.warning("market_cache.redis_get_failed", error=str(exc)[:200])

    # Cache miss — fetch
    q = await fetch_quote(symbol)

    try:
        if q is not None:
            r.setex(key, CACHE_TTL_SECONDS, _quote_to_json(q))
        else:
            # Cache the miss for a shorter TTL (2 min) so we don't re-fetch bad symbols often
            r.setex(CACHE_KEY_MISS + symbol, 120, "1")
    except Exception as exc:
        log.warning("market_cache.redis_set_failed", error=str(exc)[:200])

    return q


def invalidate(symbol: str) -> None:
    """Force the next get_quote_cached to re-fetch."""
    try:
        r = _get_redis()
        r.delete(CACHE_KEY_PREFIX + symbol)
        r.delete(CACHE_KEY_MISS + symbol)
    except Exception:
        pass


def invalidate_all() -> int:
    """Clear all cached quotes. Returns count cleared."""
    try:
        r = _get_redis()
        keys = list(r.scan_iter(match=f"{CACHE_KEY_PREFIX}*"))
        keys += list(r.scan_iter(match=f"{CACHE_KEY_MISS}*"))
        if keys:
            return r.delete(*keys)
        return 0
    except Exception:
        return 0
