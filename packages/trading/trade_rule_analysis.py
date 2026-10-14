"""Pre-compute every rule's effect on each recent trade.

The LLM cannot do arithmetic on trades it did not compute itself.
It writes plausible formulas and false "what-if" claims. This
module does the arithmetic in Python and hands the model a table
of facts.

Rules parsed from the process document:
  - 10-day breakout clearance minimum (from daily_scan)
  - 5-day ATR entry threshold (from daily_scan)
  - Base contracts, cap, risk_dollars, scale threshold (from
    position_sizing)
  - Stop distance in ATR multiples, target in ATR multiples,
    reversal liquidation threshold (from exit_logic)

For each recent trade we compute a verdict per rule:
  - did the trade violate the rule?
  - would the rule have rejected the trade?
  - would the stop have triggered?
  - with the stop in place, what would the loss have been?
  - what does the sizing formula produce, and does the cap bind?
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


DOLLARS_PER_POINT = {
    "NQ": 20, "MNQ": 2,
    "ES": 50, "MES": 5,
    "GC": 100, "MGC": 10,
    "CL": 1000, "MCL": 100,
    "ZB": 1000, "ZN": 1000,
    "RTY": 50, "M2K": 5,
    "YM": 5, "MYM": 0.5,
}


_WORDS_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _parse_number_or_word(text: str) -> float | None:
    """Extract a digit or a spelled-out number from text.
    'base position is one' → 1
    'cap at five contracts' → 5
    """
    m = re.search(r"(\d+(?:\.\d+)?)", text or "")
    if m:
        return float(m.group(1))
    m = re.search(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b",
        text or "", re.IGNORECASE,
    )
    if m:
        return float(_WORDS_TO_NUM[m.group(1).lower()])
    return None


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _search_float(text: str, pattern: str) -> float | None:
    m = re.search(pattern, text or "", re.IGNORECASE)
    if not m:
        return None
    try:
        # Strip thousands separators before parsing. "2,000" is a
        # number in prose, not a tuple.
        return float(m.group(1).replace(",", ""))
    except (ValueError, IndexError, AttributeError):
        return None


def parse_process_rules(process: dict[str, str]) -> dict[str, Any]:
    """Parse every threshold and formula constant from the process
    document. Missing values are None.
    """
    scan = process.get("daily_scan", "")
    entry = process.get("entry_logic", "")
    sizing = process.get("position_sizing", "")
    exit_ = process.get("exit_logic", "")

    # Breakout clearance minimum, in percent. "at least 0.5 %"
    breakout_min_pct = _search_float(
        scan, r"at least\s+(\d+(?:\.\d+)?)\s*%"
    )
    if breakout_min_pct is None:
        breakout_min_pct = _search_float(
            entry, r"exceeds?[^.]{0,40}?by\s+(\d+(?:\.\d+)?)\s*%"
        )

    # ATR entry threshold, dollars. "ATR above $30"
    atr_entry_threshold = _search_float(
        scan, r"atr\s*above\s*\$?(\d+(?:\.\d+)?)"
    )
    if atr_entry_threshold is None:
        atr_entry_threshold = _search_float(
            scan, r"average true range[^.]{0,40}?\$?(\d+(?:\.\d+)?)"
        )

    # Position sizing: base, cap, risk_dollars, scale threshold
    # Base position may be written as a digit or a word.
    base_match = re.search(
        r"base\s+position\s+is\s+(\S+)",
        sizing or "", re.IGNORECASE,
    )
    base_contracts = None
    if base_match:
        base_contracts = _parse_number_or_word(base_match.group(1))

    # Cap may also be written as a digit or a word.
    cap_match = re.search(
        r"cap\s+total\s+exposure\s+at\s+(\S+)",
        sizing or "", re.IGNORECASE,
    )
    max_contracts = None
    if cap_match:
        max_contracts = _parse_number_or_word(cap_match.group(1))

    # risk_dollars: multiple patterns in case of Unicode variation
    risk_dollars = None
    for pattern in (
        r"risk\s+per\s+trade\s*\([^)]*?(\d[\d,]*)\)",
        r"(\d[\d,]*)\s*\)\s*keeps",
        r"≈\s*\$?(\d[\d,]*)",
        r"\$\s*(\d[\d,]*)\s*\)",
    ):
        v = _search_float(sizing, pattern)
        if v is not None:
            risk_dollars = float(str(v).replace(",", ""))
            break

    scale_atr_threshold = _search_float(
        sizing, r"scaling\s+to\s+\w+\s+contracts\s+is\s+allowed\s+only\s+when"
               r"[^.]{0,60}?atr\s+is\s+below\s+\$?(\d+(?:\.\d+)?)"
    )

    # Stop and target multiples, in ATR.
    stop_atr_multiple: float | None = None
    stop_match = re.search(
        r"stop\s*loss[^.]{0,60}?(\d+(?:\.\d+)?)\s*[×x*]\s*atr",
        exit_ or "",
        re.IGNORECASE,
    )
    if stop_match:
        stop_atr_multiple = float(stop_match.group(1))

    target_atr_multiple: float | None = None
    target_match = re.search(
        r"(?:profit\s+)?target[^.]{0,60}?(\d+(?:\.\d+)?)\s*[×x*]\s*atr",
        exit_ or "",
        re.IGNORECASE,
    )
    if target_match:
        target_atr_multiple = float(target_match.group(1))

    # Reversal liquidation threshold: "reverses 0.5 % against me"
    reversal_pct = _search_float(
        exit_, r"reverses?\s+(\d+(?:\.\d+)?)\s*%"
    )

    return {
        "breakout_min_pct": breakout_min_pct,
        "atr_entry_threshold": atr_entry_threshold,
        "base_contracts": base_contracts,
        "max_contracts": max_contracts,
        "risk_dollars": risk_dollars,
        "scale_atr_threshold": scale_atr_threshold,
        "stop_atr_multiple": stop_atr_multiple,
        "target_atr_multiple": target_atr_multiple,
        "reversal_pct": reversal_pct,
    }


def _parse_trade_clearance(setup: str) -> float | None:
    """Extract the clearance percent from a setup string, e.g.
    '10-day high breakout, 0.7% clearance' → 0.7."""
    return _search_float(setup or "", r"(\d+(?:\.\d+)?)\s*%\s*clearance")


def _parse_trade_atr_from_setup(setup: str) -> float | None:
    """Extract the ATR if it appears in the setup string."""
    return _search_float(setup or "", r"atr\s*[≈$]?\s*\$?(\d+(?:\.\d+)?)")


def analyze_trade(trade: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Compute every rule's verdict for this trade. All arithmetic
    happens here. The model quotes the output.
    """
    inst = (trade.get("instrument") or "").upper()
    dpp = DOLLARS_PER_POINT.get(inst, 1)
    direction = (trade.get("direction") or "long").lower()

    entry = _num(trade.get("entry"))
    exit_p = _num(trade.get("exit"))
    atr = _num(trade.get("atr_at_entry"))
    contracts = trade.get("contracts") or 0
    pnl = _num(trade.get("result")) or 0
    setup = trade.get("setup") or ""

    facts: dict[str, Any] = {
        "instrument": inst,
        "direction": direction,
        "entry": entry,
        "exit": exit_p,
        "atr": atr,
        "contracts": contracts,
        "result_dollars": pnl,
        "trade_outcome": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "SCRATCH"),
        "setup": setup,
    }

    trade_clearance = _parse_trade_clearance(setup)
    if trade_clearance is None and atr is None:
        trade_clearance = _parse_trade_atr_from_setup(setup)

    # ── Rule: breakout clearance ≥ N% ─────────────────────────────
    min_pct = rules.get("breakout_min_pct")
    if min_pct is not None and trade_clearance is not None:
        honored = trade_clearance >= min_pct
        facts["breakout_clearance"] = {
            "rule_min_pct": min_pct,
            "trade_pct": trade_clearance,
            "honored": honored,
            "note": (
                f"Rule 'breakout clearance ≥ {min_pct}%': "
                f"trade clearance {trade_clearance}% — "
                f"{'rule HONORED' if honored else 'rule VIOLATED (trade should not have been taken)'}"
            ),
        }

    # ── Rule: 5-day ATR < $N entry filter (rejects ATR above $N) ──
    atr_thr = rules.get("atr_entry_threshold")
    if atr_thr is not None and atr is not None:
        triggered = atr > atr_thr
        facts["atr_filter"] = {
            "threshold": atr_thr,
            "atr": atr,
            "would_reject": triggered,
            "note": (
                f"Rule 'ATR < ${atr_thr} entry filter': "
                f"trade ATR {atr} — "
                f"{'WOULD have rejected the trade' if triggered else 'would have ALLOWED the trade'}"
            ),
        }

    # ── Rule: position size = floor(risk / (ATR × dpp)) ───────────
    risk_dollars = rules.get("risk_dollars")
    max_c = rules.get("max_contracts")
    scale_thr = rules.get("scale_atr_threshold")
    if risk_dollars and atr:
        formula_contracts = int(risk_dollars // (atr * dpp))
        effective = formula_contracts
        cap_note = ""
        if max_c is not None and formula_contracts > max_c:
            effective = int(max_c)
            cap_note = f" (capped at {int(max_c)} by the process cap)"
        facts["sizing"] = {
            "risk_dollars": risk_dollars,
            "atr": atr,
            "dollars_per_point": dpp,
            "formula_contracts": formula_contracts,
            "max_cap": max_c,
            "effective_contracts": effective,
            "actual_contracts": contracts,
            "note": (
                f"Rule 'size = floor(${risk_dollars:.0f} / "
                f"(ATR × ${dpp}))': floor({risk_dollars:.0f} / "
                f"({atr} × {dpp})) = floor({risk_dollars / (atr * dpp):.2f}) "
                f"= {formula_contracts} contracts{cap_note}. "
                f"For stop-loss math use {contracts} "
                f"(actual contracts on THIS trade), NOT "
                f"{formula_contracts} (the formula output)."
            ),
        }

    # ── Rule: 0.75×ATR stop ───────────────────────────────────────
    stop_mult = rules.get("stop_atr_multiple")
    if stop_mult is not None and atr and entry:
        stop_points = stop_mult * atr
        computed_stop = (
            entry - stop_points if direction == "long" else entry + stop_points
        )

        if pnl > 0:
            note = (
                f"Rule '{stop_mult}×ATR stop': cannot evaluate. Trade "
                f"closed in profit. The trade log does not record "
                f"intra-trade drawdown, so we cannot say whether the "
                f"stop would have triggered."
            )
            facts["stop"] = {
                "rule_name": f"{stop_mult}×ATR stop",
                "stop_price": round(computed_stop, 2),
                "note": note,
            }
        elif pnl < 0 and exit_p is not None:
            adverse = (
                (entry - exit_p) if direction == "long" else (exit_p - entry)
            )
            would_trigger = adverse >= stop_points
            if would_trigger:
                stop_loss_dollars = stop_points * contracts * dpp
                note = (
                    f"Rule '{stop_mult}×ATR stop': stop at "
                    f"{computed_stop:.2f} ({stop_points:.2f} pts below "
                    f"entry). Actual adverse move was {adverse:.2f} pts. "
                    f"The stop WOULD have triggered. With the stop in "
                    f"place, the loss would have been "
                    f"({stop_points:.2f} pts) × {contracts} contracts × "
                    f"${dpp} = ${stop_loss_dollars:.0f}, not ${abs(pnl):.0f}."
                )
                facts["stop"] = {
                    "rule_name": f"{stop_mult}×ATR stop",
                    "stop_price": round(computed_stop, 2),
                    "stop_points": round(stop_points, 2),
                    "adverse_points": round(adverse, 2),
                    "would_trigger": True,
                    "stop_loss_dollars": round(stop_loss_dollars, 2),
                    "actual_loss_dollars": round(abs(pnl), 2),
                    "savings_dollars": round(abs(pnl) - stop_loss_dollars, 2),
                    "note": note,
                }
            else:
                note = (
                    f"Rule '{stop_mult}×ATR stop': stop at "
                    f"{computed_stop:.2f} ({stop_points:.2f} pts below "
                    f"entry). Actual adverse move was {adverse:.2f} pts — "
                    f"below the stop. The stop would NOT have triggered. "
                    f"This rule would NOT have changed the outcome."
                )
                facts["stop"] = {
                    "rule_name": f"{stop_mult}×ATR stop",
                    "stop_price": round(computed_stop, 2),
                    "stop_points": round(stop_points, 2),
                    "adverse_points": round(adverse, 2),
                    "would_trigger": False,
                    "note": note,
                }
        else:
            facts["stop"] = {
                "rule_name": f"{stop_mult}×ATR stop",
                "note": f"Rule '{stop_mult}×ATR stop': cannot evaluate — scratch.",
            }

    return facts


async def get_rule_analysis(
    session: AsyncSession, org_id: str, limit: int = 5,
    include_synthetic: bool = False,
) -> str:
    """Read the process document + last N trades. Return a
    pre-computed analysis block the prompt can quote verbatim.
    """
    proc_row = (await session.execute(sql_text("""
        SELECT daily_scan, entry_logic, position_sizing, exit_logic,
               holding_period, research_to_rules, chart_overlay,
               no_backtest_rule, multi_strategy_blend,
               rolling, stocks_vs_options, universes, emotion_removal,
               fundamental_metrics, trade_history_summary
        FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
    """), {"oid": org_id})).first()
    if not proc_row:
        return ""

    cols = (
        "daily_scan", "entry_logic", "position_sizing", "exit_logic",
        "holding_period", "research_to_rules", "chart_overlay",
        "no_backtest_rule", "multi_strategy_blend",
        "rolling", "stocks_vs_options", "universes", "emotion_removal",
        "fundamental_metrics", "trade_history_summary",
    )
    process = dict(zip(cols, [str(x or "") for x in proc_row]))

    rules = parse_process_rules(process)

    trades_rows = (await session.execute(sql_text("""
        SELECT traded_at, instrument, direction, contracts,
               entry, stop, exit, result, atr_at_entry, setup
        FROM trade_log
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY traded_at DESC
        LIMIT :n
    """), {"oid": org_id, "n": limit})).all()

    if not trades_rows and not include_synthetic:
        return ""

    tcols = (
        "traded_at", "instrument", "direction", "contracts",
        "entry", "stop", "exit", "result", "atr_at_entry", "setup",
    )
    trades = [dict(zip(tcols, r)) for r in trades_rows]

    if include_synthetic:
        from packages.trading.trader_process import load_synthetic_trades
        synth = load_synthetic_trades()
        # Cap synthetic at 3, keep real trades first, no re-sort.
        for t in synth[:3]:
            trades.append({
                "traded_at": t["traded_at"],
                "instrument": t.get("instrument", "NQ"),
                "direction": t.get("direction", "long"),
                "contracts": t.get("contracts"),
                "entry": t.get("entry"),
                "stop": t.get("stop"),
                "exit": t.get("exit"),
                "result": t.get("result"),
                "atr_at_entry": t.get("atr_at_entry"),
                "setup": t.get("setup", ""),
                "_synthetic": True,
            })

    if not trades:
        return ""

    lines = ["=== TRADE RULE ANALYSIS (pre-computed) ==="]
    lines.append(
        "These facts were computed in Python. Quote them verbatim. "
        "Do NOT recompute. Do NOT invent a what-if that contradicts "
        "these facts. Rules not covered here must not be claimed."
    )
    lines.append("")

    for trade in trades:
        date = trade["traded_at"].strftime("%Y-%m-%d") if trade["traded_at"] else "?"
        inst = trade["instrument"]
        pnl = _num(trade.get("result")) or 0
        outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "SCRATCH")
        synth_mark = " (synthetic)" if trade.get("_synthetic") else ""
        lines.append(
            f"--- Trade {date}{synth_mark} | {inst} | {outcome} "
            f"({'+' if pnl >= 0 else ''}${pnl:.0f}) ---"
        )

        facts = analyze_trade(trade, rules)

        for key in ("breakout_clearance", "atr_filter", "sizing", "stop"):
            if key in facts:
                lines.append(f"  {facts[key]['note']}")

        lines.append("")

    return "\n".join(lines)
