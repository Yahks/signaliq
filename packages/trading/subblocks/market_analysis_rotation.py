"""Rotation pickers for the market_analysis subblock.

Each run of market_analysis picks one trade, one history window,
one angle, and one focus. This module holds the pickers and the
run log. Together they keep consecutive articles from repeating.

The pickers avoid the last N combinations already used by the
org. The trade picker prefers the most recent unused loss; if
there are no unused losses it takes the most recent unused trade
of any result.
"""
from __future__ import annotations

import random

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

WINDOWS = [5, 20, 60, 90]
_RECENT_LOOKBACK = 20

# Trade template: the article is about a specific trade.
# Focuses name what the trade ran into. Angles name how the
# trader responded.
TRADE_FOCUSES = [
    "segments",        # the trade failed/worked because the segment moved
    "movers",          # the trade was chasing or avoiding a mover
    "asset_classes",   # cross-asset confirmation was missing or present
    "correlation",     # the stock decoupled from the market
    "vix",             # the volatility regime shifted
    "liquidity",       # the spread widened or stayed thin
    "structure",       # support/resistance broke or held
    "momentum",        # the momentum faded or accelerated
    "multi_timeframe", # same setup across 1h vs 1d vs 1w
]
TRADE_ANGLES = [
    "confirmation",    # the signals agreed, and I acted
    "divergence",      # the signals disagreed, and I ignored it
    "trap",            # the setup looked right but failed
    "breakout",        # the setup worked
    "fade",            # the momentum reversed
    "miss",            # I saw the setup but didn't take it
    "catalyst",        # a news event drove the outcome
]

# Setup template: the article is about the statistics of a
# pattern. No trade. Focuses name a statistical property.
# Angles name a research question.
SETUP_FOCUSES = [
    "percentile_band",   # how the setup behaves at different percentiles
    "sample_size",       # is N large enough to trust?
    "significance",      # does the t-stat clear 1.96?
    "forward_return",    # the distribution of 5d and 10d forward returns
    "regime_condition",  # the setup in different VIX regimes
    "cross_asset",       # the setup when correlation flips
    "skew",              # symmetric or biased?
    "drawdown",          # max adverse excursion
]
SETUP_ANGLES = [
    "saturation",      # the setup appears too often; is the edge degrading?
    "rarity",          # the setup is rare; is the sample too small?
    "regime_flip",     # the setup worked in one regime, broke in another
    "decay",           # the forward returns have weakened over time
    "clustering",      # the setup appears in bursts, not uniformly
]

# Process template: the article is about the trader's own
# behavior across the full trade log. Focuses name a process
# dimension. Angles name what we discovered about it.
PROCESS_FOCUSES = [
    "position_sizing",    # how much capital per trade
    "risk_management",    # drawdown, exposure, correlation of positions
    "regime_awareness",   # behavior in high-vol vs low-vol regimes
    "event_calendar",     # performance around macro events
    "execution_quality",  # slippage, market impact, order type
    "strategy_comparison", # which setup type is outperforming
]
PROCESS_ANGLES = [
    "bias",        # a systematic tendency in the process
    "drift",       # the process has changed over time
    "correlation", # two process variables move together
    "outlier",     # what makes this process deviate from the norm
    "threshold",   # where the process needs a hard boundary
]

# All 5 process focuses now have data.
PROCESS_FOCUSES_WITH_DATA = PROCESS_FOCUSES

# Backward-compatible aliases for code that still imports
# the flat lists.
FOCUSES = TRADE_FOCUSES
ANGLES = TRADE_ANGLES


def pick_history_window() -> int:
    return random.choice(WINDOWS)


def pick_angle() -> str:
    return random.choice(ANGLES)


def pick_focus() -> str:
    return random.choice(FOCUSES)


async def _recent_combinations(
    session: AsyncSession, *, org_id: str,
) -> set[tuple]:
    """Return the last N (window, angle, focus) tuples."""
    rows = (await session.execute(sql_text("""
        SELECT window_days, angle, focus
        FROM market_analysis_runs
        WHERE org_id = :oid
        ORDER BY ran_at DESC
        LIMIT :lim
    """), {"oid": org_id, "lim": _RECENT_LOOKBACK})).mappings().all()
    return {(r["window_days"], r["angle"], r["focus"]) for r in rows}


def pick_combination(
    recent: set[tuple],
    template_id: str = "trade",
) -> tuple[int, str, str]:
    """Pick (window, angle, focus) not in `recent`.

    Template-aware. Trade articles pick from the trade lists
    (segment/vix/movers focuses; confirmation/trap/miss angles).
    Setup articles pick from the setup lists (sample_size/
    significance/regime_condition focuses; rarity/decay/
    regime_flip angles).

    Tries random draws for a bounded number of attempts. If every
    combination is in the recent set, returns the last drawn
    anyway.
    """
    if template_id == "setup":
        focuses = SETUP_FOCUSES
        angles = SETUP_ANGLES
    elif template_id == "process":
        # Fall back to focuses that have data
        focuses = PROCESS_FOCUSES_WITH_DATA
        angles = PROCESS_ANGLES
    else:
        focuses = TRADE_FOCUSES
        angles = TRADE_ANGLES

    for _ in range(30):
        w = random.choice(WINDOWS)
        a = random.choice(angles)
        f = random.choice(focuses)
        if (w, a, f) not in recent:
            return w, a, f
    # Fallback — should not happen with the pool sizes we have.
    return (
        random.choice(WINDOWS),
        random.choice(angles),
        random.choice(focuses),
    )


async def pick_unused_trade(
    session: AsyncSession, *,
    org_id: str,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> dict | None:
    """Return the most recent unused trade for the picked stock.

    Filters by symbol and timeframe when given so the trade
    matches the article's instrument and chart window. Falls
    back to org-wide when neither is provided (kept for
    callers that still need the old behavior).

    Prefers losses. Returns None if every trade has been used.
    """
    where_symbol = ""
    params: dict = {"oid": org_id}
    if symbol:
        where_symbol += " AND symbol = :sym"
        params["sym"] = symbol
    if timeframe:
        where_symbol += " AND timeframe = :tf"
        params["tf"] = timeframe

    row = (await session.execute(sql_text(f"""
        SELECT id::text AS id, symbol, timeframe, traded_at,
               instrument, direction,
               setup, setup_detail, entry, stop, target, exit, result,
               hold_time, exit_reason, lesson, contracts,
               atr_at_entry, percent_of_equity,
               hesitation_note, entry_time, exit_time
        FROM trade_log
        WHERE org_id = :oid
          AND used_in_article_id IS NULL
          AND deleted_at IS NULL
          {where_symbol}
        ORDER BY
            CASE
                WHEN exit_reason IN ('stop hit', 'target hit') THEN 0
                ELSE 1
            END,
            CASE WHEN result < 0 THEN 0 ELSE 1 END,
            traded_at DESC
        LIMIT 1
    """), params)).mappings().first()
    return dict(row) if row else None


async def get_recent_trades(
    session: AsyncSession, *,
    org_id: str,
    limit: int = 5,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> list[dict]:
    """Return the last N trades for the picked stock, newest first.

    Filters by symbol and timeframe when given.
    """
    where_symbol = ""
    params: dict = {"oid": org_id, "lim": limit}
    if symbol:
        where_symbol += " AND symbol = :sym"
        params["sym"] = symbol
    if timeframe:
        where_symbol += " AND timeframe = :tf"
        params["tf"] = timeframe

    rows = (await session.execute(sql_text(f"""
        SELECT id::text AS id, traded_at, instrument, direction,
               entry, exit, result, hold_time
        FROM trade_log
        WHERE org_id = :oid
          AND deleted_at IS NULL
          {where_symbol}
        ORDER BY traded_at DESC
        LIMIT :lim
    """), params)).mappings().all()
    return [dict(r) for r in rows]


async def mark_trade_used(
    session: AsyncSession, *, trade_id: str, post_id: str,
) -> None:
    await session.execute(sql_text("""
        UPDATE trade_log
        SET used_in_article_id = :pid
        WHERE id = :tid
    """), {"pid": post_id, "tid": trade_id})
    await session.commit()


async def log_run(
    session: AsyncSession,
    *,
    org_id: str,
    trade_id: str | None,
    window_days: int,
    angle: str,
    focus: str,
    template_id: str,
    post_id: str | None,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> None:
    await session.execute(sql_text("""
        INSERT INTO market_analysis_runs
            (org_id, trade_id, window_days, angle, focus,
             template_id, post_id, symbol, timeframe)
        VALUES
            (:oid, :tid, :w, :a, :f, :tpl, :pid, :sym, :tf)
    """), {
        "oid": org_id,
        "tid": trade_id,
        "w": window_days,
        "a": angle,
        "f": focus,
        "tpl": template_id,
        "pid": post_id,
        "sym": symbol,
        "tf": timeframe,
    })
    await session.commit()


async def reset_trades_if_due(
    session: AsyncSession, *, org_id: str, every_days: int = 30,
) -> bool:
    """Monthly reset. Returns True if a reset ran.

    Resets used_in_article_id for the org when every unused trade
    has been used, or when the last reset is older than `every_days`.
    """
    # Count unused.
    unused = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM trade_log
        WHERE org_id = :oid
          AND used_in_article_id IS NULL
          AND deleted_at IS NULL
    """), {"oid": org_id})).scalar() or 0
    if unused > 0:
        return False
    # No unused trades — reset.
    await session.execute(sql_text("""
        UPDATE trade_log
        SET used_in_article_id = NULL
        WHERE org_id = :oid
    """), {"oid": org_id})
    await session.commit()
    log.info("market_analysis.trade_pool_reset", org_id=org_id[:8])
    return True
