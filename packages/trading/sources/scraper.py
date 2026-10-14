"""Generic HTML/JSON scraper with rate limiting, robots.txt, and Redis cache.

Policy (global, not per-source):
  - 6 requests/minute per domain (via packages.core.rate_limit.check_scraper_limit)
  - 15-minute Redis cache by default; per-source override via cache_ttl_seconds
  - robots.txt fetched + cached per domain for 1 hour; honoured when robots="check"
  - User-Agent: SignalIQ/0.1 (+contact: ops@signaliq.local) unless per-source override
  - 3 retries with exponential backoff (1s, 4s, 16s) on 5xx / timeout
  - 15s connect, 20s read
  - NEVER raises. All errors returned as ScrapeResult(ok=False, error=...).

Source config shape (from trading_subblocks.training_config.sources[]):
  {
    "id": "stockanalysis.gainers",
    "url": "https://stockanalysis.com/markets/gainers/",
    "parser": "table",           # "table" | "json" | "text"
    "robots": "allow",           # "allow" | "check" | "deny"
    "selectors": {               # for parser="table"
        "row": "table tbody tr",
        "symbol": "td:nth-child(1)",
        "name":   "td:nth-child(2)",
        "change": "td:nth-child(4)"
    },
    "user_agent": "...",         # optional
    "cache_ttl_seconds": 900     # optional (falls back to 900)
  }
"""
from __future__ import annotations

import asyncio
import json as _json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from packages.core.logging import get_logger
from packages.core.rate_limit import check_scraper_limit

log = get_logger(__name__)

DEFAULT_USER_AGENT = "SignalIQ/0.1 (+contact: ops@signaliq.local)"
DEFAULT_CACHE_TTL = 900  # 15 min
ROBOTS_CACHE_TTL = 3600  # 1 hour
CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 20.0
MAX_RETRIES = 3
BACKOFF_SCHEDULE = [1, 4, 16]  # seconds
REDIS_DB = 3  # separate from rate limit (db 4) and any app cache

_redis_client: Any = None


def _get_redis() -> Any:
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            from packages.core.config import get_settings
            s = get_settings()
            url = getattr(s, "redis_url", "redis://localhost:6379/0")
            _redis_client = redis.Redis.from_url(
                url.rsplit("/", 1)[0] + f"/{REDIS_DB}", decode_responses=True,
            )
        except Exception as exc:
            log.warning("scraper.redis_init_failed", error=str(exc)[:200])
            _redis_client = None
    return _redis_client


@dataclass
class ScrapeResult:
    ok: bool
    source_id: str
    url: str
    parser: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    cached: bool = False
    status_code: int | None = None
    bytes_read: int | None = None


# ─────────────────────────────────────────────────────────────────────
# URL template substitution
# ─────────────────────────────────────────────────────────────────────

def _substitute_url_templates(url: str) -> str:
    """Replace {{today}} and {{today_minus_N}} placeholders in a URL.

    Examples:
      "https://x/?startdt={{today_minus_7}}&enddt={{today}}"
        → "https://x/?startdt=2026-09-12&enddt=2026-09-19"
    """
    from datetime import UTC, datetime, timedelta
    import re as _re

    today = datetime.now(UTC).date()

    def _repl(m: "_re.Match[str]") -> str:
        token = m.group(1).strip()
        if token == "today":
            return today.isoformat()
        if token.startswith("today_minus_"):
            try:
                n = int(token[len("today_minus_"):])
                return (today - timedelta(days=n)).isoformat()
            except ValueError:
                return m.group(0)
        return m.group(0)

    return _re.sub(r"\{\{\s*([^}]+?)\s*\}\}", _repl, url)


# ─────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────

def _cache_key(source_id: str, url: str) -> str:
    import hashlib
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"scrape:{source_id}:{h}"


def _cache_get(source_id: str, url: str) -> list[dict[str, Any]] | None:
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(_cache_key(source_id, url))
        if raw:
            return _json.loads(raw)
    except Exception as exc:
        log.warning("scraper.cache_get_failed", error=str(exc)[:200])
    return None


def _cache_set(source_id: str, url: str, rows: list[dict[str, Any]], ttl: int) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        r.setex(_cache_key(source_id, url), ttl, _json.dumps(rows))
    except Exception as exc:
        log.warning("scraper.cache_set_failed", error=str(exc)[:200])


# ─────────────────────────────────────────────────────────────────────
# robots.txt
# ─────────────────────────────────────────────────────────────────────

def _robots_cache_key(domain: str) -> str:
    return f"robots:{domain}"


async def _robots_allows(url: str, user_agent: str) -> bool:
    """Check robots.txt for the given URL. Fails open (returns True) if fetch fails."""
    parsed = urlparse(url)
    domain = parsed.netloc
    robots_url = f"{parsed.scheme}://{domain}/robots.txt"

    r = _get_redis()
    cached: str | None = None
    if r is not None:
        try:
            cached = r.get(_robots_cache_key(domain))
        except Exception:
            pass

    text: str
    if cached:
        text = cached
    else:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
                headers={"User-Agent": user_agent},
            ) as client:
                resp = await client.get(robots_url)
                if resp.status_code != 200:
                    # No robots.txt → allow
                    return True
                text = resp.text
            if r is not None:
                try:
                    r.setex(_robots_cache_key(domain), ROBOTS_CACHE_TTL, text)
                except Exception:
                    pass
        except Exception as exc:
            log.warning("scraper.robots_fetch_failed", domain=domain, error=str(exc)[:200])
            return True  # fail open

    # Parse with a permissive UA check
    try:
        rp = RobotFileParser()
        rp.parse(text.splitlines())
        return rp.can_fetch(user_agent, url)
    except Exception as exc:
        log.warning("scraper.robots_parse_failed", domain=domain, error=str(exc)[:200])
        return True


# ─────────────────────────────────────────────────────────────────────
# HTML parsers
# ─────────────────────────────────────────────────────────────────────

def _parse_table(html: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    """Extract rows using selectolax CSS selectors."""
    from selectolax.parser import HTMLParser

    row_sel = selectors.get("row")
    if not row_sel:
        raise ValueError("table parser requires selectors.row")

    field_sels = {k: v for k, v in selectors.items() if k != "row"}

    tree = HTMLParser(html)
    out: list[dict[str, Any]] = []
    for row_node in tree.css(row_sel):
        item: dict[str, Any] = {}
        for field_name, sel in field_sels.items():
            try:
                el = row_node.css_first(sel)
                item[field_name] = el.text(strip=True) if el else None
            except Exception:
                item[field_name] = None
        # Skip rows where every field is None
        if any(v is not None for v in item.values()):
            out.append(item)
    return out


def _parse_json(payload: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    """Extract from JSON. Optional selectors.root = dotted path to the array."""
    data = _json.loads(payload)
    root = selectors.get("root") if selectors else None
    if root:
        for key in root.split("."):
            if isinstance(data, dict):
                data = data.get(key)
            else:
                data = None
                break
    if isinstance(data, list):
        return [r if isinstance(r, dict) else {"value": r} for r in data]
    if isinstance(data, dict):
        return [data]
    return []


def _parse_text(payload: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    return [{"text": payload}]


_PARSERS = {
    "table": _parse_table,
    "json": _parse_json,
    "text": _parse_text,
}


# ─────────────────────────────────────────────────────────────────────
# The fetcher
# ─────────────────────────────────────────────────────────────────────

async def fetch(source: dict[str, Any], *, force_refresh: bool = False) -> ScrapeResult:
    """Fetch + parse one configured source. Never raises."""
    source_id = source.get("id", "unknown")
    url = source.get("url", "")
    parser_name = source.get("parser", "text")
    robots_mode = source.get("robots", "allow")
    selectors = source.get("selectors", {}) or {}
    user_agent = source.get("user_agent", DEFAULT_USER_AGENT)
    cache_ttl = int(source.get("cache_ttl_seconds", DEFAULT_CACHE_TTL))

    if not url:
        return ScrapeResult(ok=False, source_id=source_id, url="", parser=parser_name,
                            error="missing url")

    # 0. Expand {{today}} / {{today_minus_N}} templates
    url = _substitute_url_templates(url)

    # 1. Cache
    if not force_refresh:
        cached = _cache_get(source_id, url)
        if cached is not None:
            log.info("scraper.cache_hit", source_id=source_id, rows=len(cached))
            return ScrapeResult(ok=True, source_id=source_id, url=url,
                                parser=parser_name, rows=cached, cached=True)

    # 2. robots.txt
    if robots_mode == "deny":
        log.warning("scraper.robots_denied", source_id=source_id, url=url)
        return ScrapeResult(ok=False, source_id=source_id, url=url,
                            parser=parser_name, error="robots_denied_by_config")
    if robots_mode == "check":
        try:
            allowed = await _robots_allows(url, user_agent)
        except Exception as exc:
            log.warning("scraper.robots_check_error", source_id=source_id, error=str(exc)[:200])
            allowed = True
        if not allowed:
            log.warning("scraper.robots_denied", source_id=source_id, url=url)
            return ScrapeResult(ok=False, source_id=source_id, url=url,
                                parser=parser_name, error="robots_denied")

    # 3. Rate limit
    domain = urlparse(url).netloc
    rl = check_scraper_limit(domain)
    if not rl.allowed:
        log.warning("scraper.rate_limited", domain=domain, retry_after=rl.retry_after_seconds)
        return ScrapeResult(ok=False, source_id=source_id, url=url,
                            parser=parser_name,
                            error=f"rate_limited retry_after={rl.retry_after_seconds}")

    # 4. Fetch with retries
    last_error: str | None = None
    last_status: int | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
                headers={"User-Agent": user_agent},
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
            last_status = resp.status_code
            if resp.status_code >= 500:
                last_error = f"http_{resp.status_code}"
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(BACKOFF_SCHEDULE[attempt])
                    continue
                break
            if resp.status_code >= 400:
                last_error = f"http_{resp.status_code}"
                break
            # 2xx — parse
            body = resp.text
            bytes_read = len(body.encode("utf-8", errors="ignore"))
            try:
                parse_fn = _PARSERS.get(parser_name, _parse_text)
                rows = parse_fn(body, selectors)
            except Exception as exc:
                log.warning("scraper.parse_failed", source_id=source_id,
                            parser=parser_name, error=str(exc)[:200])
                return ScrapeResult(ok=False, source_id=source_id, url=url,
                                    parser=parser_name,
                                    error=f"parse_failed: {exc}",
                                    status_code=last_status, bytes_read=bytes_read)

            log.info("scraper.fetch_ok", source_id=source_id, rows=len(rows),
                     status=last_status, bytes=bytes_read)
            if rows:
                _cache_set(source_id, url, rows, cache_ttl)
            return ScrapeResult(ok=True, source_id=source_id, url=url,
                                parser=parser_name, rows=rows,
                                status_code=last_status, bytes_read=bytes_read)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
            last_error = type(exc).__name__
            if attempt < MAX_RETRIES:
                await asyncio.sleep(BACKOFF_SCHEDULE[attempt])
                continue
            break
        except Exception as exc:
            last_error = str(exc)[:200]
            break

    log.warning("scraper.fetch_failed", source_id=source_id, error=last_error,
                status=last_status)
    return ScrapeResult(ok=False, source_id=source_id, url=url,
                        parser=parser_name, error=last_error or "unknown_error",
                        status_code=last_status)
