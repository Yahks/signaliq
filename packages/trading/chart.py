"""Price-chart generator for trading articles.

Fetches OHLC bars for a ticker and renders a 30-day line chart
PNG. The chart is written to a static directory served by the API
under /static/charts/.

Design:
  - matplotlib (Agg backend, no display) is the only dependency.
  - One chart per ticker per day. Cached by filename — a second
    generation of the same chart for the same day reuses the file.
  - Filenames: {ticker}-{YYYY-MM-DD}.png
  - Tickers are sanitized (^GSPC, BRK.B, BTC-USD, etc.) into
    filesystem-safe names.

The rendered chart is intended to be inserted into a news-adjacent
article's markdown body as:

  ![<ticker> 30-day price](/static/charts/<ticker>-<date>.png)
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from packages.core.logging import get_logger

log = get_logger(__name__)


# Where the PNGs live. Served by FastAPI as a StaticFiles mount.
# Matches apps/api/main.py's mount path.
_CHART_DIR = Path("apps/api/static/charts")

# 30 calendar days → ~20 trading bars for a typical equity. Good
# default for a movers article.
_DEFAULT_DAYS = 30


def _safe_symbol(symbol: str) -> str:
    """Map a Yahoo ticker into a filesystem-safe name.

    ^GSPC   -> GSPC
    BRK.B   -> BRK-B
    BTC-USD -> BTC-USD  (already safe)
    """
    s = symbol.strip().lstrip("^")
    s = s.replace(".", "-")
    s = re.sub(r"[^A-Za-z0-9\-_]", "", s)
    return s or "UNKNOWN"


async def render_price_chart(
    symbol: str,
    *,
    days: int = _DEFAULT_DAYS,
    force: bool = False,
) -> str | None:
    """Render a price chart for `symbol`. Return the URL path.

    The PNG is written to _CHART_DIR. Returns a relative URL like
    "/static/charts/AAPL-2026-09-29.png". Returns None on any
    failure (missing bars, render error) — the caller should not
    insert an image tag in that case.
    """
    if not symbol:
        return None

    today = date.today().isoformat()
    safe = _safe_symbol(symbol)
    filename = f"{safe}-{today}.png"
    target = _CHART_DIR / filename

    # Cached — same chart for the same ticker on the same day.
    if target.exists() and not force:
        log.info("chart.cache_hit", symbol=symbol, file=filename)
        return f"/static/charts/{filename}"

    # Fetch bars.
    from packages.ingestion.sources.market_data import fetch_bars
    try:
        bars = await fetch_bars(symbol, range_=f"{days}d", interval="1d")
    except Exception as exc:
        log.warning("chart.bars_failed", symbol=symbol, error=str(exc)[:200])
        return None
    if not bars:
        log.warning("chart.no_bars", symbol=symbol)
        return None

    # Render.
    try:
        _render_png(symbol, bars, target)
    except Exception as exc:
        log.warning("chart.render_failed", symbol=symbol, error=str(exc)[:200])
        return None

    log.info(
        "chart.rendered",
        symbol=symbol,
        bars=len(bars),
        file=filename,
        bytes=target.stat().st_size,
    )
    return f"/static/charts/{filename}"


def _render_png(symbol: str, bars: list[dict], target: Path) -> None:
    """Render the chart to a PNG file. Separated for testing."""
    # matplotlib is heavy to import — do it lazily so the rest of
    # the package doesn't pay for it.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from datetime import datetime

    dates = [datetime.fromisoformat(b["date"]) for b in bars]
    closes = [float(b["close"]) for b in bars]

    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=100)

    # Line + fill for a clean look.
    ax.plot(dates, closes, color="#3b82f6", linewidth=1.6)
    ax.fill_between(
        dates, closes, min(closes),
        color="#3b82f6", alpha=0.08,
    )

    # Grid.
    ax.grid(True, linestyle="--", linewidth=0.5, color="#e5e7eb")

    # Title with price change.
    first = closes[0]
    last = closes[-1]
    pct = (last - first) / first * 100 if first else 0
    arrow = "▲" if pct >= 0 else "▼"
    color = "#16a34a" if pct >= 0 else "#dc2626"
    ax.set_title(
        f"{symbol}  ·  last 30 days  ·  {arrow} {pct:+.2f}%",
        fontsize=11,
        color=color,
        loc="left",
        pad=8,
    )

    # Axis labels.
    ax.set_ylabel("Price", fontsize=9, color="#6b7280")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.tick_params(axis="both", labelsize=8, colors="#6b7280")

    # Minimal spines.
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#d1d5db")

    fig.tight_layout()

    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, format="png", bbox_inches="tight")
    plt.close(fig)
