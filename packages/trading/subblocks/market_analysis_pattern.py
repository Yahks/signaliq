"""Detect patterns in the trader's own trade history.

Given the last N trades in `trade_log`, find the statistical gap that
matters most: the pattern where win rate and average loss diverge
hardest from the baseline. Return that pattern, its statistics, and
the specific trade inside it that best illustrates the gap.

The article this function serves is about the PATTERN, not the trade.
The trade is the proof. Without a pattern, there is no article.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

# How many recent trades to analyze for the pattern.
LOOKBACK = 100


async def _load_trades(
    session: AsyncSession, org_id: str, symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Load the last LOOKBACK trades for the org (optionally per-symbol)."""
    sym_filter = ""
    params: dict = {"oid": org_id, "lim": LOOKBACK}
    if symbol:
        sym_filter = " AND symbol = :sym"
        params["sym"] = symbol
    rows = (await session.execute(sql_text(f"""
        SELECT id::text AS id, symbol, timeframe, traded_at,
               entry, stop, target, exit, result, contracts,
               direction, exit_reason, atr_at_entry, hold_time,
               entry_time, exit_time
        FROM trade_log
        WHERE org_id = :oid
          AND deleted_at IS NULL
          {sym_filter}
        ORDER BY traded_at ASC
        LIMIT :lim
    """), params)).mappings().all()
    return [dict(r) for r in rows]


def _bucket_stats(
    trades: list[dict[str, Any]], in_pattern: list[bool],
) -> tuple[float, float, float, float, int, int]:
    """Return win_rate_in, win_rate_out, avg_loss_in, avg_loss_out,
    n_in, n_out for a given boolean mask."""
    in_trades = [t for t, b in zip(trades, in_pattern) if b]
    out_trades = [t for t, b in zip(trades, in_pattern) if not b]

    def _stats(ts):
        n = len(ts)
        if n == 0:
            return 0.0, 0.0
        wins = sum(1 for t in ts if (t.get("result") or 0) > 0)
        losses = [float(t.get("result") or 0) for t in ts
                  if (t.get("result") or 0) < 0]
        wr = wins / n
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        return wr, avg_loss

    wr_in, al_in = _stats(in_trades)
    wr_out, al_out = _stats(out_trades)
    return wr_in, wr_out, al_in, al_out, len(in_trades), len(out_trades)


def _candidates(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a list of candidate patterns with their stats."""
    if len(trades) < 10:
        return []

    candidates: list[dict[str, Any]] = []

    # ── Candidate 1: time since prior stop hit ──
    mask = []
    for i, t in enumerate(trades):
        if i == 0:
            mask.append(False)
            continue
        prior = trades[i - 1]
        if (prior.get("result") or 0) >= 0:
            mask.append(False)
            continue
        # Compute time between this trade and the prior (by traded_at)
        try:
            delta = (t["traded_at"] - prior["traded_at"]).total_seconds()
            mask.append(delta < 600)  # within 10 min
        except Exception:
            mask.append(False)
    wr_in, wr_out, al_in, al_out, n_in, n_out = _bucket_stats(trades, mask)
    if n_in >= 3 and n_out >= 10:
        candidates.append({
            "name": "Trades fired within 10 min of a prior stop",
            "mask": mask,
            "n_in": n_in, "n_out": n_out,
            "wr_in": wr_in, "wr_out": wr_out,
            "al_in": al_in, "al_out": al_out,
            "gap": abs(wr_out - wr_in),
        })

    # ── Candidate 2: position in a losing streak ──
    mask = []
    streak = 0
    for t in trades:
        if (t.get("result") or 0) < 0:
            streak += 1
            mask.append(streak >= 3)
        else:
            streak = 0
            mask.append(False)
    wr_in, wr_out, al_in, al_out, n_in, n_out = _bucket_stats(trades, mask)
    if n_in >= 3 and n_out >= 10:
        candidates.append({
            "name": "Trades taken as the 3rd+ loss in a consecutive-loss streak",
            "mask": mask,
            "n_in": n_in, "n_out": n_out,
            "wr_in": wr_in, "wr_out": wr_out,
            "al_in": al_in, "al_out": al_out,
            "gap": abs(wr_out - wr_in),
        })

    # ── Candidate 3: size vs median ──
    sizes = [float(t.get("contracts") or 0) for t in trades
             if (t.get("contracts") or 0) > 0]
    if sizes:
        sizes_sorted = sorted(sizes)
        median_size = sizes_sorted[len(sizes_sorted) // 2]
        mask = [
            (float(t.get("contracts") or 0) >= 2.0 * median_size)
            for t in trades
        ]
        wr_in, wr_out, al_in, al_out, n_in, n_out = _bucket_stats(trades, mask)
        if n_in >= 3 and n_out >= 10:
            candidates.append({
                "name": "Trades taken at 2x+ the trader's median size",
                "mask": mask,
                "median_size": median_size,
                "n_in": n_in, "n_out": n_out,
                "wr_in": wr_in, "wr_out": wr_out,
                "al_in": al_in, "al_out": al_out,
                "gap": abs(wr_out - wr_in),
            })

    # ── Candidate 4: direction flip from prior ──
    mask = []
    for i, t in enumerate(trades):
        if i == 0:
            mask.append(False)
            continue
        prior = trades[i - 1]
        mask.append(t.get("direction") != prior.get("direction"))
    wr_in, wr_out, al_in, al_out, n_in, n_out = _bucket_stats(trades, mask)
    if n_in >= 3 and n_out >= 10:
        candidates.append({
            "name": "Trades that flipped direction from the prior trade",
            "mask": mask,
            "n_in": n_in, "n_out": n_out,
            "wr_in": wr_in, "wr_out": wr_out,
            "al_in": al_in, "al_out": al_out,
            "gap": abs(wr_out - wr_in),
        })

    # ── Candidate 5: time of day ──
    mask = []
    for t in trades:
        et = t.get("entry_time")
        if et is None:
            mask.append(False)
            continue
        try:
            # If before 10:30 (local or UTC), consider it "early"
            mask.append(et.hour < 10 or (et.hour == 10 and et.minute < 30))
        except Exception:
            mask.append(False)
    wr_in, wr_out, al_in, al_out, n_in, n_out = _bucket_stats(trades, mask)
    if n_in >= 3 and n_out >= 10:
        candidates.append({
            "name": "Trades opened in the first hour of the session",
            "mask": mask,
            "n_in": n_in, "n_out": n_out,
            "wr_in": wr_in, "wr_out": wr_out,
            "al_in": al_in, "al_out": al_out,
            "gap": abs(wr_out - wr_in),
        })

    return candidates


async def detect_pattern(
    session: AsyncSession,
    *,
    org_id: str,
    symbol: str | None = None,
) -> dict[str, Any] | None:
    """Detect the largest statistical gap in the trader's history.

    Returns a pattern dict (with featured trade) or None if the history
    is too small or too uniform to detect a pattern.
    """
    trades = await _load_trades(session, org_id, symbol=symbol)
    if len(trades) < 15:
        log.info("pattern.too_few_trades", n=len(trades), org_id=org_id[:8])
        return None

    candidates = _candidates(trades)
    if not candidates:
        log.info("pattern.no_candidates", n=len(trades), org_id=org_id[:8])
        return None

    # Pick the candidate with the largest win-rate gap.
    best = max(candidates, key=lambda c: c["gap"])

    # Pick the featured trade: the worst loss inside the pattern bucket.
    in_pattern_trades = [t for t, b in zip(trades, best["mask"]) if b]
    if not in_pattern_trades:
        log.info("pattern.no_in_pattern_trades", name=best["name"])
        return None
    featured = min(in_pattern_trades, key=lambda t: float(t.get("result") or 0))

    # Behavioral note - computed from the featured trade vs. the pool.
    behavior = _behavioral_note(featured, trades, best)

    return {
        "name": best["name"],
        "n_in": best["n_in"],
        "n_out": best["n_out"],
        "wr_in": best["wr_in"],
        "wr_out": best["wr_out"],
        "al_in": best["al_in"],
        "al_out": best["al_out"],
        "gap": best["gap"],
        "median_size": best.get("median_size"),
        "featured_trade": featured,
        "behavioral_note": behavior,
    }


def _behavioral_note(
    featured: dict, all_trades: list[dict], pattern: dict,
) -> str:
    """Compute the behavioral moment from the featured trade vs. context."""
    sizes = [float(t.get("contracts") or 0) for t in all_trades
             if (t.get("contracts") or 0) > 0]
    if not sizes:
        return ""
    median = sorted(sizes)[len(sizes) // 2]
    feat_size = float(featured.get("contracts") or 0)
    feat_result = float(featured.get("result") or 0)

    # Priority 1: size deviation
    if feat_size >= 2.0 * median:
        return (
            f"The trader's median size is {median:.0f}. On this trade the "
            f"trader took {feat_size:.0f} - {feat_size / median:.1f}x the "
            f"median."
        )
    if feat_size <= 0.5 * median and feat_size > 0:
        return (
            f"The trader's median size is {median:.0f}. On this trade the "
            f"trader took {feat_size:.0f} - {feat_size / median:.1f}x the "
            f"median."
        )

    # Priority 2: time since prior stop
    idx = None
    for i, t in enumerate(all_trades):
        if t.get("id") == featured.get("id"):
            idx = i
            break
    if idx is not None and idx > 0:
        prior = all_trades[idx - 1]
        if (prior.get("result") or 0) < 0:
            try:
                delta_s = (featured["traded_at"] - prior["traded_at"]).total_seconds()
                delta_min = delta_s / 60.0
                if delta_min < 5:
                    where = f"{int(delta_min)} minutes after"
                elif delta_min < 120:
                    where = f"{int(delta_min)} minutes after"
                elif delta_min < 60 * 24 * 2:
                    where = f"{int(delta_min / 60)} hours after"
                else:
                    where = f"{int(delta_min / (60 * 24))} days after"
                return (
                    f"The trader re-entered {where} their previous stop."
                )
            except Exception:
                pass

    # Priority 3: direction flip
    if idx is not None and idx > 0:
        prior = all_trades[idx - 1]
        if featured.get("direction") != prior.get("direction"):
            return (
                f"The trader flipped direction from {prior.get('direction')} "
                f"to {featured.get('direction')}."
            )

    # Fallback
    return (
        f"The loss on this trade was ${abs(feat_result):.0f}."
    )
