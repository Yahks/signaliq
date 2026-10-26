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
    # Compare absolute values: the body signals a loss with the
    # word "lost" and writes "$95.76", while the skeleton stores
    # -95.76. Requiring the sign in the dollar token rejected
    # correct sentences ("lost $95.76", "lost $1,463.53").
    allowed = {abs(round(t["result"], 2)) for t in trades}
    for m in re.finditer(r"\$\s*-?([\d,]+(?:\.\d+)?)", body or ""):
        v = abs(float(m.group(1).replace(",", "")))
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


def _skeleton_trade_percents(skeleton: str) -> set[str]:
    """Per-trade 'X% of equity' values from the TRADES block."""
    return {
        m.group(1)
        for m in re.finditer(r"(-?\d+(?:\.\d+)?)% of equity", skeleton or "")
    }


# Rule percentages may only be cited in the context of the rule
# they belong to. "0.5% clearance" is real; "0.5% of equity" is a
# different claim and must match a trade's actual percent.
_RULE_CONTEXT = {
    "0.5": ("clearance", "range", "high", "low", "rule", "require",
            "threshold", "move", "test", "minimum"),
}


def _skeleton_all_percentages(skeleton: str) -> set[str]:
    """Every percentage the skeleton prints, anywhere. A percentage
    the article cites is allowed iff it appears here.

    This replaces context-guessing (rule words, move phrasing,
    convergence bands). The skeleton prints the clearance rule
    (0.5), the EMA gap (-0.26), the bar moves (0.16/0.71/0.55), the
    breakout margin (0.03), the trade percents, and the computed
    clearance — all legitimate to quote. Anything absent is
    invented.
    """
    return {
        m.group(1).lstrip("+-")
        for m in re.finditer(r"([-+]?\d+(?:\.\d+)?)\s*%", skeleton or "")
    }


def _check_invented_statistic(body: str, skeleton: str) -> list[str]:
    """A percentage in the body must appear in the skeleton."""
    allowed = _skeleton_all_percentages(skeleton)

    def _norm(x: str) -> str:
        # "0" and "0.0" are the same number; the batch line prints
        # "0.0%" while the article may write "0%".
        try:
            return f"{float(x):g}"
        except ValueError:
            return x

    allowed = {_norm(a) for a in allowed}
    reasons: list[str] = []
    for m in re.finditer(r"([-+]?\d+(?:\.\d+)?)\s*%", body or ""):
        v = _norm(m.group(1).lstrip("+-"))
        if v not in allowed:
            reasons.append(
                f"percentage {m.group(0).strip()} is not in the skeleton "
                f"(invented statistic)"
            )
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


def _skeleton_bars(skeleton: str) -> list[dict[str, float]]:
    """Parse '2026-10-21 16:30 | O 199.46 | H 200.30 | L 199.40 | C 199.99'."""
    out: list[dict[str, float]] = []
    for line in (skeleton or "").splitlines():
        m = re.match(
            r"\s*(\d{4}-\d{2}-\d{2}[ \d:]*) \|\s*O\s*([\d,.]+)\s*\|\s*"
            r"H\s*([\d,.]+)\s*\|\s*L\s*([\d,.]+)\s*\|\s*C\s*([\d,.]+)",
            line,
        )
        if m:
            out.append({
                "open": float(m.group(2).replace(",", "")),
                "high": float(m.group(3).replace(",", "")),
                "low": float(m.group(4).replace(",", "")),
                "close": float(m.group(5).replace(",", "")),
            })
    return out


def _skeleton_clearance(skeleton: str) -> float | None:
    """The computed clearance percentage from the SUBJECT block."""
    m = re.search(r"clearance (?:beyond prior high|below prior low):\s*([+-]?[\d.]+)%",
                  skeleton or "")
    if not m:
        return None
    try:
        return abs(float(m.group(1)))
    except ValueError:
        return None


def _check_bars_cited(body: str, skeleton: str) -> list[str]:
    """Any OHLC value the body cites must be one of the skeleton's
    bar values (open/high/low/close, within rounding)."""
    reasons: list[str] = []
    bars = _skeleton_bars(skeleton)
    if not bars:
        return reasons
    allowed: set[float] = set()
    for b in bars:
        for k in ("open", "high", "low", "close"):
            allowed.add(round(b[k], 2))
    # OVERLAY prices (prior-day high/low, EMA values) are real
    # figures the article may cite as prices.
    _ov = re.search(r"OVERLAY[^\n]*\n(.*?)(?:\n\s*\n|\Z)",
                    skeleton or "", re.DOTALL)
    if _ov:
        for _n in re.findall(r"\d+\.\d+", _ov.group(1)):
            try:
                allowed.add(round(float(_n), 2))
            except ValueError:
                pass
    # Only inspect numbers that sit next to an OHLC-ish word, so we
    # don't flag ordinary prose numbers.
    for m in re.finditer(
        r"\b(open(?:ed)?|high|low|close[d]?)\b[^.\d]{0,24}?"
        r"(\d[\d,]*\.\d{2,})(?!\s*%)",
        body or "", re.IGNORECASE,
    ):
        v = round(float(m.group(2).replace(",", "")), 2)
        if v < 1.0:
            # A price below 1 is almost certainly a percentage
            # fragment ("0.048%") mis-read as a candle value.
            continue
        if not any(abs(v - a) <= 0.02 for a in allowed):
            reasons.append(
                f"candle value {m.group(1)} {m.group(2)} is not in the "
                f"skeleton's BARS block"
            )
    return reasons


def _check_clearance_claim(body: str, skeleton: str) -> list[str]:
    """If the body states the clearance percentage, it must match
    the computed value in the SUBJECT block."""
    reasons: list[str] = []
    computed = _skeleton_clearance(skeleton)
    if computed is None:
        return reasons
    for m in re.finditer(r"clear(?:ed|ance)[^.\d]{0,40}?([\d.]+)\s*%", body or "",
                         re.IGNORECASE):
        # Skip a sentence that quotes the RULE ("cleared the prior
        # high by 0.5%", "clearance test", "whenever ... cleared").
        _sentence = body[max(0, m.start() - 80):m.end() + 80].lower()
        if any(w in _sentence for w in (
            "rule", "test", "whenever", "require", "minimum", "threshold",
        )):
            continue
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if abs(v - computed) > 0.05:
            reasons.append(
                f"clearance stated as {v}% but the skeleton computes "
                f"{computed}%"
            )
    return reasons


def _skeleton_overlay(skeleton: str) -> dict[str, str] | None:
    """Parse the OVERLAY block: EMA values and the state string."""
    m = re.search(
        r"OVERLAY \(computed at the entry bar\):(.*?)(?:\n\s*\n|\Z)",
        skeleton or "", re.DOTALL,
    )
    if not m:
        return None
    block = m.group(1)
    out: dict[str, str] = {}
    e20 = re.search(r"20-EMA:\s*([\d.]+|unknown)", block)
    e50 = re.search(r"50-EMA:\s*([\d.]+|unknown)", block)
    st = re.search(r"state:\s*([^\n]+)", block)
    out["ema20"] = e20.group(1) if e20 else "unknown"
    out["ema50"] = e50.group(1) if e50 else "unknown"
    out["state"] = (st.group(1).strip().lower() if st else "")
    return out


def _check_ema_claims(body: str, skeleton: str) -> list[str]:
    """An EMA relationship the body asserts must match the skeleton.

    The published D8 article claimed the 20-EMA was 'below' the
    50-EMA for a trade where the skeleton computes them
    converging (0.18% apart). This check makes that a rejection.
    """
    reasons: list[str] = []
    ov = _skeleton_overlay(skeleton)
    if not ov:
        return reasons
    state = ov["state"]
    b = (body or "").lower()

    def _mentions(pattern: str) -> bool:
        return re.search(pattern, b) is not None

    # If EMAs are not computable, the body must not assert any state.
    if "not computable" in state:
        if re.search(r"(20[- ]?ema|50[- ]?ema)", b) and re.search(
            r"\b(above|below|crossing|converging|bullish|bearish)\b", b
        ):
            reasons.append(
                "body asserts an EMA relationship, but the skeleton "
                "reports the EMAs are not computable at this bar"
            )
        return reasons

    # The model writes "20-period EMA", "20-EMA", "20 EMA".
    _e = r"20[- ]?(?:period )?ema"
    _claims_below = bool(re.search(_e + r"[^.]{0,40}\bbelow\b", b))
    _claims_above = bool(re.search(_e + r"[^.]{0,40}\babove\b", b))
    # When the EMAs are converging (within 0.5%), NO direction is
    # supportable — the model asserted "below" for a pair the
    # skeleton computes 0.18% apart. Any directional claim against
    # a converging state is a rejection.
    if "converging" in state:
        if _claims_below or _claims_above:
            reasons.append(
                f"body asserts the 20-EMA is "
                f"{'below' if _claims_below else 'above'} the 50-EMA, "
                f"but the skeleton computes them converging "
                f"({ov['state']}) — no direction is supportable"
            )
        # A converging pair supports NO directional market read.
        # The model has written "converging downtrend" and "bearish
        # pressure" from a 0.18% gap. Reject the directional words
        # when they sit near the EMA discussion.
        for m in re.finditer(
            r"(?:ema|overlay)[^.]{0,120}?"
            r"\b(downtrend|uptrend|bearish|bullish|trend support|"
            r"trend bias|downward bias|upward bias)\b",
            b, re.IGNORECASE,
        ):
            reasons.append(
                f"body calls the EMA state '{m.group(1)}' but the "
                f"skeleton computes them converging "
                f"({ov['state']}) — a converging pair supports no "
                f"direction"
            )
    elif _claims_below and "above" in state:
        reasons.append(
            f"body says the 20-EMA is below the 50-EMA, but the "
            f"skeleton computes: {ov['state']}"
        )
    elif _claims_above and "below" in state:
        reasons.append(
            f"body says the 20-EMA is above the 50-EMA, but the "
            f"skeleton computes: {ov['state']}"
        )
    # "crossing/converging" claim vs a clearly separated state
    if ("crossing" in b or "converging" in b) and "converging" not in state:
        if "above" in state or "below" in state:
            reasons.append(
                f"body calls the EMAs crossing/converging, but the "
                f"skeleton computes: {ov['state']}"
            )
    # VWAP: never computable (no volume)
    if "vwap" in b:
        reasons.append(
            "body references VWAP, but market_history has no volume "
            "data — VWAP cannot be computed and must not be claimed"
        )
    return reasons


def _normalize_body(body: str) -> str:
    """Fold Unicode punctuation to ASCII and drop image URLs.

    The model emits non-breaking hyphens (U+2011), en/em dashes,
    and narrow no-break spaces. Every checker below uses ASCII
    '-' and ' ', so without this fold the EMA, date, and candle
    checks silently miss real text ("20-EMA" written "20‑EMA").
    The chart image URL is removed so its date does not read as a
    trade date.
    """
    b = body or ""
    b = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", b)   # markdown images
    b = re.sub(r"/static/[^\s\)]+", "", b)          # static paths
    b = (b.replace("\u2011", "-").replace("\u2013", "-")
          .replace("\u2014", "-").replace("\u2212", "-")
          .replace("\u00a0", " ").replace("\u202f", " ")
          .replace("\u2009", " "))
    return b


def _skeleton_moves(skeleton: str) -> set[str]:
    """The 'close moved X%' figures from the BAR ACTION block."""
    m = re.search(r"BAR ACTION.*?(?:\n\s*\n|\Z)", skeleton or "", re.DOTALL)
    if not m:
        return set()
    return {v.lstrip("+").lstrip("-")
            for v in re.findall(r"close moved ([+-]?\d+\.\d+)%", m.group(0))}


def _check_bar_moves(body: str, skeleton: str) -> list[str]:
    """A percentage the body attributes to a price move must be a
    computed BAR ACTION figure, not a characterisation."""
    reasons: list[str] = []
    moves = _skeleton_moves(skeleton)
    if not moves:
        return reasons
    for m in re.finditer(
        r"(?:price|close|it)\s+(?:moved|fell|rose|dropped|gained)\s+"
        r"([\d.]+)\s*%", body or "", re.IGNORECASE,
    ):
        v = m.group(1).lstrip("-")
        if v not in moves:
            reasons.append(
                f"body states a price move of {m.group(1)}%, but the "
                f"BAR ACTION block computes {sorted(moves)}"
            )
    return reasons


def _check_invented_ratios(body: str, skeleton: str) -> list[str]:
    """Reject 'N× the average' style claims the skeleton cannot
    support. The published D2 article invented a "1.5x the 20-day
    volume average" filter — market_history has no volume, and no
    such rule is in the process. Ratios are not percentages, so the
    percentage check missed it."""
    reasons: list[str] = []
    for m in re.finditer(
        r"(\d+(?:\.\d+)?)\s*[x×]\s*(?:the\s+)?"
        r"([a-z0-9\- ]{0,30}?)\b(average|mean|median|baseline)\b",
        body or "", re.IGNORECASE,
    ):
        reasons.append(
            f"body invents a ratio ('{m.group(0).strip()}') the skeleton "
            f"cannot support — no such computed figure or rule exists"
        )
    # Volume is not tracked at all.
    if re.search(r"\bvolume\b", body or "", re.IGNORECASE):
        reasons.append(
            "body references volume, but market_history has no volume "
            "data — volume-based rules cannot be claimed"
        )
    return reasons


def validate_technical_indicators(body: str, skeleton: str) -> list[str]:
    if not body:
        return ["empty body"]
    if not skeleton:
        return ["technical_indicators validator: no skeleton supplied"]
    body = _normalize_body(body)
    reasons: list[str] = []
    reasons.extend(_check_future_dates(body, skeleton))
    reasons.extend(_check_cited_trades(body, skeleton))
    reasons.extend(_check_invented_statistic(body, skeleton))
    reasons.extend(_check_bars_cited(body, skeleton))
    # Clearance claims are covered by the percentage allowlist
    # (the computed margin appears in the skeleton).
    reasons.extend(_check_ema_claims(body, skeleton))
    reasons.extend(_check_bar_moves(body, skeleton))
    reasons.extend(_check_invented_ratios(body, skeleton))
    reasons.extend(_check_no_cta(body))
    reasons.extend(_check_no_checklist(body))
    return reasons
