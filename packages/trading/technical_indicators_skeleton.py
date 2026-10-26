"""technical_indicators_skeleton: computed trade evidence for a
technical-indicator article.

The published technical_indicators articles (EMA 0165d05a, ATR
9ab84460) cited trades that do not exist — dates in the future
("2026-12-11"), prices with no symbol ("22 500"), and P&L with no
size ("$3 000"). The process excerpt already carries five real
trades from trade_log, newest first; the model ignored them and
invented its own.

This module surfaces those five real trades with every figure
computed by technical_indicators_math (result reconciles to the
cent; percent_of_equity computed because the column is NULL;
stop ATR multiple and R-multiple derived). It filters by the
coordinate's dimension:

  D2 (entry logic)   -> the 0.75xATR cluster (breakout timing)
  D8 (chart overlay) -> the 1.75xATR cluster (indicator/stop)

and states which cluster it used, so the article cannot claim a
stop rule the cited trades do not follow.

The math is verified against the real five trades (2026-10-21):
PLTR +145.28 (0.75x), ROK -1738.04 (1.75x), ROK -1385.63 (1.75x),
NXPI +1243.01 (0.75x), CMCSA -3000.05 (1.75x, stored -2999.91,
delta 0.14 flagged).
"""
from __future__ import annotations

import re

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

import re

from sqlalchemy import text as sql_text

from .technical_indicators_math import (
    ACCOUNT_EQUITY, compute_batch, compute_trade,
)

# Which stop-multiple cluster each dimension cites.
# 0.75 and 1.75 are the two observed clusters in trade_log.
DIMENSION_CLUSTER: dict[str, float] = {
    "D2": 0.75,   # entry_logic  -> breakout timing
    "D8": 1.75,   # chart_overlay -> indicator / stop
}

CLUSTER_TOLERANCE = 0.05


def _fmt(v: Any, places: int = 2) -> str:
    if v is None:
        return "unknown"
    try:
        return f"{float(v):,.{places}f}"
    except (TypeError, ValueError):
        return "unknown"


async def _recent_trades(
    session: AsyncSession, org_id: str, limit: int = 20,
) -> list[dict[str, Any]]:
    rows = (await session.execute(sql_text("""
        SELECT symbol, timeframe, direction, contracts, entry, stop,
               exit, result, atr_at_entry, traded_at, setup
        FROM trade_log
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY traded_at DESC
        LIMIT :n
    """), {"oid": org_id, "n": limit})).mappings().all()
    return [dict(r) for r in rows]


# Rules quoted from the CURRENT PROCESS section of the process
# document. Each (pattern, label) pair matches a real threshold the
# trader states, so the validator can verify a percentage the
# article cites. PRIOR VERSIONS are deliberately excluded — they
# describe superseded rules and must not be cited as current.
_CURRENT_RULES: tuple[tuple[str, str], ...] = (
    (r"require a ([\d.]+)% clearance", "0.5% clearance beyond the high/low"),
    (r"range above ([\d.]+)% of price", "0.5% 24-hour range filter"),
    (r"([\d.]+)% clearance", "0.5% clearance rule"),
)


# Only these sections carry rule thresholds a technical-indicator
# article may cite. Fundamentals, blending allocations, and the
# history summary also contain percentages but are not indicator
# rules — including them would let the article cite "55% win rate"
# or "20% of trades" as if they were thresholds.
_RULE_SECTIONS = (
    "THE DAILY SCAN", "ENTRY LOGIC", "EXIT LOGIC", "POSITION SIZING",
)


def _rule_percentages(process_excerpt: str) -> set[str]:
    """Percentages that are rule thresholds in the CURRENT PROCESS
    rule sections (daily scan, entry, exit, sizing)."""
    if not process_excerpt:
        return set()
    m = re.search(r"=== CURRENT PROCESS ===(.*?)(?:=== PRIOR VERSIONS|\Z)",
                  process_excerpt, re.DOTALL)
    if not m:
        return set()
    current = m.group(1)
    pcts: set[str] = set()
    for sec in _RULE_SECTIONS:
        sm = re.search(
            r"---\s*" + re.escape(sec) + r"[^-]*---(.*?)(?=\n---|\Z)",
            current, re.DOTALL,
        )
        if sm:
            pcts |= set(re.findall(r"(\d+(?:\.\d+)?)\s*%", sm.group(1)))
    return pcts


async def _fetch_bars(
    session: AsyncSession, org_id: str, symbol: str,
    timeframe: str, limit: int = 80,
) -> list[dict[str, Any]]:
    """Last `limit` bars for a symbol/timeframe from market_history,
    oldest-first, plus the prior-10-bar high/low computed from the
    full window."""
    rows = (await session.execute(sql_text("""
        SELECT bar_time, trade_date, open, high, low, close
        FROM market_history
        WHERE org_id = :oid AND symbol = :sym AND timeframe = :tf
          AND deleted_at IS NULL
          AND open IS NOT NULL AND high IS NOT NULL
          AND low IS NOT NULL AND close IS NOT NULL
        ORDER BY bar_time DESC
        LIMIT :n
    """), {"oid": org_id, "sym": symbol, "tf": timeframe,
           "n": limit + 10})).mappings().all()
    bars = [dict(r) for r in reversed(rows)]
    # rows is limit+10 (warmup for the prior-10-bar range); the
    # caller prints and passes only the last `limit` to the model.
    return bars


def _ema(closes: list[float], period: int) -> float | None:
    """Exponential moving average of `closes`, seeded with the SMA
    of the first `period` values. None if there are fewer than
    `period` closes — never guess."""
    if len(closes) < period or period <= 0:
        return None
    k = 2.0 / (period + 1.0)
    e = sum(closes[:period]) / period
    for c in closes[period:]:
        e = c * k + e * (1.0 - k)
    return e


def _ema_at(
    closes: list[float], period: int, at_index: int,
) -> float | None:
    """EMA computed using only closes up to and including at_index."""
    if at_index < 0:
        return None
    return _ema(closes[:at_index + 1], period)


def _ema_state(
    e20: float | None, e50: float | None,
) -> str:
    """Relationship between the two EMAs, as words the article may
    quote. Never asserts a state the numbers don't support."""
    if e20 is None or e50 is None:
        return "not computable (insufficient bars)"
    if e50 == 0:
        return "not computable"
    gap_pct = (e20 - e50) / e50 * 100.0
    if abs(gap_pct) < 0.5:
        return f"converging (20-EMA {gap_pct:+.2f}% vs 50-EMA)"
    if gap_pct > 0:
        return f"20-EMA above 50-EMA ({gap_pct:+.2f}%)"
    return f"20-EMA below 50-EMA ({gap_pct:+.2f}%)"


def _prior_range(
    bars: list[dict[str, Any]],
    at_index: int,
    window: int = 10,
) -> tuple[float | None, float | None]:
    """Highest high / lowest low of the `window` bars immediately
    BEFORE `at_index` — the entry bar. The process rule is a close
    beyond the prior 10-bar high at the moment of entry, so the
    range must be taken from the bars preceding the entry, not from
    the tail of the printed window."""
    if at_index <= 0 or not bars:
        return None, None
    start = max(0, at_index - window)
    prior = bars[start:at_index]
    if not prior:
        return None, None
    try:
        return (max(float(b["high"]) for b in prior),
                min(float(b["low"]) for b in prior))
    except (TypeError, ValueError, KeyError):
        return None, None


def _subject_from_skeleton_trades(trades: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The article's subject is the first trade in the TRADES set
    (the skeleton orders them newest-first from the process
    excerpt)."""
    return trades[0] if trades else None


async def build_technical_skeleton(
    session: AsyncSession,
    org_id: str,
    coord: dict[str, Any],
    process_excerpt: str = "",
) -> tuple[str, dict[str, Any] | None]:
    """Return (skeleton_text, subject_trade).

    subject_trade carries symbol/timeframe/entry/stop/exit/target
    so the caller can render the chart without a second query. It
    is None when no trade was available."""
    dim = coord.get("dimension_id", "")
    cluster = DIMENSION_CLUSTER.get(dim)

    trades = await _recent_trades(session, org_id)
    today = datetime.now(timezone.utc).date()

    # Filter to the dimension's cluster. If no cluster for this
    # dimension, or nothing matches, fall back honestly to all
    # five and state both regimes.
    filtered: list[dict[str, Any]] = []
    if cluster is not None:
        for t in trades:
            c = compute_trade(t)
            m = c.get("stop_atr_multiple")
            if m is not None and abs(float(m) - cluster) <= CLUSTER_TOLERANCE:
                filtered.append(t)
    used_fallback = not filtered
    if used_fallback:
        filtered = list(trades)

    batch = compute_batch(filtered)
    _dates = [t.get("traded_at") for t in filtered
              if isinstance(t.get("traded_at"), datetime)]
    win_start = min(_dates).date().isoformat() if _dates else "unknown"
    win_end = max(_dates).date().isoformat() if _dates else "unknown"
    low_sample = batch["n"] < 4

    lines: list[str] = []
    lines.append("TECHNICAL INDICATOR SKELETON (computed by Python; "
                 "every number the article uses must come from here)")
    lines.append("")
    lines.append(f"COORDINATE: {coord.get('coord','?')} — "
                 f"{coord.get('dimension_label','?')} / "
                 f"{coord.get('angle_label','?')}")
    lines.append(f"INDICATOR FOCUS: {coord.get('dimension_label','?')} "
                 f"({coord.get('dimension_keywords','')})")
    lines.append("")

    # ── SUBJECT: the trade this article is about ──
    subject = _subject_from_skeleton_trades(filtered)
    bars: list[dict[str, Any]] = []
    if subject is not None:
        _sym = str(subject.get("symbol") or "")
        _tf = str(subject.get("timeframe") or "1d")
        # Fetch enough bars for the 50-EMA to initialise (needs 50
        # closes); only the last 20 print and pass to the model.
        _all_bars = await _fetch_bars(session, org_id, _sym, _tf, limit=80)
        # Locate the entry bar: the bar whose time matches the
        # trade's hour, or the last bar at/before the trade time.
        _entry_dt = subject.get("traded_at")
        _entry_idx = None
        if isinstance(_entry_dt, datetime):
            for _k, _b in enumerate(_all_bars):
                _bt = _b.get("bar_time")
                if isinstance(_bt, datetime) and _bt >= _entry_dt:
                    _entry_idx = _k
                    break
            if _entry_idx is None:
                _entry_idx = len(_all_bars) - 1
        else:
            _entry_idx = len(_all_bars) - 1
        _hi, _lo = _prior_range(_all_bars, _entry_idx, window=10)
        # The chart window and the prompt get the last 20 bars; the
        # extra 10 above were warmup for the prior-10-bar range.
        bars = _all_bars[-20:] if len(_all_bars) > 20 else _all_bars
        c = compute_trade(subject)
        _d = subject.get("traded_at")
        _d_str = _d.strftime("%Y-%m-%d") if isinstance(_d, datetime) else str(_d)
        lines.append("SUBJECT TRADE (this article is about THIS trade; "
                     "the chart shows its candles):")
        lines.append(
            f"  {_d_str} | {_sym} | {subject.get('direction','?')} | "
            f"{subject.get('contracts','?')} shares | "
            f"entry {_fmt(subject.get('entry'))} | "
            f"stop {_fmt(subject.get('stop'))} | "
            f"exit {_fmt(subject.get('exit'))} | "
            f"result ${_fmt(c['result'])} | "
            f"{_fmt(c['percent_of_equity'], 4)}% of equity | "
            f"stop {_fmt(c['stop_atr_multiple'])}xATR | "
            f"R {_fmt(c['r_multiple'])}"
        )
        if _hi is not None and _lo is not None:
            lines.append(
                f"  prior 10-bar high: {_fmt(_hi)} | prior 10-bar low: {_fmt(_lo)}"
            )
        # Computed clearance beyond the prior high (long) or below
        # the prior low (short). The process requires 0.5%. Computed
        # in Python so the article cites a fact, not a division, and
        # the validator can check it.
        _entry_f = None
        try:
            _entry_f = float(subject.get("entry"))
        except (TypeError, ValueError):
            pass
        _dir = str(subject.get("direction") or "long").lower()
        if _entry_f is not None and _hi is not None and _lo is not None:
            if _dir == "long" and _hi > 0:
                _clear = (_entry_f - _hi) / _hi * 100.0
                lines.append(
                    f"  clearance beyond prior high: {_clear:+.3f}% "
                    f"(entry {_fmt(_entry_f)} vs prior high {_fmt(_hi)})"
                )
                if _clear < 0.5:
                    lines.append(
                        f"  RULE CHECK: the process requires 0.5% "
                        f"clearance; THIS TRADE CLEARED ONLY {_clear:.3f}% "
                        f"— name the shortfall, do not hide it."
                    )
            elif _dir == "short" and _lo > 0:
                _clear = (_lo - _entry_f) / _lo * 100.0
                lines.append(
                    f"  clearance below prior low: {_clear:+.3f}% "
                    f"(entry {_fmt(_entry_f)} vs prior low {_fmt(_lo)})"
                )
                if _clear < 0.5:
                    lines.append(
                        f"  RULE CHECK: the process requires 0.5% "
                        f"clearance; THIS TRADE CLEARED ONLY {_clear:.3f}% "
                        f"— name the shortfall, do not hide it."
                    )
        lines.append("")

        if bars:
            lines.append(f"BARS ({_sym} {_tf}, last {len(bars)}; "
                         "the chart shows these candles — cite an OHLC "
                         "value only if it appears here):")
            for b in bars:
                _bd = b.get("bar_time") or b.get("trade_date")
                _bd_str = (_bd.strftime("%Y-%m-%d %H:%M")
                           if hasattr(_bd, "strftime") else str(_bd))
                lines.append(
                    f"  {_bd_str} | O {_fmt(b.get('open'))} | "
                    f"H {_fmt(b.get('high'))} | "
                    f"L {_fmt(b.get('low'))} | "
                    f"C {_fmt(b.get('close'))}"
                )
            lines.append("")
        else:
            lines.append("BARS: none available for this symbol/timeframe — "
                         "do NOT describe specific candles; discuss the "
                         "indicator only.")
            lines.append("")

        # ── OVERLAY: EMAs and prior-day levels, computed at the
        # entry bar from the same bars. VWAP is NOT computable
        # (market_history has no volume) and must not be claimed.
        if _all_bars and not locals().get("_overlay_emitted"):
            # Compute the EMAs from the FULL fetched set (80 bars) so
            # EMA50 is initialisable; `bars` (20) is only what prints.
            _closes = []
            for _b in _all_bars:
                try:
                    _closes.append(float(_b["close"]))
                except (TypeError, ValueError, KeyError):
                    _closes.append(0.0)
            # The entry bar's index within the full set.
            _entry_pos = None
            if isinstance(_entry_dt, datetime):
                for _k, _b in enumerate(_all_bars):
                    _bt = _b.get("bar_time")
                    if isinstance(_bt, datetime) and _bt >= _entry_dt:
                        _entry_pos = _k
                        break
            if _entry_pos is None:
                _entry_pos = len(_all_bars) - 1
            _e20 = _ema_at(_closes, 20, _entry_pos)
            _e50 = _ema_at(_closes, 50, _entry_pos)
            lines.append("OVERLAY (computed at the entry bar):")
            lines.append(f"  20-EMA: {_fmt(_e20)}")
            lines.append(f"  50-EMA: {_fmt(_e50)}")
            lines.append(f"  state: {_ema_state(_e20, _e50)}")
            # prior day's high/low from the daily series
            _pd = (await session.execute(sql_text("""
                SELECT high, low FROM market_history
                WHERE org_id=:o AND symbol=:s AND timeframe='1d'
                  AND deleted_at IS NULL AND trade_date < :d
                ORDER BY trade_date DESC LIMIT 1
            """), {"o": org_id, "s": _sym,
                   "d": (_entry_dt.date() if isinstance(_entry_dt, datetime)
                         else None)})).first()
            if _pd:
                lines.append(f"  prior day high: {_fmt(_pd[0])} | "
                             f"prior day low: {_fmt(_pd[1])}")
            lines.append("  NOT AVAILABLE: 5-day VWAP (no volume data). "
                         "Do NOT claim a VWAP value or a VWAP relationship.")
            lines.append("")
            _overlay_emitted = True

    if used_fallback:
        lines.append("TRADE CLUSTER: no trades matched the "
                     f"{cluster}xATR cluster; citing ALL {len(filtered)} "
                     "recent trades. These mix two stop regimes — "
                     "state both, do not claim one rule.")
    else:
        lines.append(f"TRADE CLUSTER: {cluster}xATR — the article "
                     f"cites ONLY the {len(filtered)} trades below, "
                     f"all of which used a {cluster}xATR stop.")
    lines.append("")

    lines.append("TRADES (the only trades you may cite):")
    for t in filtered:
        c = compute_trade(t)
        d = t.get("traded_at")
        d_str = d.strftime("%Y-%m-%d") if isinstance(d, datetime) else str(d)
        lines.append(
            f"  - {d_str} | {t.get('symbol','?')} | "
            f"{t.get('direction','?')} | {t.get('contracts','?')} shares | "
            f"entry {_fmt(t.get('entry'))} | stop {_fmt(t.get('stop'))} | "
            f"exit {_fmt(t.get('exit'))} | "
            f"result ${_fmt(c['result'])} | "
            f"{_fmt(c['percent_of_equity'], 4)}% of equity | "
            f"stop {_fmt(c['stop_atr_multiple'])}xATR | "
            f"R {_fmt(c['r_multiple'])}"
        )
        if c.get("result_reconciles") is False:
            lines.append(
                f"    NOTE: stored result {_fmt(c['stored_result'])} "
                f"differs from computed {_fmt(c['result'])} by "
                f"${_fmt(c['result_delta'])} — cite the computed value."
            )
    lines.append("")

    lines.append("BATCH (over the trades above):")
    lines.append(f"  window: {win_start} → {win_end}")
    lines.append(f"  low_sample: {'true' if low_sample else 'false'}"
                 + (" (n<4 — do not claim the indicator has or lacks "
                    "edge from this sample)" if low_sample else ""))
    lines.append(f"  n: {batch['n']}")
    lines.append(f"  wins: {batch['wins']}  losses: {batch['losses']}")
    lines.append(f"  win_rate: {batch['win_rate']}%")
    lines.append(f"  avg_win: ${_fmt(batch['avg_win'])}")
    lines.append(f"  avg_loss: ${_fmt(batch['avg_loss'])}")
    lines.append(f"  net: ${_fmt(batch['net'])}")
    lines.append(f"  expectancy: ${_fmt(batch['expectancy'])}")
    lines.append("")

    lines.append("LIMITS (state these plainly; do not paper over them):")
    lines.append(f"  - Today is {today.isoformat()}. Every date you "
                 "cite must be on or before today. A future date is a "
                 "fabrication.")
    lines.append(f"  - Every cited trade falls between {win_start} and "
                 f"{win_end}. Do not write a date outside that range. "
                 "Do not say 'over recent weeks' or 'this month' — the "
                 "window is those days.")
    lines.append("  - Cite ONLY the trades listed above. Do not invent "
                 "a date, symbol, entry, exit, or P&L. Do not write "
                 "'22 500' with no symbol.")
    lines.append("  - Do not cite a paper (Crabel, Jegadeesh-Titman, "
                 "Ball-Brown, etc.) unless the process document names "
                 "it. Do not misstate what a paper shows.")
    lines.append("  - Do not claim a process statistic (average loser, "
                 "win rate change) that is not in the BATCH block.")
    lines.append("")

    # Rules that ARE real (current process only), quotable and
    # verifiable. A percentage the article cites must be one of
    # these, a trade result, or a batch figure.
    rule_pcts = _rule_percentages(process_excerpt)
    if rule_pcts:
        lines.append("RULES (current process; these percentages are "
                     "quotable — any other percentage is invented):")
        for pct in sorted(rule_pcts, key=lambda x: float(x)):
            lines.append(f"  - {pct}%")
        lines.append("")

    return "\n".join(lines), subject
