"""market_conditions_skeleton: structured regime data for the
market_condition subblock.

The market_condition prompt previously handed the model the
trader's raw prose block and five loose numeric readings. The
model then:
  - misattributed rules to the wrong regime (applied the
    Narrow-Breadth scan-skip while breadth was normal, applied
    the Elevated-VIX stop-widening while VIX was unremarkable),
  - presented one signal in two units as two confirmations,
  - conflated the candidate-count reading with the scan result,
  - and quoted metrics with no window, source, or as-of date.

This module lifts the trader's regimes/thresholds/adjustments
into structured blocks the model cannot misread:

  READINGS        each metric with unit, source, as-of date
  REGIMES         each regime with its threshold test and an
                  ACTIVE / INACTIVE / UNKNOWN state
  ACTIVE_RULES    only the adjustments whose regime is ACTIVE
  INACTIVE        regimes not active, with why
  TRADE_LINKAGE   trades in the window, from trade_log
  LIMITS          what this snapshot cannot tell the model

The six regime definitions mirror the trader's stored
`regimes`/`thresholds` prose. They are hardcoded here (the
trader's dashboard definition is stable) and the stored prose
is echoed beneath for transparency. A future UI can override
the constants; until then this is the single source of truth
for the tests.

States:
  ACTIVE    — every required reading is present and passes
  INACTIVE  — every required reading is present and fails
  UNKNOWN   — a required reading is absent from the snapshot
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession


# ── Baseline assumptions ─────────────────────────────────────────
# The trader's prose gives ranges, not point estimates. These are
# the midpoints, stated so the skeleton can say where they came
# from rather than implying they were measured.
BASELINE_CANDIDATE_COUNT = 2.5       # from "2-3 viable setups"
BASELINE_ADR_PCT = 1.5               # from "ADR ≈ 1.5% (24h)"
BASELINE_CORRELATION = 0.30          # from "pairwise strategy correlation ≈ 0.30"
BASELINE_SPREAD_BP = 0.9             # from "median 5-min spread ≈ 0.9 bp"
BASELINE_REALIZED_VOL = 0.80         # from "10-day realized vol ≈ 0.80 %/day"
BASELINE_RANGE_PCT = 1.5             # ADR baseline, same as above

# Persistence is not measurable from one snapshot. The window is
# what the trade-linkage query uses; it is NOT a regime-persistence
# window, and the LIMITS block says so.
TRADE_LINKAGE_DAYS = 20


def _fmt_num(v: Any) -> str:
    if v is None:
        return "unknown"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "unknown"
    if f == int(f):
        return f"{int(f)}"
    return f"{f:g}"


def _regime_states(mc: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate each regime against the snapshot's numeric readings.

    Each entry: name, state, reading, threshold, why.
    """
    vol = mc.get("realized_vol_current")
    rng = mc.get("range_current")
    spread = mc.get("spread_current")
    corr = mc.get("correlation_current")
    breadth = mc.get("breadth_current")

    out: list[dict[str, Any]] = []

    # Compressed-Volatility: 10d vol < 0.60%/day AND range < 1.2%
    if vol is not None and rng is not None:
        active = float(vol) < 0.60 and float(rng) < 1.2
        out.append({
            "name": "Compressed-Volatility",
            "state": "ACTIVE" if active else "INACTIVE",
            "reading": f"vol {_fmt_num(vol)}%/day, range {_fmt_num(rng)}%",
            "threshold": "vol < 0.60%/day AND range < 1.2%",
            "why": "" if active else "one or both conditions failed",
        })
    else:
        out.append({"name": "Compressed-Volatility", "state": "UNKNOWN",
                    "reading": "vol/range missing",
                    "threshold": "vol < 0.60%/day AND range < 1.2%",
                    "why": "reading absent from snapshot"})

    # Expanded-Volatility: 10d vol > 1.20%/day AND range > 1.8%
    if vol is not None and rng is not None:
        active = float(vol) > 1.20 and float(rng) > 1.8
        out.append({
            "name": "Expanded-Volatility",
            "state": "ACTIVE" if active else "INACTIVE",
            "reading": f"vol {_fmt_num(vol)}%/day, range {_fmt_num(rng)}%",
            "threshold": "vol > 1.20%/day AND range > 1.8%",
            "why": "" if active else "one or both conditions failed",
        })
    else:
        out.append({"name": "Expanded-Volatility", "state": "UNKNOWN",
                    "reading": "vol/range missing",
                    "threshold": "vol > 1.20%/day AND range > 1.8%",
                    "why": "reading absent from snapshot"})

    # Thin-Liquidity: spread > 1.8 bp OR slippage > 1.5 bp
    if spread is not None:
        active = float(spread) > 1.8
        # Slippage is not a column; note it.
        out.append({
            "name": "Thin-Liquidity",
            "state": "ACTIVE" if active else "INACTIVE",
            "reading": f"spread {_fmt_num(spread)} bp (slippage not tracked)",
            "threshold": "spread > 1.8 bp OR slippage > 1.5 bp",
            "why": "" if active else "spread below threshold; slippage unavailable",
        })
    else:
        out.append({"name": "Thin-Liquidity", "state": "UNKNOWN",
                    "reading": "spread missing",
                    "threshold": "spread > 1.8 bp OR slippage > 1.5 bp",
                    "why": "reading absent from snapshot"})

    # High-Correlation: correlation > 0.50 (entry only; no exit
    # threshold in the trader's prose — see LIMITS)
    if corr is not None:
        active = float(corr) > 0.50
        out.append({
            "name": "High-Correlation",
            "state": "ACTIVE" if active else "INACTIVE",
            "reading": f"correlation {_fmt_num(corr)}",
            "threshold": "correlation > 0.50 (entry; exit threshold not defined)",
            "why": "" if active else "correlation below 0.50",
        })
    else:
        out.append({"name": "High-Correlation", "state": "UNKNOWN",
                    "reading": "correlation missing",
                    "threshold": "correlation > 0.50",
                    "why": "reading absent from snapshot"})

    # Narrow-Breadth: candidate count < 0.4 × 50-day avg count.
    # The 50-day avg is not a column; derive from the trader's
    # baseline range "2-3" → midpoint 2.5, stated explicitly.
    if breadth is not None:
        cutoff = 0.4 * BASELINE_CANDIDATE_COUNT
        active = float(breadth) < cutoff
        out.append({
            "name": "Narrow-Breadth",
            "state": "ACTIVE" if active else "INACTIVE",
            "reading": f"candidate count {_fmt_num(breadth)}",
            "threshold": (f"count < 0.4 × 50-day avg "
                          f"(avg ≈ {_fmt_num(BASELINE_CANDIDATE_COUNT)} "
                          f"from trader baseline '2-3' → cutoff "
                          f"{_fmt_num(cutoff)})"),
            "why": "" if active else f"count {_fmt_num(breadth)} ≥ cutoff {_fmt_num(cutoff)}",
        })
    else:
        out.append({"name": "Narrow-Breadth", "state": "UNKNOWN",
                    "reading": "candidate count missing",
                    "threshold": "count < 0.4 × 50-day avg",
                    "why": "reading absent from snapshot"})

    # Elevated-Volatility-Index: VIX > 20-day avg + 2σ
    # No VIX reading column exists.
    out.append({
        "name": "Elevated-Volatility-Index",
        "state": "UNKNOWN",
        "reading": "volatility-index spread not tracked in snapshot",
        "threshold": "VIX > 20-day avg + 2σ",
        "why": "reading absent from snapshot",
    })

    # Crowded-Longs: positioning survey net-long > 80%
    # No positioning reading column exists.
    out.append({
        "name": "Crowded-Longs",
        "state": "UNKNOWN",
        "reading": "positioning survey not tracked in snapshot",
        "threshold": "net-long > 80%",
        "why": "reading absent from snapshot",
    })

    return out


# ── Adjustments, keyed to the regime that activates them ─────────
ADJUSTMENTS_BY_REGIME: dict[str, str] = {
    "Compressed-Volatility": (
        "cut base size to 0.5 unit, tighten stops to 0.5%, suspend "
        "the 0.75% stop-loss rule"
    ),
    "Expanded-Volatility": (
        "keep full size, raise the profit target to 2%, widen the "
        "trailing stop to 0.7%"
    ),
    "Thin-Liquidity": (
        "use limit orders, split orders into ≤0.25-unit chunks, "
        "avoid trades that would exceed a 0.5 bp slippage estimate"
    ),
    "High-Correlation": (
        "cap total concurrent units at 3, skip the lowest-edge "
        "breakout signal"
    ),
    "Narrow-Breadth": (
        "skip the day's scan entirely unless the volatility index "
        "exceeds its 20-day average by 2σ AND the positioning "
        "survey is below 70% net-long"
    ),
    "Elevated-Volatility-Index": (
        "raise the stop-loss to 1%, trade only the breakout "
        "micro-strategy"
    ),
    "Crowded-Longs": (
        "exit any open long at market close, ban new longs until "
        "the positioning survey falls below 75% net-long"
    ),
}


async def _trade_linkage(
    session: AsyncSession, org_id: str, as_of: datetime,
) -> dict[str, Any]:
    """Trades in the window that APPEARED IN AN ARTICLE.

    trade_log is entirely synthetic-generated and none of it is
    manually entered. The only subset with narrative meaning is
    the set that became article subjects (used_in_article_id is
    not null) — "trades that appeared in my notes," not "trades
    I took." The LIMITS block says so, so the article cannot
    overclaim.
    """
    start = as_of - timedelta(days=TRADE_LINKAGE_DAYS)
    rows = (await session.execute(sql_text("""
        SELECT symbol, setup, result, direction
        FROM trade_log
        WHERE org_id = :oid AND deleted_at IS NULL
          AND used_in_article_id IS NOT NULL
          AND traded_at >= :start AND traded_at <= :as_of
        ORDER BY traded_at
    """), {"oid": org_id, "start": start, "as_of": as_of})).mappings().all()
    trades = [dict(r) for r in rows]
    wins = sum(1 for t in trades if (t.get("result") or 0) > 0)
    losses = sum(1 for t in trades if (t.get("result") or 0) < 0)
    net = sum(float(t.get("result") or 0) for t in trades)
    # Top symbols by frequency, capped — the full list is noise.
    freq: dict[str, int] = {}
    for t in trades:
        s = str(t.get("symbol") or "")
        if s:
            freq[s] = freq.get(s, 0) + 1
    top = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    # Per-symbol P&L, so the article can say which names lost.
    per_sym: dict[str, dict[str, float]] = {}
    for t in trades:
        sym = str(t.get("symbol") or "")
        if not sym:
            continue
        d = per_sym.setdefault(sym, {"n": 0, "net": 0.0})
        d["n"] += 1
        d["net"] += float(t.get("result") or 0)
    per_sym_top = sorted(
        per_sym.items(), key=lambda kv: kv[1]["net"]
    )[:5]   # worst first
    return {
        "window_days": TRADE_LINKAGE_DAYS,
        "start": start.date().isoformat(),
        "as_of": as_of.date().isoformat(),
        "n_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "net": net,
        "top_symbols": top,
        "per_symbol": per_sym_top,
    }


async def build_conditions_skeleton(
    session: AsyncSession,
    org_id: str,
    mc: dict[str, Any],
) -> str:
    """Return the structured skeleton the market_condition prompt
    quotes. Caller is responsible for passing a non-empty mc.
    """
    created = mc.get("created_at")
    if isinstance(created, datetime):
        as_of = created.astimezone(timezone.utc)
        as_of_str = as_of.strftime("%Y-%m-%d %H:%M UTC")
    else:
        as_of = datetime.now(timezone.utc)
        as_of_str = "unknown (snapshot timestamp missing)"

    states = _regime_states(mc)
    active = [s for s in states if s["state"] == "ACTIVE"]
    inactive = [s for s in states if s["state"] == "INACTIVE"]
    unknown = [s for s in states if s["state"] == "UNKNOWN"]

    lines: list[str] = []
    lines.append("MARKET CONDITIONS SKELETON (computed by Python; "
                 "every number the article uses must come from here)")
    lines.append("")
    lines.append(f"AS-OF: {as_of_str}")
    lines.append(f"SNAPSHOT ID: {mc.get('id', 'unknown')}")
    lines.append("")

    # ── READINGS ──
    lines.append("READINGS (the only metrics you may cite):")
    src = (mc.get("metric_source") or "").strip()
    lines.append(f"  realized_vol_current: {_fmt_num(mc.get('realized_vol_current'))} %/day"
                 f"   (10-day standard deviation of 5-min returns)")
    lines.append(f"  spread_current: {_fmt_num(mc.get('spread_current'))} bp"
                 f"   (median 5-min mid-quote width)")
    lines.append(f"  range_current: {_fmt_num(mc.get('range_current'))} %"
                 f"   (average daily range)")
    lines.append(f"  correlation_current: {_fmt_num(mc.get('correlation_current'))}"
                 f"   (Pearson, intraday returns, primary vs reference)")
    lines.append(f"  breadth_current: {_fmt_num(mc.get('breadth_current'))}"
                 f"   (candidate count from the scan)")
    if src:
        lines.append(f"  metric_source: {src}")
    lines.append("  NOT TRACKED in this snapshot: volatility-index (VIX) "
                 "reading; positioning survey net-long %; slippage.")
    lines.append("")
    # ── BASELINES ──
    # The trader's stored baselines are what make a reading
    # interpretable ("0.42 vs 0.80 normal"). The article may cite
    # these; without them the metric check rejected a correct
    # baseline comparison (b34ff13d: "0.42 against my baseline of
    # 0.80").
    lines.append("BASELINES (the trader's normal-day values; cite these "
                 "when comparing a reading):")
    lines.append(f"  realized_vol_baseline: {_fmt_num(BASELINE_REALIZED_VOL)} %/day")
    lines.append(f"  range_baseline: {_fmt_num(BASELINE_RANGE_PCT)} %")
    lines.append(f"  spread_baseline: {_fmt_num(BASELINE_SPREAD_BP)} bp")
    lines.append(f"  correlation_baseline: {_fmt_num(BASELINE_CORRELATION)}")
    lines.append(f"  candidate_count_baseline: {_fmt_num(BASELINE_CANDIDATE_COUNT)}")
    lines.append("")

    # ── REGIMES ──
    lines.append("REGIMES (each state is computed, not inferred):")
    for s in states:
        line = f"  {s['name']}: {s['state']}"
        if s["reading"]:
            line += f"  | reading: {s['reading']}"
        if s["state"] == "UNKNOWN" and s["why"]:
            line += f"  | {s['why']}"
        lines.append(line)
    lines.append("")
    lines.append("  Threshold detail:")
    for s in states:
        lines.append(f"    - {s['name']}: {s['threshold']}")
    lines.append("")

    # ── ACTIVE RULES ──
    lines.append("ACTIVE_RULES (the only process adjustments in force "
                 "for this snapshot):")
    if active:
        for s in active:
            rule = ADJUSTMENTS_BY_REGIME.get(s["name"], "")
            lines.append(f"  [{s['name']}] {rule}")
    else:
        lines.append("  (none — no regime is currently active)")
    lines.append("")

    # ── RULE CONFLICTS ──
    # The trader's stored adjustments can contain mutually
    # exclusive rules. The clearest case: "tighten stops to 0.5%"
    # and "suspend the 0.75% stop-loss rule" in the same regime —
    # a stop cannot be tightened and suspended at once. Surface it
    # so the article resolves it (one stop policy governs) rather
    # than quoting both.
    _conflicts: list[str] = []
    for s in active:
        rule = ADJUSTMENTS_BY_REGIME.get(s["name"], "").lower()
        _tightens = ("tighten" in rule and "stop" in rule)
        _suspends = ("suspend" in rule and "stop" in rule)
        if _tightens and _suspends:
            _conflicts.append(
                f"{s['name']}: the stored adjustment both tightens "
                f"stops and suspends the stop-loss rule — these are "
                f"mutually exclusive. State which one governs; do "
                f"not present both as active."
            )
    if _conflicts:
        # Do not merely flag the conflict and leave the model to
        # resolve it — the model "resolved" it by writing "I tighten
        # stops to 0.5% while suspending the 0.75% rule, because the
        # conflict resolves in favor of the tighter stop", which
        # still contains both and resolves nothing. State the
        # resolution as a fact the article quotes.
        lines.append("RESOLVED_RULE (state this; do NOT restate the raw "
                     "conflict):")
        for s in active:
            rule = ADJUSTMENTS_BY_REGIME.get(s["name"], "").lower()
            if "tighten" in rule and "stop" in rule and "suspend" in rule and "stop" in rule:
                lines.append(
                    f"  [{s['name']}] the tightened 0.5% stop REPLACES the "
                    f"0.75% stop-loss rule AND supersedes the 1.75xATR stop "
                    f"in this regime. The 0.75% rule is NOT suspended — it "
                    f"is superseded. The 1.75xATR stop governs in normal "
                    f"volatility; the 0.5% fixed stop governs in "
                    f"Compressed-Volatility. State which stop governs in "
                    f"which regime; do not write 'suspend'."
                )
        lines.append("")

    # ── INACTIVE ──
    lines.append("INACTIVE_REGIMES (name these as NOT in force; do not "
                 "apply their rules):")
    if inactive:
        for s in inactive:
            lines.append(f"  - {s['name']}: {s['reading']} "
                         f"({s['why'] or 'threshold not met'})")
    else:
        lines.append("  (none)")
    lines.append("")

    if unknown:
        lines.append("UNKNOWN_REGIMES (the snapshot cannot assess these; "
                     "say so, do not guess):")
        for s in unknown:
            lines.append(f"  - {s['name']}: {s['why']}")
        lines.append("")

    # ── TRADE LINKAGE ──
    linkage = await _trade_linkage(session, org_id, as_of)
    lines.append("TRADE LINKAGE (trades that appeared in my notes "
                 "in the window ending at the snapshot date):")
    lines.append(f"  window: {linkage['start']} → {linkage['as_of']} "
                 f"({linkage['window_days']} days)")
    lines.append(f"  window_start: {linkage['start']}")
    lines.append(f"  window_end: {linkage['as_of']}"
                 f"  (state both dates in the article)")
    if linkage["n_trades"] == 0:
        lines.append("  n_trades: 0 (no article-linked trades in the "
                     "window — say so; do not invent a sample)")
    else:
        lines.append(f"  n_trades: {linkage['n_trades']} "
                     f"(wins {linkage['wins']}, losses "
                     f"{linkage['losses']}, net "
                     f"${linkage['net']:+.2f})")
        if linkage["top_symbols"]:
            _ts = ", ".join(f"{s} ({n})" for s, n in linkage["top_symbols"])
            lines.append(f"  top symbols: {_ts}")
        if linkage.get("per_symbol"):
            lines.append("  per_symbol_pnl (worst first; state which names "
                         "lost):")
            for _sym, _d in linkage["per_symbol"]:
                lines.append(f"    {_sym}: {int(_d['n'])} trades, "
                             f"net ${_d['net']:+.2f}")

        # Derived analysis — the reviewer's top omission. Raw
        # counts are not analysis; win rate, expectancy, and
        # drawdown are, and the article must state them.
        _n = linkage["n_trades"]
        _w = linkage["wins"]
        _l = linkage["losses"]
        _net = linkage["net"]
        _wr = (_w / _n * 100.0) if _n else 0.0
        _exp = (_net / _n) if _n else 0.0
        _dd = (_net / 200000.0 * 100.0) if _n else 0.0
        lines.append("  DERIVED ANALYSIS (state these; do not stop at "
                     "the raw counts):")
        lines.append(f"    win_rate_pct: {_wr:.2f}%")
        # Expectancy at full precision, not rounded: 678 trades at
        # -$163.48 quoted against a net of -$110,842.73 implies
        # -$110,839.44 (off $3.29). State the exact quotient.
        lines.append(f"    expectancy_per_trade: ${_net / _n:+.4f}"
                     f" (quote to 2dp: ${_net / _n:+.2f})")
        lines.append(f"    equity_impact_pct: {_dd:+.1f}% of $200,000 "
                     f"starting equity")
        # R per trade: net / cumulative planned risk at 1.5% per trade
        _cum_risk = _n * 0.015 * 200000.0
        _r_per_trade = (_net / _cum_risk) if _cum_risk else 0.0
        lines.append(f"    r_per_trade: {_r_per_trade:+.4f}R "
                     f"(net / cumulative 1.5% planned risk)")
        # 95% CI on the win rate (normal approximation)
        _se = ((_wr / 100.0) * (1 - _wr / 100.0) / _n) ** 0.5 if _n else 0.0
        _ci = 1.96 * _se * 100.0
        lines.append(f"    win_rate_95ci_pp: +/- {_ci:.1f}pp "
                     f"(n={_n}; state the interval or say the edge is "
                     f"statistically conclusive)")
        if _dd <= -20.0:
            lines.append("    WARNING: the window's net loss exceeds 20% "
                         "of starting equity. State the drawdown plainly "
                         "and reconcile it against the 1.5% per-trade "
                         "budget — a budget that size cannot produce a "
                         "drawdown this large without either not being "
                         "enforced or the sample including pre-rule "
                         "trades. Say which — pick one branch and commit "
                         "to it: 'the budget was not enforced in this "
                         "sample' OR 'the sample includes pre-rule trades'. "
                         "Do not write 'either/or'.")
        lines.append("  note: these are synthetic-generated trades that "
                     "appeared in articles — describe them as 'trades "
                     "that appeared in my notes', not as trades you "
                     "personally executed. This is the generated trade "
                     "universe, not the one-or-two example trades in the "
                     "notes; do not imply they are the same strategy.")
    lines.append("")

    # ── UNIT DEFINITION ──
    lines.append("UNIT_DEFINITION (state this when you write 'unit'):")
    lines.append("  1 unit = the full position at the 1.5% risk budget "
                 "on $200,000 equity. 0.5 unit = half that position. "
                 "3 units = the concurrent-position cap. Do not use "
                 "'unit' without this definition.")
    lines.append("")

    # ── LIMITS ──
    lines.append("LIMITS (state these plainly; do not paper over them):")
    lines.append("  - Hysteresis: the trader's thresholds define ENTRY "
                 "only. There is no exit band. Do not claim the regime "
                 "will persist or has persisted for N days.")
    lines.append("  - Persistence: this is a SINGLE snapshot. There is "
                 "no history in the block, so do not write "
                 "correlation-been-above-0.50-for-N-days.")
    lines.append("  - Base rate / conditional expectancy: not computed. "
                 "Do not claim the regime predicts returns; the "
                 "trade-linkage count is the only performance evidence.")
    lines.append("  - VIX and positioning readings are absent. If you "
                 "mention those regimes, say the reading is unavailable.")
    lines.append("  - The trade record is synthetic-generated and "
                 "article-linked. Say 'trades that appeared in my notes', "
                 "never 'trades I executed' or 'my fills'.")
    lines.append("")
    return "\n".join(lines)
