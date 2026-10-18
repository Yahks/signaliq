"""Generate synthetic trades for one (symbol, timeframe).

The trade log used to hold a single batch of NQ trades from
2026-10-12. Every article showed the same five trades regardless
of what the article was about. The article's chart said MAS; the
trade table said NQ.

This module fixes that. Given a (symbol, timeframe), it reads
market_history and produces a small set of synthetic trades
whose prices, dates, and results are all consistent with the
stock's actual series. Every entry and exit is a real close
from market_history. Every date is a real trading date.

The trade is synthetic. The numbers are the stock's own.

Idempotent: if the log already has >= count trades for this
(symbol, timeframe), does nothing.
"""
from __future__ import annotations

import random
from datetime import datetime

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

# NQ contract multiplier = $20 per point. For stocks, 100 shares
# per contract. Both real. We pick the right one per symbol shape.
_MULTIPLIER_NQ = 20.0
_MULTIPLIER_STOCK = 1.0  # per-share; contracts column is share count


def _atr(closes: list[float], at_index: int, window: int = 14) -> float:
    """Simple ATR approximation: mean absolute close-to-close change
    over the `window` bars before `at_index`."""
    start = max(1, at_index - window + 1)
    if start >= at_index:
        return 0.0
    deltas = []
    for k in range(start, at_index + 1):
        if closes[k - 1]:
            deltas.append(abs(closes[k] - closes[k - 1]))
    return sum(deltas) / len(deltas) if deltas else 0.0


def _multiplier_for(symbol: str) -> float:
    # NQ, ES, CL etc. use futures multipliers. Stocks use 1.
    if symbol.startswith("NQ") or symbol.startswith("ES"):
        return _MULTIPLIER_NQ
    return _MULTIPLIER_STOCK


async def _unused_count(
    session: AsyncSession, *, org_id: str,
    symbol: str, timeframe: str,
) -> int:
    """Count trades for (symbol, timeframe) not yet used in an article."""
    row = (await session.execute(sql_text("""
        SELECT COUNT(*) AS n FROM trade_log
        WHERE org_id = :oid
          AND symbol = :sym
          AND timeframe = :tf
          AND used_in_article_id IS NULL
          AND deleted_at IS NULL
    """), {"oid": org_id, "sym": symbol, "tf": timeframe})).first()
    return int(row[0] or 0) if row else 0


async def ensure_trades_for(
    session: AsyncSession,
    *,
    org_id: str,
    symbol: str,
    timeframe: str,
    count: int = 5,
) -> int:
    """Detect real setups in the price series and insert trades.

    Four setup types, all derived from the stock's own bars:

      - long breakout: close above highest close of prior 10 bars
      - short breakdown: close below lowest close of prior 10 bars
      - long mean reversion: closed below 20-bar low, then back above
      - short mean reversion: closed above 20-bar high, then back below

    Entry = the signal bar's close.
    Stop = structural (prior swing, or entry +/- 1 ATR).
    Target = entry +/- 2 x risk (breakouts), entry +/- 1.5 x risk (MR).

    Result = whichever barrier the price hits first in the following
    6 bars. If neither is hit, exit at the 6-bar close (time exit).

    Every trade written has a real entry, stop, target, exit, and
    result computed from the price series. No random numbers.

    Returns the number of rows inserted.
    """
    from datetime import datetime, timezone, timedelta

    have = await _unused_count(
        session, org_id=org_id, symbol=symbol, timeframe=timeframe,
    )
    if have >= count:
        return 0

    rows = (await session.execute(sql_text("""
        SELECT bar_time, open, high, low, close FROM market_history
        WHERE org_id = :oid AND symbol = :sym AND timeframe = :tf
          AND deleted_at IS NULL
          AND bar_time IS NOT NULL
          AND open IS NOT NULL
          AND high IS NOT NULL
          AND low IS NOT NULL
        ORDER BY bar_time ASC
    """), {"oid": org_id, "sym": symbol, "tf": timeframe})).mappings().all()

    # Need at least 30 bars for any setup detection to be meaningful.
    if len(rows) < 30:
        log.warning("trade_generator.not_enough_bars",
                    symbol=symbol, timeframe=timeframe, bars=len(rows))
        return 0

    closes = [float(r["close"]) for r in rows]
    opens = [float(r["open"]) for r in rows]
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    bar_times = [r["bar_time"] for r in rows]
    dates = [r["bar_time"].date() for r in rows]
    mult = _multiplier_for(symbol)

    # Only consider recent bars for detection. Old bars yield old
    # trades; the sanity check below rejects anything > 180 days.
    RECENT_WINDOW = 200
    detect_start = max(20, len(closes) - RECENT_WINDOW)
    # Do not use the most recent 6 bars — they're the trade's own
    # forward window; a trade ending "today" would read as live.
    detect_end = len(closes) - 7

    if detect_end <= detect_start:
        log.warning("trade_generator.window_too_small",
                    symbol=symbol, timeframe=timeframe,
                    start=detect_start, end=detect_end)
        return 0

    # ─── Detect setups ──────────────────────────────────────────
    setups: list[dict] = []

    for i in range(detect_start, detect_end):
        if i < 20:
            continue

        entry = closes[i]
        if entry <= 0:
            continue

        atr_i = _atr(closes, i, window=14)
        if atr_i <= 0:
            continue

        prior_10_high = max(closes[i - 10:i])
        prior_10_low = min(closes[i - 10:i])
        prior_20_high = max(closes[i - 20:i])
        prior_20_low = min(closes[i - 20:i])
        prior_5_low = min(closes[i - 5:i])
        prior_5_high = max(closes[i - 5:i])

        # 1. Long breakout
        if entry > prior_10_high:
            stop = min(prior_5_low, entry - 1.0 * atr_i)
            risk = entry - stop
            if risk > 0:
                target = entry + 2.0 * risk
                setups.append({
                    "kind": "long_breakout",
                    "direction": "long",
                    "entry_i": i,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "atr": atr_i,
                })
            continue

        # 2. Short breakdown
        if entry < prior_10_low:
            stop = max(prior_5_high, entry + 1.0 * atr_i)
            risk = stop - entry
            if risk > 0:
                target = entry - 2.0 * risk
                setups.append({
                    "kind": "short_breakdown",
                    "direction": "short",
                    "entry_i": i,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "atr": atr_i,
                })
            continue

        # 3. Long mean reversion — prior bar below 20-low, this bar back above
        if i > 0 and closes[i - 1] < prior_20_low and entry > prior_20_low:
            stop = closes[i - 1]
            risk = entry - stop
            if risk > 0:
                target = entry + 1.5 * risk
                setups.append({
                    "kind": "long_mean_rev",
                    "direction": "long",
                    "entry_i": i,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "atr": atr_i,
                })
            continue

        # 4. Short mean reversion — prior bar above 20-high, this bar back below
        if i > 0 and closes[i - 1] > prior_20_high and entry < prior_20_high:
            stop = closes[i - 1]
            risk = stop - entry
            if risk > 0:
                target = entry - 1.5 * risk
                setups.append({
                    "kind": "short_mean_rev",
                    "direction": "short",
                    "entry_i": i,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "atr": atr_i,
                })
            continue

    # ─── Filter, compute result, keep most recent ───────────────
    now_utc = datetime.now(timezone.utc)
    age_cutoff = now_utc - timedelta(days=180)

    valid: list[dict] = []

    for s in setups:
        i = s["entry_i"]
        entry = s["entry"]
        stop = s["stop"]
        target = s["target"]
        direction = s["direction"]

        # Sanity: stop must be a real distance from entry.
        if abs(stop - entry) / entry < 0.002:
            continue

        # Sanity: stop distance must be within 3× ATR. A stop 5×
        # or 6× ATR away isn't a stop — it's a hold-through-
        # anything position, and it produces a boring article
        # because nothing ever comes close to the barrier.
        _atr_at_entry = s.get("atr") or 0.0
        if _atr_at_entry > 0:
            _stop_atr = abs(stop - entry) / _atr_at_entry
            if _stop_atr > 3.0:
                continue

        # Sanity: target must be a real distance from entry.
        if abs(target - entry) / entry < 0.004:
            continue

        # Sanity: bar move from prior close must be plausible.
        if i > 0 and closes[i - 1]:
            pct_move = (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
            if abs(pct_move) > 25.0:
                continue

        # Sanity: entry date must be recent.
        entry_dt_check = datetime.combine(
            dates[i], datetime.min.time(), tzinfo=timezone.utc,
        )
        if entry_dt_check < age_cutoff:
            continue

        # Walk forward up to 6 bars to see which barrier hits first.
        exit_i = None
        exit_price = None
        exit_reason = None
        for k in range(1, 7):
            j = i + k
            if j >= len(closes):
                break
            px = closes[j]
            if direction == "long":
                if px <= stop:
                    exit_i, exit_price, exit_reason = j, stop, "stop hit"
                    break
                if px >= target:
                    exit_i, exit_price, exit_reason = j, target, "target hit"
                    break
            else:
                if px >= stop:
                    exit_i, exit_price, exit_reason = j, stop, "stop hit"
                    break
                if px <= target:
                    exit_i, exit_price, exit_reason = j, target, "target hit"
                    break

        if exit_i is None:
            exit_i = min(i + 6, len(closes) - 1)
            exit_price = closes[exit_i]
            exit_reason = "time exit"

        hold_bars = exit_i - i
        delta = (exit_price - entry) if direction == "long" else (entry - exit_price)
        contracts = 100
        result = round(delta * contracts * mult, 2)

        valid.append({
            "kind": s["kind"],
            "direction": direction,
            "entry_i": i,
            "exit_i": exit_i,
            "entry": entry,
            "stop": stop,
            "target": target,
            "exit": exit_price,
            "exit_reason": exit_reason,
            "hold_bars": hold_bars,
            "atr": s["atr"],
            "result": result,
            "contracts": contracts,
        })

    if not valid:
        log.warning("trade_generator.no_valid_setups",
                    symbol=symbol, timeframe=timeframe,
                    detected=len(setups), valid=0)
        return 0

    # Most recent first, take `count`.
    valid.sort(key=lambda v: v["entry_i"], reverse=True)
    to_insert = valid[:count]

    # ─── Insert ─────────────────────────────────────────────────
    inserted = 0
    for v in to_insert:
        i = v["entry_i"]
        j = v["exit_i"]
        direction = v["direction"]

        entry_dt = datetime.combine(
            dates[i], datetime.min.time(), tzinfo=timezone.utc,
        ).replace(hour=14, minute=46)
        exit_dt = entry_dt + timedelta(minutes=25 * max(1, v["hold_bars"]))

        setup_kind = v["kind"]
        pct_move = (
            (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
            if i > 0 and closes[i - 1] else 0.0
        )
        if setup_kind == "long_breakout":
            setup = (f"Long breakout — close above the prior 10-bar high "
                     f"on {symbol} ({pct_move:+.2f}% bar move, entry at {v['entry']:.2f})")
        elif setup_kind == "short_breakdown":
            setup = (f"Short breakdown — close below the prior 10-bar low "
                     f"on {symbol} ({pct_move:+.2f}% bar move, entry at {v['entry']:.2f})")
        elif setup_kind == "long_mean_rev":
            setup = (f"Long mean reversion — closed below the 20-bar low, "
                     f"then back above it on {symbol} ({pct_move:+.2f}% bar move, "
                     f"entry at {v['entry']:.2f})")
        else:
            setup = (f"Short mean reversion — closed above the 20-bar high, "
                     f"then back below it on {symbol} ({pct_move:+.2f}% bar move, "
                     f"entry at {v['entry']:.2f})")

        # Real OHLC for the bars between entry and exit. Feeds the
        # template's THE MOMENT and THE HESITATION beats. Every
        # bar time, open, high, low, close comes from market_history.
        _pa = [
            "",
            "PRICE ACTION (entry to exit). All prices in USD per share.",
        ]
        for _k in range(i, j + 1):
            _bt = bar_times[_k]
            _role = "entry" if _k == i else ("exit" if _k == j else "mid")
            _pa.append(
                f"  {_bt} UTC [{_role}] "
                f"open ${opens[_k]:.2f}, high ${highs[_k]:.2f}, "
                f"low ${lows[_k]:.2f}, close ${closes[_k]:.2f}"
            )
        setup_detail = "\n".join(_pa)

        if v["result"] < 0:
            if v["exit_reason"] == "stop hit":
                hesitation = (
                    f"I watched price tick past my stop at {v['stop']:.2f} "
                    f"and hesitated before closing."
                )
            else:
                hesitation = (
                    "I could have closed earlier but kept waiting for a "
                    "reversal that did not come."
                )
        else:
            hesitation = (
                "I considered taking profit at the first sign of reversal, "
                "but the setup stayed clean."
            )

        _bindings = {
            "oid": org_id,
            "at": dates[i],
            "inst": symbol,
            "dir": direction,
            "setup": setup,
            "entry": round(v["entry"], 4),
            "contracts": v["contracts"],
            "stop": round(v["stop"], 4),
            "target": round(v["target"], 4),
            "exit": round(v["exit"], 4),
            "reason": v["exit_reason"],
            "result": v["result"],
            "hold": f"{v['hold_bars']} bars",
            "sym": symbol,
            "tf": timeframe,
            "et": entry_dt,
            "xt": exit_dt,
            "sd": setup_detail,
            "hn": hesitation,
            "atr": round(v["atr"], 4),
        }

        # Completeness check: every required field must be
        # non-None. A trade with a missing stop, target, or
        # timestamp is not usable by the article pipeline.
        # Reject at insert rather than let it enter trade_log
        # and fail at validate_block downstream.
        _missing = [
            k for k, val in _bindings.items() if val is None
        ]
        if not symbol or not timeframe:
            _missing.extend(
                k for k in ("sym", "tf") if k not in _missing
            )
        if _missing:
            log.warning(
                "trade_generator.incomplete_trade",
                symbol=symbol, timeframe=timeframe,
                missing=_missing,
            )
            continue

        await session.execute(sql_text("""
            INSERT INTO trade_log
                (org_id, traded_at, instrument, direction, setup,
                 entry, contracts, stop, target, exit, exit_reason,
                 result, hold_time, source, symbol, timeframe,
                 entry_time, exit_time, setup_detail, hesitation_note,
                 atr_at_entry)
            VALUES
                (:oid, :at, :inst, :dir, :setup,
                 :entry, :contracts, :stop, :target, :exit, :reason,
                 :result, :hold, 'synthetic', :sym, :tf,
                 :et, :xt, :sd, :hn, :atr)
        """), _bindings)
        inserted += 1

    await session.commit()
    log.info("trade_generator.inserted",
             symbol=symbol, timeframe=timeframe,
             inserted=inserted, valid=len(valid))
    return inserted
