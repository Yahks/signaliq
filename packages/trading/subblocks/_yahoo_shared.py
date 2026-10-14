"""Shared helper for Yahoo-backed trading subblocks.

Wraps packages.ingestion.sources.market_cache.get_quote_cached with:
  - parallel-ish sequential fetching (rate-limited by the cache layer)
  - graceful per-symbol failure (empty entry, not raise)
  - consistent output shape for prompt rendering
"""
from __future__ import annotations

from typing import Any

from packages.core.logging import get_logger

log = get_logger(__name__)


async def fetch_quotes(symbols: list[str]) -> list[dict[str, Any]]:
    """Fetch quotes for the given symbols. Failures become None fields.

    Returns list of dicts with stable keys:
      symbol, price, change_pct, high_30d, low_30d, vol_30d, name
    """
    from packages.ingestion.sources.market_cache import get_quote_cached

    out: list[dict[str, Any]] = []
    for sym in symbols:
        try:
            q = await get_quote_cached(sym, force_refresh=False)
        except Exception as exc:
            log.warning("yahoo.fetch_failed", symbol=sym, error=str(exc)[:200])
            q = None

        if q is None:
            out.append({
                "symbol": sym, "price": None, "change_pct": None,
                "high_30d": None, "low_30d": None, "vol_30d": None, "name": None,
            })
            continue

        out.append({
            "symbol": sym,
            "name": getattr(q, "name", None),
            "price": getattr(q, "latest_price", None),
            "change_pct": getattr(q, "daily_change_pct", None),
            "high_30d": getattr(q, "high_30d", None),
            "low_30d": getattr(q, "low_30d", None),
            "vol_30d": getattr(q, "volatility_30d_pct", None),
        })

    ok = sum(1 for r in out if r["price"] is not None)
    log.info("yahoo.fetched", requested=len(symbols), ok=ok)
    return out


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def fmt_num(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"


async def _fetch_bars(symbol: str) -> list[dict[str, Any]] | None:
    """Fetch OHLC bars via market_data.fetch_bars()."""
    from packages.ingestion.sources.market_data import fetch_bars

    try:
        return await fetch_bars(symbol, range_="3mo", interval="1d")
    except Exception as exc:
        log.warning("technical.fetch_failed", symbol=symbol, error=str(exc)[:200])
        return None


def _compute_for_symbol(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """bars: list of {close, high, low} sorted oldest→newest."""
    if not bars or len(bars) < 30:
        return None
    closes = [b["close"] for b in bars if b.get("close") is not None]
    highs = [b["high"] for b in bars if b.get("high") is not None]
    lows = [b["low"] for b in bars if b.get("low") is not None]
    if len(closes) < 30 or len(highs) < 30 or len(lows) < 30:
        return None

    out: dict[str, Any] = {
        "last_close": closes[-1],
        "rsi_14": _rsi(closes),
        "sma_50": _sma(closes, 50),
        "sma_200": _sma(closes, 200),
        "atr_14": _atr(highs, lows, closes),
    }
    macd = _macd(closes)
    if macd:
        out["macd"], out["macd_signal"], out["macd_hist"] = macd
    return out


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _rsi(closes: list[float], window: int = 14) -> float | None:
    if len(closes) < window + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-window, 0):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains += delta
        else:
            losses += -delta
    avg_gain = gains / window
    avg_loss = losses / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(values: list[float], window: int) -> list[float]:
    """Standard EMA series. Returns list of same length as input."""
    if not values:
        return []
    k = 2.0 / (window + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _macd(closes: list[float]) -> tuple[float, float, float] | None:
    """Returns (macd_line, signal_line, histogram) for the last bar."""
    if len(closes) < 35:
        return None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema(macd_line, 9)
    return (macd_line[-1], signal[-1], macd_line[-1] - signal[-1])


def _atr(highs: list[float], lows: list[float], closes: list[float], window: int = 14) -> float | None:
    if len(closes) < window + 1:
        return None
    trs = []
    for i in range(-window, 0):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs) / len(trs)
