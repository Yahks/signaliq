"""Redis-backed rate limiter.

Two flavors:
  - Sliding window counter (INCR + EXPIRE): simple, O(1), fine for our scale
  - Fixed window with a single key per window

Semantics:
  - First request sets the TTL. Subsequent requests increment.
  - If count > limit within the window, return Retry-After.
  - Key namespaces let us limit per IP, per user, per org, per route.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import redis

from packages.core.config import get_settings
from packages.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class RateLimitResult:
    allowed: bool
    current: int
    limit: int
    window_seconds: int
    retry_after_seconds: int


# ---- singleton redis client ----
_redis: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        from packages.core.config import get_settings
        s = get_settings()
        # Prefer explicit REDIS_URL; fall back to localhost
        url = getattr(s, "redis_url", "redis://localhost:6379/0")
        _redis = redis.Redis.from_url(url.rsplit("/", 1)[0] + "/4", decode_responses=True)
    return _redis


def check_rate_limit(
    *,
    namespace: Literal["ip", "auth", "org", "user", "scraper"],
    identifier: str,
    route: str,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    """Check + increment a rate limit counter. Returns whether the request may proceed."""
    key = f"rl:{namespace}:{identifier}:{route}"
    try:
        r = _get_redis()
        # Pipeline: INCR then EXPIRE-if-new. Race-safe enough for our use case.
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()

        # If this is a fresh key (ttl == -1), set expiry now.
        if ttl is None or ttl < 0:
            r.expire(key, window_seconds)
            ttl = window_seconds

        allowed = count <= limit
        retry_after = max(0, int(ttl)) if not allowed else 0

        return RateLimitResult(
            allowed=allowed,
            current=count,
            limit=limit,
            window_seconds=window_seconds,
            retry_after_seconds=retry_after,
        )
    except Exception as exc:
        # Redis unavailable → fail open (better than 500s when Redis is down)
        log.warning("ratelimit.redis_failed", error=str(exc)[:200])
        return RateLimitResult(
            allowed=True,
            current=0,
            limit=limit,
            window_seconds=window_seconds,
            retry_after_seconds=0,
        )


# ---- Named limiters for specific routes ----

def check_ip_limit(ip: str, route: str) -> RateLimitResult:
    return check_rate_limit(
        namespace="ip", identifier=ip, route=route,
        limit=100, window_seconds=60,
    )


def check_auth_limit(ip: str) -> RateLimitResult:
    return check_rate_limit(
        namespace="auth", identifier=ip, route="auth",
        limit=10, window_seconds=60,
    )


def check_org_fetch_limit(org_id: str) -> RateLimitResult:
    return check_rate_limit(
        namespace="org", identifier=org_id, route="fetch-stock",
        limit=10, window_seconds=3600,
    )


def check_org_chain_build_limit(org_id: str) -> RateLimitResult:
    return check_rate_limit(
        namespace="org", identifier=org_id, route="chain-build",
        limit=30, window_seconds=3600,
    )


def check_user_ai_limit(user_id: str) -> RateLimitResult:
    return check_rate_limit(
        namespace="user", identifier=user_id, route="ai",
        limit=200, window_seconds=3600,
    )

def check_scraper_limit(domain: str, *, limit: int = 6, window_seconds: int = 60) -> RateLimitResult:
    """Per-domain rate limit for outbound scraping.

    Default 6 requests/minute per domain. Fails open if Redis is down
    (matches the rest of this module's behaviour) — but the scraper
    additionally respects its own in-process token bucket so a Redis
    outage doesn't cause a burst.
    """
    return check_rate_limit(
        namespace="scraper",
        identifier=domain,
        route="fetch",
        limit=limit,
        window_seconds=window_seconds,
    )
