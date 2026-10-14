"""FRED (Federal Reserve Economic Data) client.

Free, government source. No ToS concerns. Rate limit ~120 req/min.

Caches observations in Redis (db 3, same as scraper) for 1 hour —
FRED data updates slowly, so this is safe.
"""
from __future__ import annotations

import json as _json
from typing import Any

import httpx

from packages.core.logging import get_logger

log = get_logger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred"
DEFAULT_TIMEOUT = 15.0
CACHE_TTL_SECONDS = 3600  # 1 hour
REDIS_DB = 3  # same as scraper

_redis_client: Any = None


def _get_redis() -> Any:
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            from packages.core.config import get_settings
            s = get_settings()
            url = getattr(s, "redis_url", "redis://localhost:6379/0")
            base = url.rsplit("/", 1)[0] if "/" in url else url
            _redis_client = redis.Redis.from_url(
                f"{base}/{REDIS_DB}", decode_responses=True,
            )
        except Exception as exc:
            log.warning("fred.redis_init_failed", error=str(exc)[:200])
            _redis_client = None
    return _redis_client


def _api_key() -> str | None:
    try:
        from packages.core.config import get_settings
        return getattr(get_settings(), "fred_api_key", None)
    except Exception:
        return None


async def fetch_series(
    series_id: str,
    *,
    limit: int = 30,
    sort_order: str = "desc",
) -> list[dict[str, Any]] | None:
    """Fetch the last N observations for a FRED series.

    Returns [{date: 'YYYY-MM-DD', value: float}, ...] newest first.
    Returns None on failure, never raises.
    """
    key = _api_key()
    if not key:
        log.warning("fred.no_api_key", series=series_id)
        return None

    cache_key = f"fred:series:{series_id}:{limit}:{sort_order}"
    r = _get_redis()
    if r is not None:
        try:
            cached = r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    url = f"{FRED_BASE}/series/observations"
    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "limit": limit,
        "sort_order": sort_order,
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("fred.fetch_failed", series=series_id, error=str(exc)[:200])
        return None

    out: list[dict[str, Any]] = []
    for obs in data.get("observations", []):
        v = obs.get("value")
        if v is None or v == ".":
            continue
        try:
            out.append({
                "date": obs.get("date"),
                "value": float(v),
            })
        except (ValueError, TypeError):
            continue

    if r is not None and out:
        try:
            r.setex(cache_key, CACHE_TTL_SECONDS, _json.dumps(out))
        except Exception:
            pass

    log.info("fred.series_fetched", series=series_id, count=len(out))
    return out


async def fetch_releases(days_ahead: int = 14) -> list[dict[str, Any]] | None:
    """Fetch upcoming economic release dates within the next N days.

    Returns [{release_id, release_name, date}, ...] sorted ascending.
    Returns None on failure.
    """
    key = _api_key()
    if not key:
        return None

    from datetime import UTC, datetime, timedelta

    cache_key = f"fred:releases:{days_ahead}"
    r = _get_redis()
    if r is not None:
        try:
            cached = r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    today = datetime.now(UTC).date()
    end = today + timedelta(days=days_ahead)

    url = f"{FRED_BASE}/releases/dates"
    params = {
        "api_key": key,
        "file_type": "json",
        "realtime_start": today.isoformat(),
        "realtime_end": end.isoformat(),
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc",
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("fred.releases_failed", error=str(exc)[:200])
        return None

    out: list[dict[str, Any]] = []
    for rel in data.get("release_dates", []):
        out.append({
            "release_id": rel.get("release_id"),
            "release_name": rel.get("release_name"),
            "date": rel.get("date"),
        })

    if r is not None and out:
        try:
            r.setex(cache_key, CACHE_TTL_SECONDS, _json.dumps(out))
        except Exception:
            pass

    log.info("fred.releases_fetched", count=len(out), days=days_ahead)
    return out
