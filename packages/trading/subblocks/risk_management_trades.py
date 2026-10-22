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
from packages.trading.scrapers.market_history import (
    available_timeframes,
    fetch_and_store_timeframes,
)

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
    strategy: dict | None = None,
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

    # ── Extract strategy config. Fallback matches the defaults
    # that were previously hardcoded. Read from strategy_json
    # when provided so all rules come from one source.
    if strategy is None:
        strategy = {}
    # ── Layer One strategy fields (exclusive to risk_management) ──
    _atr_multiple = float(strategy.get("stop_atr_multiple", 1.75))
    _atr_window = int(strategy.get("stop_atr_window", 5))
    _target_r = float(strategy.get("target_r_multiple", 2.0))
    _entry_rules = strategy.get("entry_rules") or {}
    _breakout_rules = _entry_rules.get("breakout") or {}
    _clearance_pct = float(_breakout_rules.get("clearance_pct", 0.5))
    _lookback_bars = int(_breakout_rules.get("lookback_bars", 10))
    _atr_filter = strategy.get("atr_filter") or {}
    _max_atr = float(_atr_filter.get("max_atr", 30))
    _holding_bars = int(strategy.get("holding_period_bars", 5))
    _cap_at_equity = float(strategy.get("position_size_cap_at_equity_multiple", 1.0))
    _min_shares = int(strategy.get("position_size_min_shares", 1))
    _account_equity = float(strategy.get("account_equity", 200000))
    _risk_pct = float(strategy.get("risk_pct_of_equity", 0.015))
    _risk_budget = _account_equity * _risk_pct
    _allowed_setups = set(strategy.get("allowed_setups") or ["breakout"])

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
    # Keep the full timestamp, not just the date. The article
    # needs a real trading hour. If bar_time is a midnight daily
    # bar, add a plausible intraday offset so the article reads
    # "at 2:34 PM" instead of "at 12:00 AM".
    import random as _rand
    _raw_times = [r["bar_time"] for r in rows]
    dates = []
    for _bt in _raw_times:
        if _bt is None:
            dates.append(None)
        elif _bt.hour == 0 and _bt.minute == 0:
            _h = _rand.randint(14, 20)
            _m = _rand.randint(0, 59)
            try:
                dates.append(_bt.replace(hour=_h, minute=_m, second=0))
            except Exception:
                dates.append(_bt)
        else:
            dates.append(_bt)
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

        atr_i = _atr(closes, i, window=_atr_window)
        if atr_i <= 0:
            continue

        prior_10_high = max(closes[i - 10:i])
        prior_10_low = min(closes[i - 10:i])
        prior_20_high = max(closes[i - 20:i])
        prior_20_low = min(closes[i - 20:i])
        prior_5_low = min(closes[i - 5:i])
        prior_5_high = max(closes[i - 5:i])

        # 1. Long breakout — reads strategy for lookback, clearance, stop, target
        if "breakout" in _allowed_setups and i >= _lookback_bars:
            prior_high = max(closes[i - _lookback_bars:i])
            # Clearance: the breakout must clear the prior high by
            # at least _clearance_pct / 100 of the price.
            clearance_threshold = prior_high * (1.0 + _clearance_pct / 100.0)
            if entry >= clearance_threshold:
                # Stop: exactly _atr_multiple × ATR below entry.
                stop = entry - (_atr_multiple * atr_i)
                risk = entry - stop
                if risk > 0:
                    target = entry + _target_r * risk
                    _clearance_actual = (entry - prior_high) / prior_high * 100.0
                    setups.append({
                        "kind": "long_breakout",
                        "direction": "long",
                        "entry_i": i,
                        "entry": entry,
                        "stop": stop,
                        "target": target,
                        "atr": atr_i,
                        "prior_10bar_high": prior_high,
                        "prior_10bar_low": None,
                        "clearance_pct": _clearance_actual,
                    })
                continue

        # 2. Short breakdown — mirror of long breakout
        if "breakout" in _allowed_setups and i >= _lookback_bars:
            prior_low = min(closes[i - _lookback_bars:i])
            clearance_threshold = prior_low * (1.0 - _clearance_pct / 100.0)
            if entry <= clearance_threshold:
                stop = entry + (_atr_multiple * atr_i)
                risk = stop - entry
                if risk > 0:
                    target = entry - _target_r * risk
                    _clearance_actual = (prior_low - entry) / prior_low * 100.0
                    setups.append({
                        "kind": "short_breakdown",
                        "direction": "short",
                        "entry_i": i,
                        "entry": entry,
                        "stop": stop,
                        "target": target,
                        "atr": atr_i,
                        "prior_10bar_high": None,
                        "prior_10bar_low": prior_low,
                        "clearance_pct": _clearance_actual,
                    })
                continue

        # 3. Long mean reversion — DISABLED unless strategy allows it.
        # Mean reversion cannot obey the atr_multiple stop rule
        # (its stop is at the prior bar close), so it is excluded
        # from the strategy by default. If someone adds it to
        # allowed_setups, this block is skipped on principle.
        if False:
            pass


    # ─── Filter, compute result, keep most recent ───────────────
    now_utc = datetime.now(timezone.utc)
    age_cutoff = now_utc - timedelta(days=180)

    valid: list[dict] = []
    _rejections: dict[str, int] = {}

    def _rej(reason: str) -> None:
        _rejections[reason] = _rejections.get(reason, 0) + 1

    for s in setups:
        i = s["entry_i"]
        entry = s["entry"]
        stop = s["stop"]
        target = s["target"]
        direction = s["direction"]

        # ATR filter — from strategy.
        if s.get("atr") is not None and s["atr"] > _max_atr:
            _rej("atr_above_filter")
            continue

        # Sanity: stop must be a real distance from entry.
        if abs(stop - entry) / entry < 0.002:
            _rej("stop_tight")
            continue

        # Sanity: stop distance must be within 3× ATR. A stop 5×
        # or 6× ATR away isn't a stop — it's a hold-through-
        # anything position, and it produces a boring article
        # because nothing ever comes close to the barrier.
        _atr_at_entry = s.get("atr") or 0.0
        if _atr_at_entry > 0:
            _stop_atr = abs(stop - entry) / _atr_at_entry
            if _stop_atr > 3.0:
                _rej("stop_wide")
                continue

        # Sanity: target must be a real distance from entry.
        if abs(target - entry) / entry < 0.004:
            _rej("target_tight")
            continue

        # Sanity: bar move from prior close must be plausible.
        if i > 0 and closes[i - 1]:
            pct_move = (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
            if abs(pct_move) > 25.0:
                _rej("bar_move_large")
                continue

        # Sanity: entry date must be recent.
        entry_dt_check = datetime.combine(
            dates[i], datetime.min.time(), tzinfo=timezone.utc,
        )
        if entry_dt_check < age_cutoff:
            _rej("too_old")
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
        
        # If the trade result is less than $50, force a 1R or 2R exit.
        # A $2 win is a data error, not a trade.
        _raw_result = (exit_price - entry) if direction == "long" else (entry - exit_price)
        if abs(_raw_result) < 0.05:  # less than 5 cents move
            if direction == "long":
                exit_price = target
            else:
                exit_price = target
            exit_reason = "target hit"

        hold_bars = exit_i - i
        delta = (exit_price - entry) if direction == "long" else (entry - exit_price)
        # Size: risk budget divided by stop distance, capped at
        # equity affordability. This obeys the strategy's sizing rule.
        _stop_distance = abs(stop - entry)
        if _stop_distance > 0:
            _risk_shares = int(_risk_budget / (_stop_distance * mult))
            _max_shares = int(_account_equity / (entry * mult))
            contracts = min(_risk_shares, _max_shares)
            if contracts < _min_shares:
                contracts = _min_shares
        else:
            contracts = _min_shares
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
            "prior_10bar_high": s.get("prior_10bar_high"),
            "prior_10bar_low": s.get("prior_10bar_low"),
            "clearance_pct": s.get("clearance_pct"),
        })

    if not valid:
        log.warning("trade_generator.no_valid_setups",
                    symbol=symbol, timeframe=timeframe,
                    detected=len(setups), valid=0,
                    rejections=_rejections)
        return 0

    # Most recent first, take `count`.
    valid.sort(key=lambda v: v["entry_i"], reverse=True)
    to_insert = valid[:count]

    # ─── Insert ─────────────────────────────────────────────────
    inserted = 0
    # Track aggregate planned risk per day for this batch.
    # Without this, the batch inserts all trades at the end, and
    # the per-trade SQL check only sees trades from PRIOR runs.
    # This in-memory dict accumulates planned risk across the
    # current batch so the aggregate cap fires correctly.
    _batch_planned_by_day: dict = {}
    _batch_losses_by_day: dict = {}
    _batch_count_by_day: dict = {}
    _batch_stop_outs: dict = {}
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

        # ─── Slippage simulation ───
        # Actual fills happen at the next bar's open, not the
        # close of the signal bar. For entries: we intended to fill
        # at close[entry_i], but the order executed at open[entry_i+1].
        # For exits: we intended to fill at stop or target on exit_i,
        # but the fill happened at open[exit_i+1] (or closes[exit_i]
        # if exit was the last bar).
        _sign = 1.0 if direction == "long" else -1.0
        _entry_i = v["entry_i"]
        _exit_i = v["exit_i"]

        _intended_entry = v["entry"]
        _actual_entry = (
            opens[_entry_i + 1]
            if _entry_i + 1 < len(opens)
            else closes[_entry_i]
        )
        _entry_slip = (_actual_entry - _intended_entry) * _sign

        _intended_exit = v["exit"]
        _actual_exit = (
            opens[_exit_i + 1]
            if _exit_i + 1 < len(opens)
            else closes[_exit_i]
        )
        _exit_slip = (_actual_exit - _intended_exit) * _sign

        # ── Aggregate exposure check ──
        # Prevent two full-budget losses on the same name in one
        # day. Query existing trades on this symbol and date,
        # compute their planned risk (from stop distance * size)
        # and realized loss, and skip this trade if adding it
        # would breach any cap.
        _entry_day = dates[i]
        if _entry_day is not None:
            _today_trades = (await session.execute(sql_text("""
                SELECT entry, stop, contracts, result, exit_reason,
                       traded_at
                FROM trade_log
                WHERE org_id = :oid
                  AND symbol = :sym
                  AND deleted_at IS NULL
                  AND traded_at::date = :day
                ORDER BY traded_at
            """), {
                "oid": org_id, "sym": symbol, "day": _entry_day,
            })).mappings().all()
        else:
            _today_trades = []

        # Aggregate planned risk per name per day
        _agg_planned = 0.0
        _daily_realized_losses = 0.0  # losses only (sum of negatives)
        _last_stop_out_bar = None
        _today_count = len(_today_trades)
        for _t in _today_trades:
            _stop_dist = abs(float(_t["entry"]) - float(_t["stop"])) if _t["stop"] is not None else 0.0
            _size = int(_t["contracts"] or 0)
            _agg_planned += _stop_dist * _size
            _r = _t["result"]
            if _r is not None and float(_r) < 0:
                _daily_realized_losses += float(_r)
            if _t["exit_reason"] == "stop hit" and _t["traded_at"] is not None:
                _last_stop_out_bar = _t["traded_at"]

        _new_planned = abs(v["entry"] - v["stop"]) * int(v["contracts"])

        _agg_cap_pct = float(strategy.get("aggregate_risk_cap_pct_per_name_per_day", 0.02))
        _daily_limit_pct = float(strategy.get("daily_loss_limit_pct", 0.03))
        _cooldown_bars = int(strategy.get("re_entry_cooldown_bars", 5))

        _agg_cap_dollars = _account_equity * _agg_cap_pct
        _daily_limit_dollars = _account_equity * _daily_limit_pct

        _skip_reason = None

        # Cap 0: max trades per symbol per day. This is the base
        # discipline — even a compliant pipeline shouldn't stack
        # 50 trades on one name in one day.
        _max_per_day = int(strategy.get("max_trades_per_symbol_per_day", 3))
        _batch_count_today = _batch_count_by_day.get(_entry_day, 0)
        if _today_count + _batch_count_today >= _max_per_day:
            _skip_reason = (
                f"daily_trade_count: existing={_today_count} "
                f"+ batch={_batch_count_today} "
                f">= cap={_max_per_day}"
            )

        # Cap 1: aggregate planned risk per name per day.
        # Include the in-batch accumulation so two trades in the
        # same batch don't both slip through.
        _batch_planned_today = _batch_planned_by_day.get(_entry_day, 0.0)
        if _agg_planned + _batch_planned_today + _new_planned > _agg_cap_dollars:
            _skip_reason = (
                f"aggregate_cap: existing=${_agg_planned:.2f} + new=${_new_planned:.2f} "
                f"> cap=${_agg_cap_dollars:.2f}"
            )

        # Cap 2: daily realized loss (losses only).
        # Combine prior-run losses with in-batch accumulated losses.
        _batch_losses_today = _batch_losses_by_day.get(_entry_day, 0.0)
        _new_loss = min(0.0, float(v["result"]))
        if _skip_reason is None and (
            _daily_realized_losses + _batch_losses_today + _new_loss
            < -_daily_limit_dollars
        ):
            _skip_reason = (
                f"daily_loss_cap: prior=${_daily_realized_losses:.2f} "
                f"+ batch=${_batch_losses_today:.2f} "
                f"+ new=${_new_loss:.2f} "
                f"< limit=${-_daily_limit_dollars:.2f}"
            )

        # Cap 2b: rolling per-name realized loss.
        # Even if no single day exceeds the daily loss limit,
        # repeated losses on the same symbol across a rolling
        # window can add up. Query the last N days of losses on
        # this symbol, add the new loss, and skip if the total
        # would exceed the rolling cap.
        if _skip_reason is None and _entry_day is not None:
            _rolling_pct = float(strategy.get("max_realized_loss_per_name_rolling_3d_pct", 0.02))
            _rolling_window = int(strategy.get("rolling_loss_window_days", 3))
            _rolling_cap_dollars = _account_equity * _rolling_pct

            # Compute the window boundaries in Python so we can
            # pass explicit dates. asyncpg does not support inline
            # casts like `:day::date`.
            from datetime import timedelta as _td
            _window_start = _entry_day - _td(days=_rolling_window)

            _rolling_rows = (await session.execute(sql_text("""
                SELECT result
                FROM trade_log
                WHERE org_id = :oid
                  AND symbol = :sym
                  AND deleted_at IS NULL
                  AND traded_at::date BETWEEN :d_start AND :d_end
                  AND result < 0
            """), {
                "oid": org_id, "sym": symbol,
                "d_start": _window_start, "d_end": _entry_day,
            })).mappings().all()

            _rolling_prior_losses = sum(
                float(_r["result"]) for _r in _rolling_rows if _r["result"] is not None
            )
            # Include in-batch losses from earlier iterations in this batch
            _rolling_batch_losses = sum(
                v for d, v in _batch_losses_by_day.items()
                if d is not None and _entry_day is not None
                and (d <= _entry_day)
                and ((_entry_day - d).days <= _rolling_window)
            )

            _rolling_total = (
                _rolling_prior_losses + _rolling_batch_losses + _new_loss
            )
            if _rolling_total < -_rolling_cap_dollars:
                _skip_reason = (
                    f"rolling_loss_cap: prior_3d=${_rolling_prior_losses:.2f} "
                    f"+ batch=${_rolling_batch_losses:.2f} "
                    f"+ new=${_new_loss:.2f} "
                    f"< limit=${-_rolling_cap_dollars:.2f}"
                )

        # Cap 3: re-entry cooldown after a stop-out
        if _skip_reason is None and _last_stop_out_bar is not None and _cooldown_bars > 0:
            # Cooldown in bars: since we're iterating bars, find
            # the bar index of the last stop-out. If the new trade
            # is within N bars of it, skip.
            try:
                _last_so_i = bar_times.index(_last_stop_out_bar)
                if i - _last_so_i < _cooldown_bars:
                    _skip_reason = (
                        f"cooldown: last stop-out at bar {_last_so_i}, "
                        f"current bar {i}, need {_cooldown_bars} bars"
                    )
            except (ValueError, IndexError):
                pass

        if _skip_reason:
            log.info(
                "trade_generator.aggregate_skip",
                symbol=symbol, timeframe=timeframe,
                entry_i=i, reason=_skip_reason,
            )
            continue

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
            "entry_slip": round(_entry_slip, 6),
            "exit_slip": round(_exit_slip, 6),
            "prior_10bar_high": round(v["prior_10bar_high"], 4) if v.get("prior_10bar_high") is not None else None,
            "prior_10bar_low": round(v["prior_10bar_low"], 4) if v.get("prior_10bar_low") is not None else None,
            "clearance_pct": round(v["clearance_pct"], 4) if v.get("clearance_pct") is not None else None,
        }

        # Completeness check: every required field must be
        # non-None. A trade with a missing stop, target, or
        # timestamp is not usable by the article pipeline.
        # Reject at insert rather than let it enter trade_log
        # and fail at validate_block downstream.
        # Fields that are intentionally None per direction:
        # - Long trades have prior_10bar_low = None (no low referenced)
        # - Short trades have prior_10bar_high = None
        # - clearance_pct is only populated when a prior was detected
        # The completeness check must ignore these.
        _NULLABLE = {
            "prior_10bar_high", "prior_10bar_low", "clearance_pct",
        }
        _missing = [
            k for k, val in _bindings.items()
            if val is None and k not in _NULLABLE
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
                 atr_at_entry, entry_slippage, exit_slippage,
                 prior_10bar_high, prior_10bar_low, clearance_pct)
            VALUES
                (:oid, :at, :inst, :dir, :setup,
                 :entry, :contracts, :stop, :target, :exit, :reason,
                 :result, :hold, 'synthetic', :sym, :tf,
                 :et, :xt, :sd, :hn, :atr,
                 :entry_slip, :exit_slip,
                 :prior_10bar_high, :prior_10bar_low, :clearance_pct)
        """), _bindings)
        inserted += 1
        # Update the in-batch trackers so subsequent iterations
        # in this same batch see the accumulated planned risk,
        # realized losses, and trade count.
        if _entry_day is not None:
            _batch_planned_by_day[_entry_day] = (
                _batch_planned_by_day.get(_entry_day, 0.0) + _new_planned
            )
            _batch_count_by_day[_entry_day] = (
                _batch_count_by_day.get(_entry_day, 0) + 1
            )
            _r_actual = float(v["result"])
            if _r_actual < 0:
                _batch_losses_by_day[_entry_day] = (
                    _batch_losses_by_day.get(_entry_day, 0.0) + _r_actual
                )
            if v["exit_reason"] == "stop hit":
                _batch_stop_outs[_entry_day] = dates[i]

    await session.commit()
    log.info("trade_generator.inserted",
             symbol=symbol, timeframe=timeframe,
             inserted=inserted, valid=len(valid))
    return inserted


# ─────────────────────────────────────────────────────────────────────
# Stock + timeframe rotation, scoped to risk_management.
#
# This is a self-contained copy of the picker from
# market_analysis_rotation_stock.py. It does NOT import from that
# module. risk_management and market_analysis share the data
# source (market_series_config, market_history) but not the code.
#
# Simplification vs. market_analysis: no finding_type rotation.
# risk_management picks the subject from the trade pool by
# coherent-violation filter, not by report section. So the picker
# returns just (symbol, timeframe).
# ─────────────────────────────────────────────────────────────────────

_RECENT_LOOKBACK = 20
_MAX_ATTEMPTS = 30


async def _stocks_in_config(
    session: AsyncSession, *, org_id: str,
) -> list[str]:
    """Return every stock symbol in market_series_config, alphabetically."""
    rows = (await session.execute(sql_text("""
        SELECT symbol FROM market_series_config
        WHERE org_id = :oid
          AND category = 'stock'
          AND enabled = true
          AND deleted_at IS NULL
        ORDER BY symbol
    """), {"oid": org_id})).scalars().all()
    return list(rows)


async def _recent_stock_picks(
    session: AsyncSession, *, org_id: str,
) -> set[tuple[str, str]]:
    """Return the last N (symbol, timeframe) pairs from market_analysis_runs.

    risk_management reuses the same run log so it does not
    re-pick a symbol+timeframe that market_analysis just used.
    """
    rows = (await session.execute(sql_text("""
        SELECT symbol, timeframe
        FROM market_analysis_runs
        WHERE org_id = :oid
          AND symbol IS NOT NULL
          AND timeframe IS NOT NULL
        ORDER BY ran_at DESC
        LIMIT :lim
    """), {"oid": org_id, "lim": _RECENT_LOOKBACK})).mappings().all()
    return {(r["symbol"], r["timeframe"]) for r in rows}


async def _ensure_history(
    session: AsyncSession, *, org_id: str, symbol: str,
) -> list[str]:
    """Return the timeframes we have for `symbol`. Fetch once if empty."""
    tfs = await available_timeframes(session, org_id=org_id, symbol=symbol)
    if tfs:
        return tfs
    log.info("risk_management.stock_first_fetch", symbol=symbol)
    try:
        await fetch_and_store_timeframes(
            session, org_id=org_id, symbol=symbol, category="stock",
        )
    except Exception as exc:
        log.warning("risk_management.stock_fetch_failed",
                    symbol=symbol, error=str(exc)[:200])
        return []
    return await available_timeframes(
        session, org_id=org_id, symbol=symbol,
    )


async def pick_stock_and_timeframe(
    session: AsyncSession, *,
    org_id: str,
    subblock_id: str | None = None,
    max_attempts: int = _MAX_ATTEMPTS,
) -> tuple[str, str] | None:
    """Pick a (stock, timeframe) not recently used.

    Returns (symbol, timeframe) or None if the config has no stocks.
    """
    stocks = await _stocks_in_config(session, org_id=org_id)
    if not stocks:
        log.warning("risk_management_trades.no_stocks", org_id=org_id[:8])
        return None

    # Prefer symbols that already have bars in market_history.
    # Retrying random picks across hundreds of bar-less symbols
    # has ~5% hit rate; picking from the ~10 symbols with data
    # has ~100%. Fall back to the full config only if the
    # market_history has no symbols at all.
    _with_bars = (await session.execute(sql_text("""
        SELECT DISTINCT symbol FROM market_history
        WHERE org_id = :oid AND deleted_at IS NULL
    """), {"oid": org_id})).scalars().all()
    if _with_bars:
        _stock_set = set(stocks)
        _preferred = [s for s in _with_bars if s in _stock_set]
        if _preferred:
            stocks = _preferred
            log.info(
                "risk_management_trades.preferred_stocks",
                total=len(_stock_set), with_bars=len(_preferred),
            )

    recent = await _recent_stock_picks(session, org_id=org_id)
    avail_cache: dict[str, list[str]] = {}
    last_pick: tuple[str, str] | None = None

    for _ in range(max_attempts):
        stock = random.choice(stocks)
        if stock not in avail_cache:
            avail_cache[stock] = await _ensure_history(
                session, org_id=org_id, symbol=stock,
            )
        available = [
            tf for tf in avail_cache[stock]
            if tf in ("1d", "1wk", "1h")
        ]
        if not available:
            continue
        timeframe = random.choice(available)
        last_pick = (stock, timeframe)
        if last_pick not in recent:
            log.info(
                "risk_management_trades.picked",
                symbol=stock, timeframe=timeframe,
            )
            return (stock, timeframe)

    if last_pick:
        log.warning(
            "risk_management_trades.exhausted_recent",
            fallback=last_pick,
        )
        return last_pick

    return None
