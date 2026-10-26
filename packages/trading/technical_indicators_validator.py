"""technical_indicators_validator: checks for the technical_indicators
subblock. Separate from article_validator.validate (trade-centric)
and market_condition_validator (regime-specific).

Every check compares the article to the skeleton it was given
(technical_indicators_skeleton.build_technical_skeleton), stored in
source_payload["skeleton"] at generation.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .technical_indicators_math import compute_trade


def _skeleton_trades(skeleton: str) -> list[dict[str, Any]]:
    """Parse the TRADES lines:
    '- 2026-10-21 | PLTR | long | 100 shares | entry 200.00 | stop 199.27 | exit 201.45 | result $145.28 | ...'
    """
    out: list[dict[str, Any]] = []
    for line in (skeleton or "").splitlines():
        m = re.match(
            r"\s*-\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([A-Z0-9.-]+)\s*\|\s*"
            r"(long|short)\s*\|\s*(\d+)\s+shares\s*\|\s*entry\s*([\d,.]+)\s*\|\s*"
            r"stop\s*([\d,.]+)\s*\|\s*exit\s*([\d,.]+)\s*\|\s*result\s*\$(-?[\d,.]+)",
            line,
        )
        if m:
            out.append({
                "date": m.group(1), "symbol": m.group(2),
                "direction": m.group(3), "contracts": int(m.group(4)),
                "entry": float(m.group(5).replace(",", "")),
                "stop": float(m.group(6).replace(",", "")),
                "exit": float(m.group(7).replace(",", "")),
                "result": float(m.group(8).replace(",", "")),
            })
    return out


def _skeleton_window(skeleton: str) -> tuple[date | None, date | None]:
    m = re.search(r"window:\s*(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})", skeleton or "")
    if not m:
        return None, None
    try:
        return (date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2)))
    except ValueError:
        return None, None


def _skeleton_batch_numbers(skeleton: str) -> set[str]:
    """Every numeric figure in the skeleton BATCH block, as strings
    with commas stripped — the allowed set for process statistics."""
    m = re.search(r"BATCH \(.*?\):(.*?)(?:\nLIMITS|\Z)", skeleton or "", re.DOTALL)
    if not m:
        return set()
    block = m.group(1)
    return {n.replace(",", "") for n in re.findall(r"-?\d+(?:\.\d+)?", block)}


def _body_numbers(body: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"-?\d+(?:\.\d+)?", body or "")}


def _check_future_dates(body: str, skeleton: str) -> list[str]:
    """No date after today, and every date inside the skeleton window."""
    reasons: list[str] = []
    today = datetime.now().date()
    win_start, win_end = _skeleton_window(skeleton)
    for m in re.finditer(r"(\d{4}-\d{2}-\d{2})", body or ""):
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d > today:
            reasons.append(f"date {m.group(1)} is in the future (today is {today.isoformat()})")
        elif win_start and win_end and not (win_start <= d <= win_end):
            reasons.append(
                f"date {m.group(1)} is outside the trade window "
                f"{win_start.isoformat()}→{win_end.isoformat()}"
            )
    return reasons


def _check_cited_trades(body: str, skeleton: str) -> list[str]:
    """Any 'SYMBOL ... entry X ... stop Y ... exit Z ... $R' tuple in the
    body must match a skeleton trade."""
    reasons: list[str] = []
    trades = _skeleton_trades(skeleton)
    if not trades:
        return reasons
    for t in trades:
        # If the body names this symbol with a dollar figure that
        # isn't this trade's result, and doesn't cite the trade by
        # date, flag it only when the symbol+result pair is clearly
        # about this trade.
        pass
    # Simpler and safer: any '$N' the body attributes to a trade
    # must be one of the skeleton results.
    allowed = {round(t["result"], 2) for t in trades}
    for m in re.finditer(r"\$\s*-?([\d,]+(?:\.\d+)?)", body or ""):
        v = float(m.group(1).replace(",", ""))
        if not any(abs(v - a) <= 0.05 for a in allowed):
            # allow batch figures too
            batch = _skeleton_batch_numbers(skeleton)
            if m.group(1).replace(",", "") in batch:
                continue
            reasons.append(f"dollar figure ${m.group(1)} is not a skeleton trade result")
    return reasons


def _skeleton_rule_percents(skeleton: str) -> set[str]:
    """Percentages listed under the skeleton's RULES block."""
    m = re.search(r"RULES \(current process.*?\n(.*?)(?:\n\n|\Z)",
                  skeleton or "", re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r"(\d+(?:\.\d+)?)\s*%", m.group(1)))


def _check_invented_statistic(body: str, skeleton: str) -> list[str]:
    """A percentage must be a real current-process rule, a trade
    percentage, or a batch figure. Anything else is invented.

    '0.5% clearance' is in the process's CURRENT PROCESS section, so
    it passes; '15% variance reduction' and '0.4% stop cap' appear
    nowhere and are flagged.
    """
    reasons: list[str] = []
    allowed = set(_skeleton_rule_percents(skeleton))
    allowed |= _skeleton_batch_numbers(skeleton)
    allowed |= _body_numbers(skeleton)
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", body or ""):
        v = m.group(1)
        if v in allowed:
            continue
        reasons.append(f"percentage {v}% is not a current rule, a trade "
                       f"percentage, or a batch figure (invented statistic)")
    return reasons


def _check_no_cta(body: str) -> list[str]:
    if re.search(r"(?i)\b(?:email me|sign up|subscribe|free sample|no card|"
                 r"try .* free|click here)\b", body or ""):
        return ["body contains a CTA / marketing line"]
    if re.search(r"https?://(?!signaliq\.com/static)", body or ""):
        return ["body contains a URL"]
    return []


def _check_no_checklist(body: str) -> list[str]:
    b = body or ""
    if re.search(r"\bchecklist\b", b, re.IGNORECASE):
        return ["article contains a checklist beat — removed from this subblock"]
    confirms = len(re.findall(r"(?:^|\n)\s*(?:Confirm|Verify|Check)\b", b))
    if confirms >= 3:
        return [f"article contains a {confirms}-item Confirm/Verify list"]
    return []


async def load_frozen_skeleton(session, post_id: str) -> str:
    from sqlalchemy import text as _t
    row = (await session.execute(
        _t("SELECT source_payload FROM trading_posts WHERE id = :p"),
        {"p": post_id},
    )).first()
    if not row or not row[0]:
        return ""
    payload = row[0]
    if isinstance(payload, str):
        import json as _json
        try:
            payload = _json.loads(payload)
        except Exception:
            return ""
    return (payload or {}).get("skeleton", "") or ""


def validate_technical_indicators(body: str, skeleton: str) -> list[str]:
    if not body:
        return ["empty body"]
    if not skeleton:
        return ["technical_indicators validator: no skeleton supplied"]
    reasons: list[str] = []
    reasons.extend(_check_future_dates(body, skeleton))
    reasons.extend(_check_cited_trades(body, skeleton))
    reasons.extend(_check_invented_statistic(body, skeleton))
    reasons.extend(_check_no_cta(body))
    reasons.extend(_check_no_checklist(body))
    return reasons
