"""Jurisdiction gating.

Blocks API access from countries where football-prediction content is
illegal or requires a license we don't have.

Never trusts the client. Uses:
  1. X-Forwarded-For header (set by our reverse proxy)
  2. Fallback to direct socket IP (works in dev)
  3. Optional country header (CF-IPCountry from Cloudflare, X-Vercel-IP-Country)

Country lookup is cached in Redis for 24h per IP.
"""
from __future__ import annotations

import ipaddress
from typing import Any

import httpx
import redis

from packages.core.logging import get_logger

log = get_logger(__name__)

# Countries where we refuse service (football prediction is illegal or
# requires a gambling license we don't hold).
BLOCKED_COUNTRIES: set[str] = {
    # Middle East — betting illegal
    "SA", "AE", "QA", "KW", "BH", "OM", "IR", "YE",
    # Sanctioned
    "KP", "CU", "SY",
    # US states where tipster services are banned (state-level, not country-level;
    # handled separately via a future US-state gate)
    # (leaving US allowed at country level for now)
}

# Cache TTL for IP → country lookups (24h)
IP_COUNTRY_CACHE_TTL = 86400

_redis: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        from packages.core.config import get_settings
        _url = get_settings().redis_url.rsplit("/", 1)[0] + "/6"
        _redis = redis.from_url(_url, decode_responses=True)
    return _redis


def _is_private_ip(ip: str) -> bool:
    """True if the IP is not routable (localhost, LAN)."""
    try:
        obj = ipaddress.ip_address(ip)
        return obj.is_private or obj.is_loopback or obj.is_reserved
    except ValueError:
        return False


def _lookup_country(ip: str) -> str | None:
    """Look up country code for an IP. Uses ip-api.com (free tier).

    Returns ISO 3166-1 alpha-2 code, or None if lookup fails.
    """
    if _is_private_ip(ip):
        return None  # Don't geolocate localhost/LAN

    # Check cache
    try:
        r = _get_redis()
        cached = r.get(f"ipcountry:{ip}")
        if cached is not None:
            return cached if cached else None
    except Exception:
        pass

    # Live lookup
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(
                f"http://ip-api.com/json/{ip}",
                params={"fields": "status,countryCode"},
            )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("status") != "success":
            return None
        country = data.get("countryCode") or None
    except Exception as exc:
        log.warning("jurisdiction.lookup_failed", ip=ip, error=str(exc)[:120])
        return None

    # Cache the result (empty string = "lookup failed", to avoid repeated failures)
    try:
        r = _get_redis()
        r.setex(f"ipcountry:{ip}", IP_COUNTRY_CACHE_TTL, country or "")
    except Exception:
        pass

    return country


def get_client_ip(headers: dict[str, str], fallback: str | None = None) -> str:
    """Extract the best-guess client IP from headers, or fall back to socket."""
    xff = headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return fallback or ""


def check_jurisdiction(
    *,
    headers: dict[str, str],
    fallback_ip: str | None = None,
) -> tuple[bool, str | None]:
    """Return (allowed, country_code).

    If country headers are present (Cloudflare/Vercel), uses them directly.
    Otherwise geolocates the IP.
    """
    # 1. Trust CDN headers if present (production behind Cloudflare/Vercel)
    cf_country = headers.get("cf-ipcountry", "").upper()
    if cf_country and cf_country != "XX":
        return (cf_country not in BLOCKED_COUNTRIES, cf_country)

    vercel_country = headers.get("x-vercel-ip-country", "").upper()
    if vercel_country:
        return (vercel_country not in BLOCKED_COUNTRIES, vercel_country)

    # 2. Fall back to geolocation
    ip = get_client_ip(headers, fallback=fallback_ip)
    if not ip:
        return (True, None)  # No IP → cannot determine, allow (dev default)

    country = _lookup_country(ip)
    if country is None:
        # Couldn't determine → allow but log
        return (True, None)

    return (country not in BLOCKED_COUNTRIES, country)
