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


async def render_yearly_chart(
    symbol: str,
    *,
    years: int = 10,
    force: bool = False,
) -> str | None:
    """Render a yearly-close chart for `symbol`.

    Fetches monthly bars, resamples to one bar per calendar year,
    drops the current (incomplete) year, keeps the last `years`
    complete years, and renders a log-scale line chart.

    Default is 10 complete years — recent enough to be meaningful,
    long enough to show structure. Returns a URL like
    "/static/charts/AAPL-yearly-10yr-2026-10-15.png".

    Log scale because a decade of growth spans an order of
    magnitude. Returns None on any failure.
    """
    if not symbol:
        return None

    today = date.today().isoformat()
    safe = _safe_symbol(symbol)
    filename = f"{safe}-yearly-{years}yr-{today}.png"
    target = _CHART_DIR / filename

    if target.exists() and not force:
        log.info("chart.cache_hit", symbol=symbol, file=filename)
        return f"/static/charts/{filename}"

    from packages.ingestion.sources.market_data import fetch_bars
    try:
        bars = await fetch_bars(symbol, range_="max", interval="1mo")
    except Exception as exc:
        log.warning("chart.bars_failed", symbol=symbol, error=str(exc)[:200])
        return None
    if not bars:
        log.warning("chart.no_bars", symbol=symbol)
        return None

    yearly: dict[int, float] = {}
    for b in bars:
        try:
            y = int(str(b["date"])[:4])
            c = float(b["close"])
        except (KeyError, TypeError, ValueError):
            continue
        yearly[y] = c

    # Drop the current year — it hasn't closed. A yearly chart
    # shows only finished years.
    current_year = date.today().year
    yearly.pop(current_year, None)

    if len(yearly) < 3:
        log.warning("chart.yearly_too_few_years",
                    symbol=symbol, years=len(yearly))
        return None

    year_list = sorted(yearly.keys())[-years:]
    closes = [yearly[y] for y in year_list]

    try:
        _render_yearly_png(symbol, year_list, closes, target)
    except Exception as exc:
        log.warning("chart.render_failed", symbol=symbol, error=str(exc)[:200])
        return None

    log.info(
        "chart.yearly_rendered",
        symbol=symbol,
        years=len(year_list),
        file=filename,
        bytes=target.stat().st_size,
    )
    return f"/static/charts/{filename}"


def _render_yearly_png(
    symbol: str, years: list[int], closes: list[float], target: Path,
) -> None:
    """Render the yearly chart to a PNG file. Separated for testing."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=100)

    ax.plot(years, closes, color="#3b82f6", linewidth=1.6)
    ax.fill_between(
        years, closes, min(closes) * 0.9,
        color="#3b82f6", alpha=0.08,
    )

    ax.set_yscale("log")
    ax.grid(True, linestyle="--", linewidth=0.5, color="#e5e7eb")

    first = closes[0]
    last = closes[-1]
    pct = (last - first) / first * 100 if first else 0
    arrow = "\u25b2" if pct >= 0 else "\u25bc"
    color = "#16a34a" if pct >= 0 else "#dc2626"
    ax.set_title(
        f"{symbol}  \u00b7  yearly close  {years[0]}\u2013{years[-1]}  \u00b7  "
        f"{arrow} {pct:+.0f}%",
        fontsize=11,
        color=color,
        loc="left",
        pad=8,
    )

    ax.set_ylabel("Price (log)", fontsize=9, color="#6b7280")
    ax.tick_params(axis="both", labelsize=8, colors="#6b7280")

    ax.annotate(
        f"{last:.2f}",
        xy=(years[-1], last),
        xytext=(6, 0), textcoords="offset points",
        fontsize=9, color="#111827", va="center", fontweight="bold",
    )

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#d1d5db")

    fig.tight_layout()

    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, format="png", bbox_inches="tight")
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────
# Stock chart — reads market_history, renders one (symbol, timeframe).
# ─────────────────────────────────────────────────────────────────────

_STOCK_CHART_WINDOW = {
    "1y":  (10,  "log"),
    "1mo": (60,  "log"),
    "1wk": (52,  "linear"),
    "1d":  (60,  "linear"),
    "1h":  (100, "linear"),
}


async def render_stock_chart(
    session,
    *,
    org_id: str,
    symbol: str,
    timeframe: str,
    force: bool = False,
) -> str | None:
    """Render a stock chart for one (symbol, timeframe).

    Reads from market_history. Returns a URL like
    "/static/charts/DASH-1d-2026-10-15.png", or None on failure.
    """
    if not symbol or not timeframe:
        return None

    from sqlalchemy import text as sql_text

    today = date.today().isoformat()
    safe = _safe_symbol(symbol)
    filename = f"{safe}-{timeframe}-{today}.png"
    target = _CHART_DIR / filename

    if target.exists() and not force:
        log.info("chart.cache_hit", symbol=symbol,
                 timeframe=timeframe, file=filename)
        return f"/static/charts/{filename}"

    n_bars, scale = _STOCK_CHART_WINDOW.get(timeframe, (60, "linear"))

    try:
        rows = (await session.execute(sql_text("""
            SELECT trade_date, close
            FROM market_history
            WHERE org_id = :oid
              AND symbol = :sym
              AND timeframe = :tf
              AND deleted_at IS NULL
            ORDER BY trade_date DESC
            LIMIT :lim
        """), {
            "oid": org_id, "sym": symbol,
            "tf": timeframe, "lim": n_bars,
        })).mappings().all()
    except Exception as exc:
        log.warning("chart.history_failed",
                    symbol=symbol, timeframe=timeframe,
                    error=str(exc)[:200])
        return None

    if len(rows) < 3:
        log.warning("chart.history_too_few",
                    symbol=symbol, timeframe=timeframe, rows=len(rows))
        return None

    rows = list(reversed(rows))
    dates = [r["trade_date"] for r in rows]
    closes = [float(r["close"]) for r in rows]

    try:
        _render_stock_png(symbol, timeframe, dates, closes, scale, target)
    except Exception as exc:
        log.warning("chart.render_failed",
                    symbol=symbol, timeframe=timeframe,
                    error=str(exc)[:200])
        return None

    log.info(
        "chart.stock_rendered",
        symbol=symbol, timeframe=timeframe,
        bars=len(rows), file=filename,
        bytes=target.stat().st_size,
    )
    return f"/static/charts/{filename}"


def _render_stock_png(
    symbol: str, timeframe: str,
    dates: list, closes: list[float],
    scale: str, target: Path,
) -> None:
    """Render a stock chart PNG. Separated for testing."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from datetime import datetime

    dts = []
    for d in dates:
        if isinstance(d, datetime):
            dts.append(d)
        elif hasattr(d, "year"):
            dts.append(datetime(d.year, d.month, d.day))
        else:
            try:
                dts.append(datetime.fromisoformat(str(d)))
            except Exception:
                dts.append(None)

    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=100)

    ax.plot(dts, closes, color="#3b82f6", linewidth=1.6)
    floor = min(closes) * (0.9 if scale == "log" else 1.0)
    ax.fill_between(dts, closes, floor, color="#3b82f6", alpha=0.08)

    if scale == "log":
        ax.set_yscale("log")

    ax.grid(True, linestyle="--", linewidth=0.5, color="#e5e7eb")

    first = closes[0]
    last = closes[-1]
    pct = (last - first) / first * 100 if first else 0
    arrow = "\u25b2" if pct >= 0 else "\u25bc"
    color = "#16a34a" if pct >= 0 else "#dc2626"
    tf_label = {
        "1y": "10-year", "1mo": "5-year monthly",
        "1wk": "1-year weekly", "1d": "60-day daily",
        "1h": "intraday",
    }.get(timeframe, timeframe)
    ax.set_title(
        f"{symbol}  \u00b7  {tf_label}  \u00b7  {arrow} {pct:+.2f}%",
        fontsize=11, color=color, loc="left", pad=8,
    )

    ax.set_ylabel("Price (log)" if scale == "log" else "Price",
                  fontsize=9, color="#6b7280")

    if timeframe == "1h":
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    elif timeframe in ("1d", "1wk"):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    ax.tick_params(axis="both", labelsize=8, colors="#6b7280")

    ax.annotate(
        f"{last:.2f}",
        xy=(dts[-1], last), xytext=(6, 0), textcoords="offset points",
        fontsize=9, color="#111827", va="center", fontweight="bold",
    )

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#d1d5db")
    ax.spines["bottom"].set_color("#d1d5db")

    fig.tight_layout()
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, format="png", bbox_inches="tight")
    plt.close(fig)
