"""News fetcher for Studio and news-adjacent subblocks.

Two sources, both free and unauthenticated:

  - Google News RSS  — aggregates thousands of outlets for a query.
                       Returns title, source, published time, link.
  - Reddit JSON      — public subreddit search API. Returns the
                       discussion titles + scores for retail sentiment.

Given a ticker or a company name, fetch_news returns a small set of
headlines from the last 24-48 hours. No ranking, no summarization —
the model reads the headlines and picks what matters.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from packages.core.logging import get_logger

log = get_logger(__name__)


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
REDDIT_SEARCH = "https://www.reddit.com/r/{sub}/search.json"

# Subreddits that carry retail-facing discussion for stocks and crypto.
REDDIT_SUBS = ["wallstreetbets", "stocks", "investing", "CryptoCurrency"]

# Identify ourselves to both services. Both require a UA.
UA = "SignalIQ/0.1 (+contact: ops@signaliq.local)"

# Only keep headlines younger than this.
MAX_AGE_HOURS = 48


@dataclass
class Headline:
    title: str
    source: str          # "Reuters", "CNBC", "r/wallstreetbets"
    url: str
    published_at: datetime | None = None

    def age_hours(self) -> float | None:
        if self.published_at is None:
            return None
        delta = datetime.now(timezone.utc) - self.published_at
        return delta.total_seconds() / 3600


def _parse_rfc822(s: str) -> datetime | None:
    """Parse RFC 822 dates from RSS feeds."""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


async def fetch_google_news(query: str, *, limit: int = 8) -> list[Headline]:
    """Query Google News RSS for a term. Returns recent headlines."""
    if not query:
        return []
    params = {
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                GOOGLE_NEWS_RSS, params=params, headers={"User-Agent": UA},
            )
            r.raise_for_status()
    except Exception as exc:
        log.warning("news.google_failed", query=query, error=str(exc)[:200])
        return []

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as exc:
        log.warning("news.google_parse_failed", error=str(exc)[:200])
        return []

    out: list[Headline] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        # Google News embeds the source in the title as "Title - Source".
        source = ""
        if " - " in title:
            title, source = title.rsplit(" - ", 1)
        title = html.unescape(title).strip()
        source = source.strip()

        h = Headline(
            title=title,
            source=source or "Google News",
            url=link,
            published_at=_parse_rfc822(pub),
        )
        # Drop old headlines.
        age = h.age_hours()
        if age is not None and age > MAX_AGE_HOURS:
            continue
        out.append(h)
        if len(out) >= limit:
            break

    log.info("news.google_ok", query=query, count=len(out))
    return out


async def fetch_reddit(query: str, *, limit: int = 6) -> list[Headline]:
    """Search retail subreddits for recent discussion of a ticker/term.

    Reads the JSON search endpoint. Public; no key. Requires a UA.
    """
    if not query:
        return []
    out: list[Headline] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for sub in REDDIT_SUBS:
            url = REDDIT_SEARCH.format(sub=sub)
            params = {
                "q": query,
                "restrict_sr": 1,
                "sort": "new",
                "t": "day",
                "limit": limit,
            }
            try:
                r = await client.get(
                    url, params=params, headers={"User-Agent": UA},
                )
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                log.warning("news.reddit_failed", sub=sub, error=str(exc)[:120])
                continue

            for child in (data.get("data") or {}).get("children") or []:
                d = child.get("data") or {}
                title = (d.get("title") or "").strip()
                if not title:
                    continue
                created = d.get("created_utc")
                published = (
                    datetime.fromtimestamp(created, tz=timezone.utc)
                    if created else None
                )
                permalink = d.get("permalink") or ""
                out.append(Headline(
                    title=html.unescape(title),
                    source=f"r/{sub}",
                    url=f"https://www.reddit.com{permalink}" if permalink else "",
                    published_at=published,
                ))
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break

    log.info("news.reddit_ok", query=query, count=len(out))
    return out


async def fetch_news(query: str, *, ticker: str | None = None) -> list[Headline]:
    """Best-effort news for a query. Merges Google News + Reddit.

    The query should be a human term ("Tesla") or a ticker ("TSLA").
    If both are known, pass ticker separately so the Google query can
    include it (e.g. "TSLA stock").
    """
    google_q = query
    if ticker and ticker.upper() not in query.upper():
        google_q = f"{query} {ticker} stock"

    google = await fetch_google_news(google_q, limit=8)
    reddit = await fetch_reddit(query or ticker or "", limit=4)

    # Deduplicate by normalized title.
    seen = set()
    merged: list[Headline] = []
    for h in google + reddit:
        key = re.sub(r"\W+", "", h.title.lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        merged.append(h)

    return merged[:10]


def render_headlines_md(headlines: list[Headline]) -> str:
    """Render headlines as a markdown block for the prompt."""
    if not headlines:
        return ""
    lines = ["Recent headlines (use these as sources; cite them by name):"]
    for h in headlines:
        age = h.age_hours()
        age_str = f"{age:.0f}h ago" if age is not None else "recent"
        lines.append(f"- [{h.source}, {age_str}] {h.title}")
    return "\n".join(lines)
