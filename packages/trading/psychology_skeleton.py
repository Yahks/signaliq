"""psychology_skeleton: the incident reconciliation the article quotes.

trading_psychology articles previously listed incidents from prose
narrative with no reconciliation: no compliant-vs-violation split,
no per-trade comparison, no violation rate. The reviewer's #1 gap.

psychology_trade_log carries a `reading_key` per incident that names
what happened. This module classifies each key as compliant or a
violation, computes the split, and emits a skeleton block the
article quotes verbatim.

Classification (from the narrative text of each key, not the name):
  compliant:   followed_rule (rule followed), partial_profit
               (scaled out at a planned level — a disciplined act)
  violation:   quick_profit (cut a winner early), size_creep_after_win,
               broke_rule, cut_early_on_reversal, post_loss_reentry
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

COMPLIANT_KEYS = {"followed_rule", "partial_profit"}
VIOLATION_KEYS = {
    "quick_profit", "size_creep_after_win", "broke_rule",
    "cut_early_on_reversal", "post_loss_reentry",
}


async def load_incidents(
    session: AsyncSession, org_id: str,
) -> list[dict[str, Any]]:
    rows = (await session.execute(sql_text("""
        SELECT id, traded_at, instrument, direction, contracts,
               entry, stop, exit, exit_reason, result, reading_key,
               narrative
        FROM psychology_trade_log
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY traded_at
    """), {"oid": org_id})).mappings().all()
    return [dict(r) for r in rows]


def _classify(key: str | None) -> str:
    k = (key or "").strip()
    if k in COMPLIANT_KEYS:
        return "compliant"
    if k in VIOLATION_KEYS:
        return "violation"
    return "unclassified"


async def build_psychology_skeleton(
    session: AsyncSession, org_id: str, subject_id: str | None = None,
) -> str:
    incidents = await load_incidents(session, org_id)
    if not incidents:
        return "PSYCHOLOGY SKELETON (no incidents recorded): write from "\
               "process only; do not invent an incident or a P&L."

    # Every incident must classify. An unknown reading_key means the
    # log gained a category this module does not know — at 200
    # articles/week that would silently produce a skeleton with an
    # "unclassified" hole. Fail instead.
    _unknown = sorted({(i["reading_key"] or "(none)")
                       for i in incidents
                       if _classify(i["reading_key"]) == "unclassified"})
    if _unknown:
        raise ValueError(
            "psychology_skeleton: unclassified reading_key(s) "
            f"{_unknown}. Add them to COMPLIANT_KEYS or VIOLATION_KEYS "
            "before generating."
        )

    comp = [i for i in incidents if _classify(i["reading_key"]) == "compliant"]
    viol = [i for i in incidents if _classify(i["reading_key"]) == "violation"]
    uncl: list[dict[str, Any]] = []

    def _net(xs): return sum(float(i["result"] or 0) for i in xs)
    def _avg(xs): return (_net(xs) / len(xs)) if xs else 0.0

    lines: list[str] = []
    lines.append("PSYCHOLOGY SKELETON (computed by Python; every figure "
                 "the article cites must come from here)")
    lines.append("")
    lines.append(f"INCIDENTS: {len(incidents)} total, "
                 f"{incidents[0]['traded_at'].date()} to "
                 f"{incidents[-1]['traded_at'].date()}")
    lines.append(f"  compliant:   n={len(comp)}, net ${_net(comp):+,.2f}, "
                 f"avg ${_avg(comp):+,.2f}/trade")
    lines.append(f"  violation:   n={len(viol)}, net ${_net(viol):+,.2f}, "
                 f"avg ${_avg(viol):+,.2f}/trade")
    if uncl:
        lines.append(f"  unclassified: n={len(uncl)} (not cited as either)")
    if comp and viol:
        _gap = _avg(comp) - _avg(viol)
        _rate = len(viol) / (len(comp) + len(viol)) * 100.0
        lines.append(f"  per_trade_gap: compliant minus violation = "
                     f"${_gap:+,.2f}")
        lines.append(f"  violation_rate: {_rate:.1f}% of classified "
                     f"incidents")
        if _net(viol) >= 0:
            lines.append("  NOTE: violations were NET POSITIVE overall — "
                         "the finding is the per-trade gap, NOT that "
                         "violations lost money. Say the gap, not a loss.")
    lines.append("")

    lines.append("INCIDENT LIST (cite these; do not invent one):")
    for i in incidents:
        _c = _classify(i["reading_key"])
        lines.append(
            f"  - {i['traded_at'].date()} | {i['instrument']} | "
            f"{i['contracts']} contracts | {i['direction']} | "
            f"result ${float(i['result'] or 0):+,.2f} | "
            f"{_c} ({i['reading_key']})"
        )
    lines.append("")

    # Subject selection is explicit. Callers pass subject_id; the
    # fallback is the most recent VIOLATION (the article is about a
    # rule break), then the most recent incident if there are none.
    # Never silently pick an arbitrary row.
    subj = None
    if subject_id:
        subj = next(
            (i for i in incidents if str(i["id"]) == str(subject_id)), None
        )
        if subj is None:
            raise ValueError(
                f"psychology_skeleton: subject_id {subject_id} not found "
                "among live incidents"
            )
    else:
        _pool = viol or incidents
        subj = max(_pool, key=lambda i: i["traded_at"])
    lines.append("SUBJECT TRADE (the article's hook):")
    lines.append(f"  id: {subj['id']}")
    lines.append(f"  date: {subj['traded_at'].date()}")
    lines.append(f"  instrument: {subj['instrument']}")
    lines.append(f"  contracts: {subj['contracts']}")
    lines.append(f"  direction: {subj['direction']}")
    lines.append(f"  result: ${float(subj['result'] or 0):+,.2f}")
    lines.append(f"  reading_key: {subj['reading_key']} "
                 f"({_classify(subj['reading_key'])})")
    if (subj.get("narrative") or "").strip():
        lines.append(f"  narrative: {subj['narrative'][:600]}")
    lines.append(f"  chart_symbol: {subj['instrument']}")
    lines.append("")

    lines.append("LIMITS (state plainly):")
    lines.append("  - Every incident is one trader's own log, not a "
                 "sample of a population.")
    lines.append("  - Compliance is classified from the reading_key, not "
                 "from an independent audit.")
    lines.append("  - The gap is per-trade; the net of violations may be "
                 "positive or negative and must be stated as computed.")
    lines.append("")
    return "\n".join(lines)
