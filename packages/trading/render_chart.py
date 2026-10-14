"""Render the equity curve as a PNG.

Server-side rendering with matplotlib. Every chart is
byte-identical for the same data, cacheable, and independent of
the browser.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

STARTING_BALANCE = 200_000.0
OUTPUT_DIR = Path("apps/web/public/charts")


async def render_equity_chart(
    session: AsyncSession,
    org_id: str,
    *,
    width_in: float = 10.0,
    height_in: float = 3.6,
    dpi: int = 200,
) -> Path:
    """Render the equity curve for one org. Returns the PNG path.

    The file is written to apps/web/public/charts/<org_id>.png,
    overwriting any previous version. Next.js serves it directly.
    """
    rows = (await session.execute(sql_text("""
        SELECT traded_at, result
        FROM trade_log
        WHERE org_id = :oid
          AND source = 'synthetic'
          AND deleted_at IS NULL
        ORDER BY traded_at ASC, id ASC
    """), {"oid": org_id})).all()

    if not rows:
        raise ValueError("no trades for org")

    dates = []
    equity = []
    balances = []
    running = STARTING_BALANCE
    for r in rows:
        running += float(r[1] or 0)
        dates.append(r[0])
        balances.append(running)
        equity.append(running)

    # Colors: green for up moves, red for down moves.
    colors = []
    prev = STARTING_BALANCE
    for b in balances:
        if b > prev:
            colors.append("#16a34a")
        elif b < prev:
            colors.append("#dc2626")
        else:
            colors.append("#6b7280")
        prev = b

    # Dark theme — matches the app.
    plt.rcParams.update({
        "figure.facecolor": "#0b0f14",
        "axes.facecolor": "#0b0f14",
        "axes.edgecolor": "#374151",
        "axes.labelcolor": "#9ca3af",
        "xtick.color": "#9ca3af",
        "ytick.color": "#9ca3af",
        "text.color": "#e5e7eb",
        "font.family": "monospace",
    })

    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
    fig.patch.set_facecolor("#0b0f14")

    # Grid.
    ax.grid(True, color="#1f2937", linewidth=0.7, alpha=0.7)
    ax.set_axisbelow(True)

    # Starting balance.
    ax.axhline(
        STARTING_BALANCE,
        color="#6b7280", linestyle="--", linewidth=1, alpha=0.7,
    )

    # Equity line.
    ax.plot(dates, balances, color="#1e3a8a", linewidth=1.6, zorder=3)

    # Points.
    ax.scatter(
        dates, balances,
        c=colors, s=18, zorder=4,
        edgecolors="#0b0f14", linewidths=0.5,
    )

    # Annotate peak and trough.
    peak_i = max(range(len(balances)), key=lambda i: balances[i])
    trough_i = min(range(len(balances)), key=lambda i: balances[i])
    ax.annotate(
        f"peak ${int(balances[peak_i]):,}",
        xy=(dates[peak_i], balances[peak_i]),
        xytext=(8, 10), textcoords="offset points",
        fontsize=8, color="#16a34a",
    )
    ax.annotate(
        f"trough ${int(balances[trough_i]):,}",
        xy=(dates[trough_i], balances[trough_i]),
        xytext=(8, -16), textcoords="offset points",
        fontsize=8, color="#dc2626",
    )

    # Y-axis formatting.
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"${int(v):,}")
    )

    # X-axis formatting.
    ax.xaxis.set_major_formatter(DateFormatter("%b %d"))
    fig.autofmt_xdate(rotation=0, ha="center")

    # Titles.
    first = dates[0].strftime("%b %-d, %Y")
    last = dates[-1].strftime("%b %-d, %Y")
    ax.set_title(
        f"Equity curve — {first} to {last}",
        color="#e5e7eb", fontsize=11, pad=10,
    )
    ax.set_ylabel("Account balance", color="#9ca3af", fontsize=9)

    plt.tight_layout()

    # Write.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"{org_id}.png"
    fig.savefig(out, dpi=dpi, facecolor="#0b0f14", edgecolor="none")
    plt.close(fig)

    log.info("chart.rendered", org_id=org_id[:8], path=str(out))
    return out


def chart_url(org_id: str, cache_bust: bool = True) -> str:
    """The public URL of the rendered chart."""
    base = f"/charts/{org_id}.png"
    if cache_bust:
        return f"{base}?v={int(datetime.utcnow().timestamp())}"
    return base
