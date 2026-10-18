"""Article charts: distribution, setup-history, timeframe returns,
correlation. Each reads from market_history and produces one PNG
in the same directory the price chart uses.

Caller: market_analysis.generate() after the price chart.

Every renderer returns a URL string or None on failure. Failures
are logged but never raised — a missing chart is not fatal.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

_CHART_DIR = Path("apps/api/static/charts")


def _safe_symbol(symbol: str) -> str:
    import re
    s = symbol.strip().lstrip("^")
    s = s.replace(".", "-")
    s = re.sub(r"[^A-Za-z0-9\-_]", "", s)
    return s or "UNKNOWN"


def _to_dt(d: Any) -> datetime | None:
    if isinstance(d, datetime):
        return d
    if hasattr(d, "year"):
        return datetime(d.year, d.month, d.day)
    try:
        return datetime.fromisoformat(str(d))
    except Exception:
        return None


async def _read_closes(
    session: AsyncSession, *,
    org_id: str, symbol: str, timeframe: str = "1d",
    limit: int | None = None,
) -> list[tuple[date, float]]:
    """Return the (date, close) series, oldest first."""
    sql = (
        "SELECT trade_date, close FROM market_history "
        "WHERE org_id = :oid AND symbol = :sym AND timeframe = :tf "
        "AND deleted_at IS NULL "
        "ORDER BY trade_date DESC"
    )
    params: dict = {"oid": org_id, "sym": symbol, "tf": timeframe}
    if limit:
        sql += " LIMIT :lim"
        params["lim"] = limit
    rows = (await session.execute(sql_text(sql), params)).mappings().all()
    return [(r["trade_date"], float(r["close"])) for r in reversed(rows)]


async def render_distribution_chart(
    session: AsyncSession, *,
    org_id: str,
    symbol: str,
    force: bool = False,
) -> str | None:
    """Histogram of the last 90 daily returns, colored by sign."""
    today = date.today().isoformat()
    safe = _safe_symbol(symbol)
    filename = f"{safe}-distribution-{today}.png"
    target = _CHART_DIR / filename

    if target.exists() and not force:
        return f"/static/charts/{filename}"

    series = await _read_closes(
        session, org_id=org_id, symbol=symbol, timeframe="1d", limit=91,
    )
    if len(series) < 21:
        return None

    closes = [c for _, c in series]
    rets = [
        (closes[k] - closes[k - 1]) / closes[k - 1] * 100.0
        for k in range(1, len(closes))
        if closes[k - 1]
    ]
    if not rets:
        return None

    ups = sum(1 for r in rets if r > 0)
    downs = sum(1 for r in rets if r < 0)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=100)
    n, bins, patches = ax.hist(rets, bins=20, edgecolor="none")
    for patch, left_edge in zip(patches, bins[:-1]):
        patch.set_facecolor("#16a34a" if left_edge >= 0 else "#dc2626")
        patch.set_alpha(0.75)

    ax.axvline(0, color="#374151", linewidth=0.7, linestyle="--")
    ax.grid(True, linestyle="--", linewidth=0.5, color="#e5e7eb", axis="y")
    ax.set_axisbelow(True)
    ax.set_xlabel("Daily return (%)", fontsize=9, color="#6b7280")
    ax.set_ylabel("Count of days", fontsize=9, color="#6b7280")
    ax.set_title(
        f"{symbol}  \u00b7  90-day move distribution  "
        f"(up {ups}, down {downs})",
        fontsize=11, loc="left", pad=8, color="#111827",
    )
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, format="png", bbox_inches="tight")
    plt.close(fig)

    log.info("chart.distribution_rendered",
             symbol=symbol, days=len(rets), file=filename)
    return f"/static/charts/{filename}"


async def render_setup_history_chart(
    session: AsyncSession, *,
    org_id: str,
    symbol: str,
    timeframe: str = "1d",
    force: bool = False,
) -> str | None:
    """Forward returns at 5 and 10 days for the last 3 setup instances."""
    today = date.today().isoformat()
    safe = _safe_symbol(symbol)
    tf_key = timeframe if timeframe == "1d" else "1d"
    filename = f"{safe}-setuphistory-{today}.png"
    target = _CHART_DIR / filename

    if target.exists() and not force:
        return f"/static/charts/{filename}"

    # The setup-history data comes from the daily series regardless
    # of the article's timeframe.
    series = await _read_closes(
        session, org_id=org_id, symbol=symbol, timeframe=tf_key,
    )
    closes = [c for _, c in series]
    if len(closes) < 60:
        return None

    # Recompute the current percentile band using the same logic as
    # the report so the chart matches the article.
    current = closes[-1]
    closes_sorted = sorted(closes)
    below = sum(1 for c in closes_sorted if c <= current)
    cur_pct = int(round(below / len(closes_sorted) * 100))
    band_lo = max(0, cur_pct - 5)
    band_hi = min(100, cur_pct + 5)

    instances: list[tuple[date, float, float]] = []
    for k in range(len(closes) - 16, 20, -1):
        window = closes[:k + 1]
        w_sorted = sorted(window)
        c_below = sum(1 for c in w_sorted if c <= window[-1])
        pct_k = int(round(c_below / len(w_sorted) * 100))
        if band_lo <= pct_k <= band_hi:
            base = closes[k]
            f5 = None
            f10 = None
            if base and k + 5 < len(closes):
                f5 = (closes[k + 5] - base) / base * 100.0
            if base and k + 10 < len(closes):
                f10 = (closes[k + 10] - base) / base * 100.0
            if f5 is not None and f10 is not None:
                d = series[k][0]
                instances.append((d, f5, f10))
            if len(instances) >= 3:
                break

    if not instances:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    labels = [d.strftime("%Y-%m-%d") for d, _, _ in instances]
    f5_vals = [v[1] for v in instances]
    f10_vals = [v[2] for v in instances]

    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=100)
    x = np.arange(len(labels))
    w = 0.36
    bars5 = ax.bar(x - w/2, f5_vals, w, label="+5 days")
    bars10 = ax.bar(x + w/2, f10_vals, w, label="+10 days")
    for b in bars5:
        b.set_color("#16a34a" if b.get_height() >= 0 else "#dc2626")
        b.set_alpha(0.75)
    for b in bars10:
        b.set_color("#16a34a" if b.get_height() >= 0 else "#dc2626")
        b.set_alpha(0.55)

    ax.axhline(0, color="#374151", linewidth=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=0, fontsize=8)
    ax.set_ylabel("Forward return (%)", fontsize=9, color="#6b7280")
    ax.grid(True, linestyle="--", linewidth=0.5, color="#e5e7eb", axis="y")
    ax.set_axisbelow(True)
    ax.set_title(
        f"{symbol}  \u00b7  what happened the last "
        f"{len(instances)} times in the {band_lo}-{band_hi} band",
        fontsize=11, loc="left", pad=8, color="#111827",
    )
    ax.legend(fontsize=8, loc="upper right", frameon=False)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, format="png", bbox_inches="tight")
    plt.close(fig)

    log.info("chart.setup_history_rendered",
             symbol=symbol, instances=len(instances), file=filename)
    return f"/static/charts/{filename}"


async def render_timeframe_returns_chart(
    session: AsyncSession, *,
    org_id: str,
    symbol: str,
    force: bool = False,
) -> str | None:
    """Horizontal bar chart of returns across 20d, 12w, 6m, 5y."""
    today = date.today().isoformat()
    safe = _safe_symbol(symbol)
    filename = f"{safe}-timeframes-{today}.png"
    target = _CHART_DIR / filename

    if target.exists() and not force:
        return f"/static/charts/{filename}"

    # Read the four series.
    series_by_tf: dict[str, list[float]] = {}
    for tf in ("1d", "1wk", "1mo", "1y"):
        s = await _read_closes(
            session, org_id=org_id, symbol=symbol, timeframe=tf,
        )
        if len(s) >= 2:
            series_by_tf[tf] = [c for _, c in s]

    windows = [
        ("20-day", "1d", 20),
        ("12-week", "1wk", 12),
        ("6-month", "1mo", 6),
        ("5-year", "1y", 5),
    ]
    labels: list[str] = []
    values: list[float] = []
    for label, tf, n in windows:
        s = series_by_tf.get(tf) or []
        if len(s) >= n + 1 and s[-n - 1]:
            r = (s[-1] - s[-n - 1]) / s[-n - 1] * 100.0
            labels.append(label)
            values.append(r)
        else:
            labels.append(label)
            values.append(0.0)

    if not any(v != 0.0 for v in values):
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 3.0), dpi=100)
    colors = ["#16a34a" if v >= 0 else "#dc2626" for v in values]
    y = list(range(len(labels)))
    ax.barh(y, values, color=colors, alpha=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.axvline(0, color="#374151", linewidth=0.7)
    ax.set_xlabel("Return (%)", fontsize=9, color="#6b7280")
    ax.grid(True, linestyle="--", linewidth=0.5, color="#e5e7eb", axis="x")
    ax.set_axisbelow(True)
    ax.set_title(
        f"{symbol}  \u00b7  returns across windows",
        fontsize=11, loc="left", pad=8, color="#111827",
    )
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for yi, v in zip(y, values):
        sign = "+" if v >= 0 else ""
        ax.text(
            v + (0.1 if v >= 0 else -0.1), yi, f"{sign}{v:.2f}%",
            va="center", ha="left" if v >= 0 else "right",
            fontsize=8, color="#111827",
        )
    fig.tight_layout()
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, format="png", bbox_inches="tight")
    plt.close(fig)

    log.info("chart.timeframes_rendered",
             symbol=symbol, file=filename)
    return f"/static/charts/{filename}"


async def render_correlation_chart(
    session: AsyncSession, *,
    org_id: str,
    symbol: str,
    force: bool = False,
) -> str | None:
    """Rolling 20-day correlation of the stock vs SPY, last 90 days."""
    today = date.today().isoformat()
    safe = _safe_symbol(symbol)
    filename = f"{safe}-correlation-{today}.png"
    target = _CHART_DIR / filename

    if target.exists() and not force:
        return f"/static/charts/{filename}"

    stock = await _read_closes(
        session, org_id=org_id, symbol=symbol, timeframe="1d", limit=120,
    )
    spy = await _read_closes(
        session, org_id=org_id, symbol="SPY", timeframe="1d", limit=120,
    )
    if len(stock) < 40 or len(spy) < 40:
        return None

    stock_by_d = {d: c for d, c in stock}
    spy_by_d = {d: c for d, c in spy}
    common = sorted(set(stock_by_d) & set(spy_by_d))
    if len(common) < 40:
        return None

    stock_closes = [stock_by_d[d] for d in common]
    spy_closes = [spy_by_d[d] for d in common]

    sr = [
        (stock_closes[k] - stock_closes[k - 1]) / stock_closes[k - 1]
        if stock_closes[k - 1] else 0.0
        for k in range(1, len(stock_closes))
    ]
    pr = [
        (spy_closes[k] - spy_closes[k - 1]) / spy_closes[k - 1]
        if spy_closes[k - 1] else 0.0
        for k in range(1, len(spy_closes))
    ]
    window = 20
    corr_series = []
    corr_dates = []
    for k in range(window, len(sr) + 1):
        a = sr[k - window:k]
        b = pr[k - window:k]
        ms = sum(a) / window
        mp = sum(b) / window
        cov = sum((a[i] - ms) * (b[i] - mp) for i in range(window))
        vs = sum((a[i] - ms) ** 2 for i in range(window))
        vp = sum((b[i] - mp) ** 2 for i in range(window))
        denom = math.sqrt(vs * vp)
        if denom:
            corr_series.append(cov / denom)
            corr_dates.append(common[k])

    if len(corr_series) < 10:
        return None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    dts = [_to_dt(d) for d in corr_dates]

    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=100)
    ax.axhspan(-0.5, 0.5, color="#f3f4f6", alpha=0.6)
    ax.axhline(0, color="#374151", linewidth=0.7, linestyle="--")
    ax.plot(dts, corr_series, color="#3b82f6", linewidth=1.6)
    ax.set_ylim(-1.05, 1.05)
    ax.grid(True, linestyle="--", linewidth=0.5, color="#e5e7eb")
    ax.set_axisbelow(True)
    ax.set_ylabel("Correlation", fontsize=9, color="#6b7280")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.set_title(
        f"{symbol} vs broad index  \u00b7  20-day rolling correlation",
        fontsize=11, loc="left", pad=8, color="#111827",
    )
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, format="png", bbox_inches="tight")
    plt.close(fig)

    log.info("chart.correlation_rendered",
             symbol=symbol, points=len(corr_series), file=filename)
    return f"/static/charts/{filename}"
