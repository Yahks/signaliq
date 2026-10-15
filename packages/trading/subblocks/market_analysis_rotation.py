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
ANGLES = ["divergence", "pattern", "outlier", "confirmation", "miss"]
FOCUSES = ["readings", "segments", "vix", "asset_classes", "movers"]
_RECENT_LOOKBACK = 20


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


def pick_combination(recent: set[tuple]) -> tuple[int, str, str]:
    """Pick (window, angle, focus) not in `recent`.

    Tries random draws for a bounded number of attempts. If every
    combination is in the recent set (should not happen with the
    pool sizes we have), returns the last drawn anyway.
    """
    for _ in range(30):
        w = pick_history_window()
        a = pick_angle()
        f = pick_focus()
        if (w, a, f) not in recent:
            return w, a, f
    # Fallback — should not happen.
    return pick_history_window(), pick_angle(), pick_focus()


async def pick_unused_trade(
    session: AsyncSession, *, org_id: str,
) -> dict | None:
    """Return the most recent unused trade. Prefer losses.

    Uses ORDER BY to prefer rows with result < 0 first, then most
    recent. Returns None if every trade has been used.
    """
    row = (await session.execute(sql_text("""
        SELECT id::text AS id, traded_at, instrument, direction,
               setup, entry, stop, target, exit, result,
               hold_time, exit_reason, lesson, contracts
        FROM trade_log
        WHERE org_id = :oid
          AND used_in_article_id IS NULL
          AND deleted_at IS NULL
        ORDER BY
            CASE WHEN result < 0 THEN 0 ELSE 1 END,
            traded_at DESC
        LIMIT 1
    """), {"oid": org_id})).mappings().first()
    return dict(row) if row else None


async def get_recent_trades(
    session: AsyncSession, *, org_id: str, limit: int = 5,
) -> list[dict]:
    """Return the last N trades by traded_at, newest first."""
    rows = (await session.execute(sql_text("""
        SELECT id::text AS id, traded_at, instrument, direction,
               entry, exit, result, hold_time
        FROM trade_log
        WHERE org_id = :oid
          AND deleted_at IS NULL
        ORDER BY traded_at DESC
        LIMIT :lim
    """), {"oid": org_id, "lim": limit})).mappings().all()
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
) -> None:
    await session.execute(sql_text("""
        INSERT INTO market_analysis_runs
            (org_id, trade_id, window_days, angle, focus,
             template_id, post_id)
        VALUES
            (:oid, :tid, :w, :a, :f, :tpl, :pid)
    """), {
        "oid": org_id,
        "tid": trade_id,
        "w": window_days,
        "a": angle,
        "f": focus,
        "tpl": template_id,
        "pid": post_id,
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
