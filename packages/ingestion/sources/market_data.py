"""Yahoo Finance market data fetcher.

Fetches OHLCV + metadata for a symbol, computes derived metrics:
  - latest price
  - daily change (absolute + pct)
  - 30-day realized volatility (daily log returns std)
  - 30-day high / low

Symbols accepted by Yahoo:
  - Equities:      AAPL, MSFT, TSLA
  - Indices:       ^GSPC (S&P 500), ^IXIC (Nasdaq), ^VIX
  - Commodities:   NG=F (natural gas), CL=F (crude), GC=F (gold)
  - FX:            EURUSD=X, USDCNY=X
  - Crypto:        BTC-USD, ETH-USD

Uses the public chart API. No auth required. Be polite:
  - 15 min Redis cache (managed by caller)
  - 1.5s rate limit between distinct symbols
  - Compliance: this is a public read-only endpoint (see robots.txt policy)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from packages.core.logging import get_logger

log = get_logger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

DEFAULT_TIMEOUT = 15.0
DEFAULT_RANGE = "1mo"
DEFAULT_INTERVAL = "1d"


@dataclass
class Quote:
    symbol: str
    name: str | None
    currency: str | None
    latest_price: float | None
    previous_close: float | None
    daily_change_abs: float | None
    daily_change_pct: float | None
    high_30d: float | None
    low_30d: float | None
    volatility_30d_pct: float | None  # daily std of log returns, in %
    as_of: str  # ISO timestamp
    raw_days: int
    extra: dict[str, Any] = field(default_factory=dict)


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compute_volatility_pct(closes: list[float]) -> float | None:
    """Daily volatility = std of log returns, expressed as %.

    Uses N-1 denominator (sample std). Returns None if < 3 data points.
    """
    if len(closes) < 3:
        return None
    log_rets: list[float] = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a <= 0 or b <= 0:
            continue
        log_rets.append(math.log(b / a))
    if len(log_rets) < 2:
        return None
    mean = sum(log_rets) / len(log_rets)
    var = sum((r - mean) ** 2 for r in log_rets) / (len(log_rets) - 1)
    return math.sqrt(var) * 100.0


async def fetch_quote(symbol: str, *, range_: str = DEFAULT_RANGE) -> Quote | None:
    """Fetch one symbol's quote + derived metrics. Returns None on failure."""
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"range": range_, "interval": DEFAULT_INTERVAL}
    headers = {
        # Yahoo blocks empty UAs. Identify ourselves — same practice as SEC.
        "User-Agent": "SignalIQ/0.1 (contact@yoursite.com)",
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            r = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("market.fetch_failed", symbol=symbol, error=str(exc)[:200])
        return None

    if r.status_code != 200:
        log.warning("market.http_error", symbol=symbol, status=r.status_code)
        return None

    try:
        data = r.json()
    except ValueError:
        log.warning("market.json_error", symbol=symbol)
        return None

    chart = (data.get("chart") or {}).get("result") or []
    if not chart:
        log.info("market.no_result", symbol=symbol)
        return None
    result = chart[0]

    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes_raw = quotes.get("close") or []

    # Filter None closes
    closes: list[float] = []
    for v in closes_raw:
        f = _safe_float(v)
        if f is not None:
            closes.append(f)

    latest = _safe_float(meta.get("regularMarketPrice"))

    # S.8.6.2 — use Yahoo's own daily change figures.
    #
    # The chart response's `meta` block contains:
    #   regularMarketPrice          — current price
    #   regularMarketChangePercent  — today's % change (correct)
    #   regularMarketChange         — today's absolute change
    #   fulldayChangePercent        — same as regularMarketChangePercent
    #   chartPreviousClose          — close BEFORE the range start
    #                                 (1 month ago for range=1mo)
    #
    # There is NO `previousClose` field in this response, so trying
    # to compute change from chartPreviousClose gives a 1-month
    # change with a daily label. That was the S.8.6.1 bug.
    #
    # We now read the change fields directly. Compute from closes
    # only as a fallback if Yahoo omits them (rare).

    # S.8.9.8 — read Yahoo's change fields, but reject implausible
    # values. Observed: Yahoo returns regularMarketChangePercent =
    # -48.061 for ^VIX with previousClose=None, on every range. The
    # underlying chartPreviousClose is fine (yesterday's close), and
    # the price is fine; only the pre-computed change field is
    # corrupt. So we:
    #   1. Read regularMarketChangePercent when present and plausible.
    #   2. Otherwise compute from the last two daily closes.
    #   3. Reject anything |change| > 25 as garbage and fall through.
    daily_pct = _safe_float(meta.get("regularMarketChangePercent"))
    if daily_pct is None:
        daily_pct = _safe_float(meta.get("fulldayChangePercent"))

    daily_abs = _safe_float(meta.get("regularMarketChange"))
    if daily_abs is None:
        daily_abs = _safe_float(meta.get("fulldayChange"))

    # Reject implausible values from Yahoo.
    if daily_pct is not None and abs(daily_pct) > 25:
        log.warning(
            "market.implausible_change_pct",
            symbol=symbol,
            pct=daily_pct,
            latest=latest,
            meta_prev=meta.get("previousClose"),
        )
        daily_pct = None
        daily_abs = None

    # If meta didn't have a price, take the last close.
    if latest is None and closes:
        latest = closes[-1]

    # Fallback: compute from the last two daily closes.
    if daily_pct is None and len(closes) >= 2 and latest is not None:
        prev = closes[-2]
        if prev:
            daily_abs = latest - prev
            daily_pct = (daily_abs / prev) * 100.0

    # previous_close is informational; set from closes[-2] if we
    # can, else None.
    prev_close = closes[-2] if len(closes) >= 2 else None

    high_30d = max(closes) if closes else None
    low_30d = min(closes) if closes else None

    vol_pct = _compute_volatility_pct(closes)

    return Quote(
        symbol=symbol,
        name=meta.get("shortName") or meta.get("longName"),
        currency=meta.get("currency"),
        latest_price=latest,
        previous_close=prev_close,
        daily_change_abs=daily_abs,
        daily_change_pct=daily_pct,
        high_30d=high_30d,
        low_30d=low_30d,
        volatility_30d_pct=vol_pct,
        as_of=datetime.utcnow().isoformat() + "Z",
        raw_days=len(closes),
        extra={
            "exchange": meta.get("exchangeName"),
            "instrument_type": meta.get("instrumentType"),
            "first_trade_date": meta.get("firstTradeDate"),
        },
    )


async def validate_symbol(symbol: str) -> bool:
    """Cheap validation — does Yahoo return data for this symbol?"""
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"range": "5d", "interval": "1d"}
    headers = {"User-Agent": "SignalIQ/0.1 (contact@yoursite.com)"}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            r = await client.get(url, params=params, headers=headers)
        if r.status_code != 200:
            return False
        data = r.json()
        result = ((data.get("chart") or {}).get("result") or [])
        return bool(result)
    except Exception:
        return False


async def fetch_many(
    symbols: list[str],
    *,
    delay_seconds: float = 1.5,
    range_: str = DEFAULT_RANGE,
) -> dict[str, Quote]:
    """Fetch multiple symbols sequentially with rate limiting.

    Returns dict[symbol -> Quote] for symbols that succeeded.
    """
    results: dict[str, Quote] = {}
    last_fetch = 0.0

    for sym in symbols:
        elapsed = time.monotonic() - last_fetch
        if last_fetch > 0 and elapsed < delay_seconds:
            import asyncio
            await asyncio.sleep(delay_seconds - elapsed)
        last_fetch = time.monotonic()

        q = await fetch_quote(sym, range_=range_)
        if q is not None:
            results[sym] = q
    return results


# ─────────────────────────────────────────────────────────────────────
# Raw OHLC bars (for indicator computation)
# ─────────────────────────────────────────────────────────────────────

async def fetch_bars(
    symbol: str,
    *,
    range_: str = "3mo",
    interval: str = "1d",
) -> list[dict[str, Any]] | None:
    """Fetch raw OHLC bars for a symbol.

    Returns list of dicts: {date, open, high, low, close, volume}
    sorted oldest -> newest. Returns None on any failure.

    Uses the same Yahoo chart endpoint as fetch_quote.
    """
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"range": range_, "interval": interval}

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                url, params=params,
                headers={"User-Agent": "SignalIQ/0.1 (+contact: ops@signaliq.local)"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("market_data.bars_fetch_failed", symbol=symbol, error=str(exc)[:200])
        return None

    try:
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators") or {}).get("quote") or [{}]
        q0 = quote[0]

        opens = q0.get("open") or []
        highs = q0.get("high") or []
        lows = q0.get("low") or []
        closes = q0.get("close") or []
        volumes = q0.get("volume") or []

        bars: list[dict[str, Any]] = []
        for i, ts in enumerate(timestamps):
            try:
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                lo = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                v = volumes[i] if i < len(volumes) else None
                # Skip incomplete bars
                if c is None or h is None or lo is None:
                    continue
                bars.append({
                    "date": datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(),
                    "open": _safe_float(o),
                    "high": _safe_float(h),
                    "low": _safe_float(lo),
                    "close": _safe_float(c),
                    "volume": int(v) if v is not None else None,
                })
            except (IndexError, TypeError, ValueError):
                continue

        log.info("market_data.bars_fetched", symbol=symbol, count=len(bars))
        return bars if bars else None
    except Exception as exc:
        log.warning("market_data.bars_parse_failed", symbol=symbol, error=str(exc)[:200])
        return None
