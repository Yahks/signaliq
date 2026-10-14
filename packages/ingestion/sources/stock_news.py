"""US stock news fetcher.

Pulls from:
  - Yahoo Finance per-ticker RSS
  - SEC EDGAR official filings API
  - General market RSS feeds (CNBC, MarketWatch, Investing.com, Nasdaq)

Returns a list of SourceRow objects. No LLM. No DB writes.
The caller (Celery task) handles event extraction and persistence.

Compliance:
  - Respects robots.txt
  - Enforces per-host rate limits
  - Identifies itself via SEC_USER_AGENT
  - Attribution preserved on every row
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from packages.core.config import get_settings
from packages.core.logging import get_logger
from packages.ingestion.sources.robots import can_fetch, crawl_delay

log = get_logger(__name__)

# --- Feeds ---
# NOTE: Yahoo Finance per-ticker RSS is DISALLOWED in robots.txt (verified 2026-09).
# We no longer fetch it. Ticker-specific news comes from SEC filings + general feeds.
YAHOO_TICKER_RSS = None  # disabled

# General market RSS feeds.
# Only feeds whose robots.txt allows us are kept.
# MarketWatch (feeds.content.dowjones.io) returns 403 to robots.txt fetches
# from non-browser UAs — excluded until we have an explicit compliance decision.
GENERAL_FEEDS: dict[str, str] = {
    "CNBC Markets": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "Nasdaq Stocks": "https://www.nasdaq.com/feed/rssoutbound?category=Stocks",
    "Investing.com Stock News": "https://www.investing.com/rss/news_25.rss",
}

# Whether to require robots.txt to explicitly allow a URL before fetching.
# Set False ONLY for sources with an explicit compliance waiver.
REQUIRE_ROBOTS_ALLOW = True

# --- SEC ---
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
SEC_CATALYST_FORMS = {"8-K", "10-Q", "10-K", "S-1", "425", "6-K"}

# --- Catalyst keywords ---
CATALYST_KEYWORDS = [
    "guidance", "raises forecast", "cuts forecast", "lowers guidance",
    "raises guidance", "backlog", "new contract", "order", "orders",
    "demand", "supply agreement", "partnership", "acquisition", "merger",
    "beats estimates", "misses estimates", "beat expectations",
    "miss expectations", "restructuring", "layoffs", "recall",
    "price target", "upgrade", "downgrade", "buyback", "dividend",
    "warns", "warning", "delays", "launch", "expansion", "capacity",
]
CATALYST_REGEX = re.compile(
    r"(" + "|".join(re.escape(k) for k in CATALYST_KEYWORDS) + r")", re.IGNORECASE
)


@dataclass
class SourceRow:
    """One fetched article/filing, ready to be converted to a news event."""
    source_name: str
    source_url: str
    title: str
    summary: str  # RSS summary or article excerpt (may be empty)
    published_at: str | None  # ISO string
    ticker: str | None = None
    catalyst_flag: bool = False
    matched_keywords: list[str] = field(default_factory=list)
    full_content: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        """Stable identity for deduplication."""
        url = (self.source_url or "").strip().lower()
        title = (self.title or "").strip().lower()
        return f"{url}|{title}"


def _flag_catalyst(text: str) -> tuple[bool, list[str]]:
    if not text:
        return False, []
    matches = sorted(set(m.group(0).lower() for m in CATALYST_REGEX.finditer(text)))
    return (len(matches) > 0), matches


# ---------------------------------------------------------------------------
# Rate-limited HTTP client
# ---------------------------------------------------------------------------

class _RateLimited:
    """Simple per-host rate limiter. Enforces a minimum delay between requests."""

    def __init__(self, default_delay: float) -> None:
        self.default_delay = default_delay
        self._last_request: dict[str, float] = {}

    def wait(self, url: str, *, per_host_delay: float | None = None) -> None:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        delay = per_host_delay if per_host_delay is not None else self.default_delay
        last = self._last_request.get(host, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request[host] = time.monotonic()


# ---------------------------------------------------------------------------
# Fetch: Yahoo Finance RSS per ticker
# ---------------------------------------------------------------------------

def fetch_yahoo_ticker_news(
    ticker: str,
    *,
    client: httpx.Client,
    limiter: _RateLimited,
    user_agent: str,
) -> list[SourceRow]:
    """DISABLED — Yahoo Finance per-ticker RSS is disallowed in robots.txt.

    Kept as a stub so callers don't need to change. Returns [] always.
    """
    return []


# ---------------------------------------------------------------------------
# Fetch: SEC EDGAR filings
# ---------------------------------------------------------------------------

_ticker_to_cik_cache: dict[str, str] | None = None


def _load_ticker_to_cik(
    *,
    client: httpx.Client,
    limiter: _RateLimited,
    user_agent: str,
) -> dict[str, str]:
    global _ticker_to_cik_cache
    if _ticker_to_cik_cache is not None:
        return _ticker_to_cik_cache
    if not can_fetch(SEC_TICKER_MAP_URL, user_agent):
        log.warning("stock.sec_ticker_map_disallowed")
        _ticker_to_cik_cache = {}
        return {}
    limiter.wait(SEC_TICKER_MAP_URL, per_host_delay=1.0)
    try:
        r = client.get(SEC_TICKER_MAP_URL, headers={"User-Agent": user_agent}, timeout=15.0)
        r.raise_for_status()
        data = r.json()
        mapping = {e["ticker"].upper(): str(e["cik_str"]).zfill(10) for e in data.values()}
        _ticker_to_cik_cache = mapping
        log.info("stock.sec_ticker_map_loaded", count=len(mapping))
        return mapping
    except Exception as exc:
        log.warning("stock.sec_ticker_map_failed", error=str(exc)[:200])
        _ticker_to_cik_cache = {}
        return {}


def fetch_sec_filings(
    ticker: str,
    *,
    client: httpx.Client,
    limiter: _RateLimited,
    user_agent: str,
    max_filings: int = 10,
) -> list[SourceRow]:
    mapping = _load_ticker_to_cik(client=client, limiter=limiter, user_agent=user_agent)
    cik10 = mapping.get(ticker.upper())
    if not cik10:
        log.info("stock.sec_no_cik", ticker=ticker)
        return []

    url = SEC_SUBMISSIONS_URL.format(cik10=cik10)
    if not can_fetch(url, user_agent):
        return []
    limiter.wait(url, per_host_delay=1.0)

    try:
        r = client.get(url, headers={"User-Agent": user_agent}, timeout=15.0)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.warning("stock.sec_filings_failed", ticker=ticker, error=str(exc)[:200])
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    rows: list[SourceRow] = []
    for form, date, accn, doc in zip(forms, dates, accns, docs):
        if form not in SEC_CATALYST_FORMS:
            continue
        accn_nodash = accn.replace("-", "")
        link = f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{accn_nodash}/{doc}"
        rows.append(SourceRow(
            source_name=f"SEC EDGAR ({form})",
            source_url=link,
            title=f"{ticker} filed {form} on {date}",
            summary=f"SEC filing: {form} filed by {ticker} on {date}",
            published_at=f"{date}T00:00:00Z" if date else None,
            ticker=ticker,
            catalyst_flag=True,
            matched_keywords=[form],
            extra={"form_type": form, "filing_date": date},
        ))
        if len(rows) >= max_filings:
            break
    log.info("stock.sec_filings", ticker=ticker, count=len(rows))
    return rows


# ---------------------------------------------------------------------------
# Fetch: general market RSS feeds
# ---------------------------------------------------------------------------

def fetch_general_feeds(
    *,
    client: httpx.Client,
    limiter: _RateLimited,
    user_agent: str,
) -> list[SourceRow]:
    rows: list[SourceRow] = []
    for name, url in GENERAL_FEEDS.items():
        if not can_fetch(url, user_agent):
            log.warning("stock.general_feed_disallowed", name=name)
            continue
        limiter.wait(url)
        try:
            r = client.get(url, headers={"User-Agent": user_agent}, timeout=15.0)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("stock.general_feed_failed", name=name, error=str(exc)[:200])
            continue
        feed = feedparser.parse(r.content)
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()
            if not title:
                continue
            is_cat, kws = _flag_catalyst(f"{title} {summary}")
            rows.append(SourceRow(
                source_name=name,
                source_url=entry.get("link", ""),
                title=title,
                summary=summary[:2000],
                published_at=entry.get("published", "") or None,
                ticker=None,
                catalyst_flag=is_cat,
                matched_keywords=kws,
            ))
        log.info("stock.general_feed_fetched", name=name, count=len(feed.entries))
    return rows


# ---------------------------------------------------------------------------
# Optional: full article text (dev = off by default)
# ---------------------------------------------------------------------------

def enrich_with_content(rows: list[SourceRow], *, user_agent: str, limiter: _RateLimited) -> None:
    """Download full article text for each row that has a URL. Mutates rows in place.

    Requires trafilatura. Slow — for dev, keep STOCK_NEWS_FETCH_CONTENT=false.
    """
    try:
        import trafilatura
    except ImportError:
        log.warning("stock.content_enrich_skipped", reason="trafilatura not installed")
        return

    for row in rows:
        if not row.source_url:
            continue
        if not can_fetch(row.source_url, user_agent):
            continue
        limiter.wait(row.source_url)
        try:
            downloaded = trafilatura.fetch_url(row.source_url)
            if not downloaded:
                continue
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            if text:
                row.full_content = text[:20000]
        except Exception as exc:
            log.warning("stock.content_fetch_failed", url=row.source_url, error=str(exc)[:200])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_all(
    *,
    tickers: list[str] | None = None,
    include_general: bool = True,
    include_sec: bool = True,
    fetch_content: bool | None = None,
) -> list[SourceRow]:
    """Fetch from all configured sources. Returns deduplicated rows.

    Args:
        tickers: List of ticker symbols. Defaults to STOCK_NEWS_TICKERS env.
        include_general: Include general market RSS feeds.
        include_sec: Include SEC EDGAR filings.
        fetch_content: Download full article text (slow). Defaults to env flag.
    """
    settings = get_settings()
    user_agent = settings.sec_user_agent
    delay = settings.stock_news_request_delay
    if fetch_content is None:
        fetch_content = settings.stock_news_fetch_content

    if tickers is None:
        tickers = [t.strip().upper() for t in settings.stock_news_tickers.split(",") if t.strip()]

    limiter = _RateLimited(default_delay=delay)
    rows: list[SourceRow] = []

    with httpx.Client() as client:
        for ticker in tickers:
            rows.extend(fetch_yahoo_ticker_news(
                ticker, client=client, limiter=limiter, user_agent=user_agent,
            ))
            if include_sec:
                rows.extend(fetch_sec_filings(
                    ticker, client=client, limiter=limiter, user_agent=user_agent,
                ))
        if include_general:
            rows.extend(fetch_general_feeds(
                client=client, limiter=limiter, user_agent=user_agent,
            ))

    # Dedup by URL + title
    seen: set[str] = set()
    unique: list[SourceRow] = []
    for r in rows:
        k = r.dedup_key()
        if k in seen:
            continue
        seen.add(k)
        unique.append(r)

    log.info("stock.fetch_complete", total=len(rows), unique=len(unique), tickers=len(tickers))

    if fetch_content:
        with httpx.Client() as client:
            limiter2 = _RateLimited(default_delay=delay)
            enrich_with_content(unique, user_agent=user_agent, limiter=limiter2)

    return unique
