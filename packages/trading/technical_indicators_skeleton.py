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
        SELECT symbol, direction, contracts, entry, stop, exit,
               result, atr_at_entry, traded_at, setup
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


async def build_technical_skeleton(
    session: AsyncSession,
    org_id: str,
    coord: dict[str, Any],
    process_excerpt: str = "",
) -> str:
    """Return the structured skeleton for this coordinate."""
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

    return "\n".join(lines)
