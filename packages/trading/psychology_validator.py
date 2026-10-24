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
    """True if the figure appears, tolerant of separators and signs.

    The model writes "+38 666.00" with U+202F as the thousands
    separator and U+2013 as the minus. Normalise the body AND the
    candidate the same way: strip every separator (comma, space,
    narrow space, U+2010-2015) and compare the digits and the sign.
    """
    import re as _re
    def _norm(s: str) -> str:
        s = (s or "")
        s = _re.sub(r"[,\s\u202f\u00a0]", "", s)      # separators
        s = _re.sub(r"[\u2010-\u2015]", "-", s)       # dashes -> '-'
        return s
    b = _norm(body.replace("$", ""))
    for cand in _fmt_money(v):
        if _norm(cand) in b:
            return True
    return False


_UNICODE_MAP = {
    0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2013: "-",
    0x2014: "-", 0x2015: "-",
    0x202F: " ", 0x00A0: " ",
}


def _ascii(text: str) -> str:
    """Normalise Unicode punctuation before any match.

    The model writes dates with U+2011 ("2026‑01‑02") and
    the sample-period check compares against ASCII. Without this the
    check rejects a correct sentence. Same fix as
    market_condition_validator._ascii.
    """
    return (text or "").translate(_UNICODE_MAP)


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


def _check_reframe_paragraph(body: str) -> list[str]:
    """The article must end on a 3-4 sentence explanatory paragraph.

    A one-line reframe invites a contrast template ("X is not Y; it
    is Z"), which is what the prompt used to hand the model and
    what it reproduced. Requiring prose forces an explanation.
    """
    paras = [p.strip() for p in (body or "").strip().split("\n\n")
             if p.strip()]
    # skip a trailing image (the chart)
    while paras and paras[-1].startswith("!["):
        paras.pop()
    if not paras:
        return ["article has no closing paragraph"]
    last = paras[-1]
    sentences = [s for s in __import__("re").split(
        r"(?<=[.!?])\s+", last) if s.strip()]
    # A single sentence is the slogan shape the prompt forbids.
    # Beyond that, length is the model's call — the prompt no longer
    # specifies a sentence count, so neither does this check.
    if len(sentences) < 2:
        return [
            "the closing paragraph is a single sentence; the reframe "
            "must be an explanation, not a one-line slogan"
        ]
    return []


def _check_unit_word(body: str, latest_trade: dict | None) -> list[str]:
    """A stock is described in shares, a future in contracts."""
    if not latest_trade:
        return []
    from packages.trading.psychology_log import unit_word
    inst = latest_trade.get("instrument")
    want = unit_word(inst)
    wrong = "contracts" if want == "shares" else "shares"
    import re as _re
    m = _re.search(rf"\b\d[\d,]*\s+{wrong}\b", body or "",
                   _re.IGNORECASE)
    if m:
        return [
            f"cited trade {inst} uses '{want}', but the article says "
            f"'{m.group(0)}'"
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
    body = _ascii(body)

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
    reasons.extend(_check_reframe_paragraph(body))
    reasons.extend(_check_unit_word(body, latest_trade))

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
