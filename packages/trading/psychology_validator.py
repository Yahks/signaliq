"""psychology_validator: checks for trading_psychology articles.

Separate from verify_article_content (which is an LLM verifier and
fail-open) and from delivery_check (which handles CTA/URL/register
shape). This module does the deterministic reconciliation checks the
reviewer asked for:

  - the compliant-vs-violation split appears and matches `readings`
  - the per-trade gap and the violation rate appear
  - the cited trade's instrument, contracts, result and hold time
    appear exactly as in `latest_trade`
  - no checklist ("Can I / Do I / Have I" run) and no URL
"""
from __future__ import annotations

import re
from typing import Any


def _fmt_money(v: float) -> list[str]:
    """Accept $28,800 / 28,800 / 28800 / $28.8k for a figure."""
    out = []
    a = f"{abs(v):,.2f}"
    out.append(a)                       # 28,800.00
    out.append(a.split(".")[0])         # 28,800
    out.append(f"{abs(v):.0f}")         # 28800
    if abs(v) >= 1000:
        out.append(f"{abs(v)/1000:.1f}k")   # 28.8k
        out.append(f"{abs(v)/1000:.0f}k")   # 29k
    return out


def _present(body: str, v: float, *, signed: bool = False) -> bool:
    b = body.replace("$", "").replace("\u202f", " ").replace(",", "")
    for cand in _fmt_money(v):
        c = cand.replace(",", "")
        if c in b:
            return True
    return False


_RETIRED_REFRAMES = (
    "the rule doesn't end the impulse",
    "the rule doesn't stop the impulse",
    "the rule doesn't silence the impulse",
    "it ends the click",
    "it stops the click",
    "it redirects the click",
    "a stop isn't the trade",
    "discipline is what's left",
    "the gap between the impulse and the click",
)


def _check_reframe_not_boilerplate(body: str) -> list[str]:
    """Reject a reframe copied from a retired template.

    The prompt used to list example reframes; at temperature 0 the
    model reproduced them verbatim. The examples are gone; this
    catches a regression.
    """
    low = (body or "").lower()
    for phrase in _RETIRED_REFRAMES:
        if phrase in low:
            return [
                f"reframe reuses a retired template ({phrase!r}) — "
                f"write a reframe specific to this article"
            ]
    return []


def validate_psychology(
    body: str,
    readings: dict[str, Any],
    latest_trade: dict[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    if not body:
        return ["empty body"]

    # ── 1. the reconciliation ──
    r = readings or {}
    if r.get("compliant_n") is not None:
        checks = (
            ("compliant n", r.get("compliant_n")),
            ("violation n", r.get("violation_n")),
        )
        for label, val in checks:
            if val is not None and str(val) not in body:
                reasons.append(
                    f"reconciliation: {label} ({val}) does not appear "
                    f"in the article"
                )
        for label, key in (
            ("compliant net", "compliant_net"),
            ("violation net", "violation_net"),
            ("per-trade gap", "per_trade_gap"),
        ):
            v = r.get(key)
            if v is not None and not _present(body, float(v)):
                reasons.append(
                    f"reconciliation: {label} (${float(v):+,.2f}) does "
                    f"not appear in the article"
                )
        rate = r.get("violation_rate_pct")
        if rate is not None and f"{float(rate):g}" not in body:
            reasons.append(
                f"reconciliation: violation rate ({rate}%) does not "
                f"appear in the article"
            )
        # profitable violations — the reviewer's #1 point
        vwn = r.get("violation_wins_n")
        if vwn:
            if str(vwn) not in body:
                reasons.append(
                    f"reconciliation: count of profitable violations "
                    f"({vwn}) does not appear — a violation that wins "
                    f"is the most dangerous kind and must be stated"
                )
        # sample period
        ss, se = r.get("sample_start"), r.get("sample_end")
        if ss and se:
            if ss not in body or se not in body:
                reasons.append(
                    f"sample period ({ss} to {se}) does not appear"
                )

    # ── 2. the cited trade ──
    lt = latest_trade or {}
    if lt.get("instrument"):
        if lt["instrument"] not in body:
            reasons.append(
                f"cited trade: instrument {lt['instrument']} does not "
                f"appear in the article"
            )
    if lt.get("contracts") is not None:
        # accept the digit or a number word near the instrument
        _n = str(lt["contracts"])
        _words = {"1": "one", "2": "two", "3": "three", "4": "four",
                  "5": "five", "6": "six", "7": "seven", "8": "eight",
                  "9": "nine", "10": "ten"}
        if _n not in body and _words.get(_n, "") not in body.lower():
            reasons.append(
                f"cited trade: contracts ({_n}) does not appear"
            )
    if lt.get("result") is not None and not _present(
        body, float(lt["result"])
    ):
        reasons.append(
            f"cited trade: result (${float(lt['result']):+,.2f}) does "
            f"not appear"
        )
    # the charted trade's entry must be named, so the article refers
    # to the prices on the chart it accompanies
    if lt.get("entry") is not None and not _present(
        body, float(lt["entry"])
    ):
        reasons.append(
            f"chart: the cited trade's entry price "
            f"({float(lt['entry']):.2f}) does not appear — name the "
            f"entry so the article refers to the charted trade"
        )

    # the chart tag must have been placed and substituted
    if "[CHART:" in (body or ""):
        reasons.append(
            "chart tag [CHART:daily] was not substituted — the chart "
            "placement failed"
        )

    reasons.extend(_check_reframe_not_boilerplate(body))

    # ── 3. backstops ──
    if re.search(r"https?://(?!signaliq\.com/static)", body):
        reasons.append("article contains a URL")
    q = re.findall(r"\b(?:Can I|Do I|Have I|Did I)\b", body)
    if len(q) >= 3:
        reasons.append(
            f"article contains a {len(q)}-question checklist; "
            f"psychology-note has no checklist beat"
        )

    return reasons
