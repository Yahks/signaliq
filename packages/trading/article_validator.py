"""Post-generation validator.

The pre-computation layer (trade_rule_analysis.py) gives the model
facts it can quote. The prompt forbids computing, fabricating, and
reusing a reframe mechanism. The model has repeatedly satisfied
the form of those instructions without honoring the intent: it
reads "avoid super-linear tail risk" and writes "super-linear tail
exposure"; it reads the analysis and grafts the formula's contract
count into a stop-loss computation; it invents a fifth diagnostic
that no trade in the log supports.

This module is the enforcement layer. It runs after generation,
before persistence. A rejection lands the post as a draft with a
__validator__ flag; the reviewer sees why. No regeneration loop
(S.8.20: retries mask the failure rate).
"""
from __future__ import annotations

import re
from typing import Any

from packages.core.logging import get_logger

log = get_logger(__name__)


# ── Forbidden reframe mechanisms ─────────────────────────────────
# The model has repeatedly re-expressed the same idea in fresh
# words. Phrase bans don't catch variants. These are canonical
# statements; a generated reframe is rejected if it is close to
# any of them by token overlap.
FORBIDDEN_REFRAME_MECHANISMS = [
    "tail risk grows faster than linear position size",
    "each additional contract adds more than a linear amount of risk",
    "high volatility amplifies the risk of extra contracts",
    "position size and tail risk have a nonlinear relationship",
    "a linear increase in contracts produces a superlinear increase in risk",
]

# Rhetorical constants tolerated in the body even if not in source.
# Structural constants the article may cite from the process
# document. These are not fabrications — they are the rules the
# trade was measured against.
#   $2,000 — the risk budget in the sizing rule
#   $20    — dollars per point (NQ)
#   $30    — the ATR threshold
#   $200,000 — the account equity / starting balance
#   0.5%   — the clearance minimum
#   0.75   — the stop multiplier (also written as a percent)
#   0.3%   — a small percent-of-entry move the analysis mentions
#   1.5%   — a typical move size mentioned in the analysis
TOLERATED_DOLLARS = {
    20, 30, 1000, 2000, 5000, 10000, 200000,
}
TOLERATED_PERCENTS = {
    0.3, 0.5, 0.75, 1.0, 1.5, 30.0, 100.0,
}

# Keyword groups for the "position size → disproportionate risk"
# mechanism. A sentence that pulls tokens from each group is
# expressing the mechanism regardless of the exact wording.
_PROPORTIONALITY = {
    "linear", "linearly", "linearity", "nonlinear",
    "superlinear", "super",
    "disproportionate", "disproportionately",
    "faster", "outpace", "outpaces", "outrun", "outruns",
    "outstrip", "outstrips", "exceed", "exceeds",
    "multiplied", "multiplies", "multiply", "multiples",
    "compounds", "compounding",
    "amplifies", "amplified", "amplify",
    "spikes", "inflationary",
}
_SUBJECT = {
    "contract", "contracts", "size", "sizes", "position", "positions",
    "sizing", "exposure", "exposures", "leverage",
}
_RISK_TERMS = {"risk", "tail", "drawdown", "loss", "losses"}
_SIZE_CHANGE = {
    "add", "adds", "adding", "added",
    "increase", "increases", "increasing", "increased",
    "raise", "raises", "raising", "raised",
    "load", "loads", "loading", "loaded",
    "scale", "scales", "scaling", "scaled",
    "more", "additional", "extra", "larger",
}

# Distinctive mechanism keywords. A reframe must share at least one
# with the article's trade setups or the analysis block, else it is
# not about this article.
_REFRAME_KEYWORDS = {
    "stop", "atr", "clearance", "contract", "contracts", "size",
    "sizing", "breakout", "pullback", "regime", "exit", "entry",
    "signal", "nq", "nasdaq", "volatility", "drawdown", "rule",
    "filter", "risk",
}


# ── Extractors ────────────────────────────────────────────────────

def _dollars_from(text: str) -> set[int]:
    out: set[int] = set()
    for m in re.finditer(r"\$\s*([\d,]+(?:\.\d+)?)", text or ""):
        try:
            out.add(int(float(m.group(1).replace(",", ""))))
        except ValueError:
            continue
    return out


def _percents_from(text: str) -> set[float]:
    """All percent figures as they appear — no rounding. The
    comparison in validate() applies a tolerance instead."""
    out: set[float] = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", text or ""):
        try:
            out.add(float(m.group(1)))
        except ValueError:
            continue
    return out


def _tokens(text: str) -> set[str]:
    # Normalize hyphens to spaces so "super-linear" → {super, linear}.
    norm = (text or "").lower().replace("-", " ")
    return set(re.findall(r"[a-z]+", norm))


def _similar(a: str, b: str) -> float:
    """Jaccard similarity on content tokens, stopwords removed."""
    stop = {
        "the", "a", "an", "of", "to", "and", "or", "in", "on", "is",
        "are", "was", "were", "it", "its", "as", "at", "by", "for",
        "with", "that", "this", "than", "then", "so", "but", "if",
        "not", "no", "be", "been", "being", "has", "have", "had",
        "do", "does", "did", "will", "would", "can", "could",
        "should", "may", "might", "must", "into", "from", "more",
        "most", "less", "least", "each", "every", "any", "all",
        "one", "two", "three", "four", "five",
    }
    ca = _tokens(a) - stop
    cb = _tokens(b) - stop
    if not ca or not cb:
        return 0.0
    return len(ca & cb) / len(ca | cb)


# ── Allowed sets ─────────────────────────────────────────────────

def _allowed_dollars(
    analysis: str,
    trades: list[dict[str, Any]],
    account_equity: float | None = None,
    performance: dict[str, Any] | None = None,
) -> set[int]:
    allowed: set[int] = set()
    for t in trades:
        r = t.get("result")
        if r is not None:
            allowed.add(abs(int(r)))
    if account_equity:
        allowed.add(int(account_equity))
    if performance:
        for k in (
            "average_winner", "average_loser",
            "largest_win", "largest_loss",
        ):
            v = performance.get(k)
            if v is not None:
                try:
                    allowed.add(abs(int(round(float(v)))))
                except (TypeError, ValueError):
                    pass
    # ATR values sometimes appear in the prose with a "$" prefix
    # ("ATR $32" or "$32 ATR"). They are point values, not
    # dollar values, but the extractor cannot tell. Allow them
    # so the article isn't rejected for a formatting quirk the
    # prompt already discourages.
    for t in trades:
        atr = t.get("atr_at_entry")
        if atr is not None:
            try:
                allowed.add(abs(int(round(float(atr)))))
            except (TypeError, ValueError):
                pass
    allowed |= _dollars_from(analysis)
    allowed |= TOLERATED_DOLLARS
    return allowed


def _allowed_percents(
    analysis: str,
    trades: list[dict[str, Any]],
    account_equity: float | None = None,
    skeleton_text: str | None = None,
) -> set[float]:
    """Allowed percent figures.

    Sources:
      - clearance % from a trade's setup string
      - derived %-of-equity loss: abs(result) / equity * 100
      - every % in the analysis block
      - every % in the skeleton text (the picked trade + its
        derived values + its counterfactuals)
      - a small set of tolerated rhetorical constants
    """
    allowed: set[float] = set()
    for t in trades:
        setup = t.get("setup") or ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*clearance", setup)
        if m:
            allowed.add(float(m.group(1)))
        r = t.get("result")
        if r is not None and account_equity and account_equity > 0:
            pct = abs(float(r)) / float(account_equity) * 100.0
            allowed.add(pct)
    allowed |= _percents_from(analysis)
    if skeleton_text:
        allowed |= _percents_from(skeleton_text)
    allowed |= TOLERATED_PERCENTS
    return allowed


# ── Reframe extraction and semantic checks ───────────────────────

def _extract_reframe(body: str) -> str:
    """The reframe is the article's standalone truth sentence,
    wrapped in <!-- REFRAME --> markers. Read it from the markers,
    not by guessing.

    Falls back to the old shape (reframe immediately before a
    paragraph starting with "I now"/"I added"/"I introduced") for
    compatibility with older articles.
    """
    m = re.search(
        r"<!--\s*REFRAME\s*-->\s*(.*?)\s*<!--\s*/REFRAME\s*-->",
        body or "", re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    # Fallback for the old shape.
    m = re.search(
        r"([A-Z][^.!?]{20,200}[.!?])\s*\n+\s*I (?:now|added|introduced)",
        body or "",
    )
    if m:
        return m.group(1).strip()
    return ""


def _semantic_reframe_violation(body: str) -> str | None:
    reframe = _extract_reframe(body)
    if not reframe:
        return None

    # Near-verbatim reuse of a prior article's reframe.
    for forbidden in FORBIDDEN_REFRAME_MECHANISMS:
        sim = _similar(reframe, forbidden)
        if sim >= 0.30:
            return (
                f"reframe is near-verbatim to a prior article's line "
                f"({sim:.2f}) — rewrite from a different mechanism"
            )

    # Keyword co-occurrence of the "position size → disproportionate
    # risk" mechanism.
    tokens = _tokens(reframe)
    has_subject = bool(tokens & _SUBJECT)
    has_proportionality = bool(tokens & _PROPORTIONALITY)
    has_consequence = bool(tokens & _RISK_TERMS)
    has_change = bool(tokens & _SIZE_CHANGE)
    if has_subject and has_proportionality and (has_consequence or has_change):
        return (
            "reframe reuses the forbidden 'position size → "
            "disproportionate risk' mechanism. Pick a different "
            "aspect (stop placement, manual override, clearance "
            "rule, regime classification)."
        )

    return None


# ── Structural checks ────────────────────────────────────────────

def _parse_hook_date(text: str) -> tuple[int, int, int] | None:
    m = re.search(r"(\d{4})[-‑](\d{2})[-‑](\d{2})", text or "")
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m = re.search(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
        text or "",
    )
    if m:
        return int(m.group(3)), months[m.group(1).lower()], int(m.group(2))
    return None


def _check_irrelevant_rule(body: str,
                           trades: list[dict[str, Any]]) -> list[str]:
    """If the subject trade's setup is a pull-back, the body must
    not discuss clearance — that rule doesn't apply.

    The subject is trades[0]: the builder orders the batch as
    [subject] + related.
    """
    if not trades:
        return []
    subject = trades[0]
    st = (subject.get("setup") or "").lower()
    if "pull" not in st:
        return []
    if re.search(r"\bclearance\b", body or "", re.IGNORECASE):
        return [
            "clearance is discussed but the subject trade's setup "
            "is a pull-back — clearance does not apply"
        ]
    return []


def _check_hook_stop_state(
    body: str, trades: list[dict[str, Any]],
) -> list[str]:
    first = (body or "").split("\n\n", 1)[0]
    dt = _parse_hook_date(first)
    if not dt:
        return []
    trade = None
    for t in trades:
        td = t.get("traded_at")
        if td and hasattr(td, "year") and (td.year, td.month, td.day) == dt:
            trade = t
            break
    if not trade:
        return []
    log_has_stop = trade.get("stop") is not None
    lower = first.lower()
    claims_no_stop = bool(re.search(
        r"\b(no stop|did not place|without a stop|stop was missing|"
        r"stop was not set|stop was never|ignored the stop|"
        r"omitting the stop|missing stop)\b",
        lower,
    ))
    claims_stop_placed = bool(re.search(
        r"\b(stop (?:was|sat|placed|set) at|rule-based stop at|"
        r"stop price was|stop sat at)\b",
        lower,
    ))
    reasons: list[str] = []
    if log_has_stop and claims_no_stop:
        reasons.append(
            f"hook says no stop was placed, but trade_log has "
            f"stop={trade.get('stop')} for this trade"
        )
    if (not log_has_stop) and claims_stop_placed:
        reasons.append(
            "hook says a stop was placed, but trade_log has no stop "
            "for this trade"
        )
    return reasons


def _check_diagnostics_beat(body: str) -> list[str]:
    bullet_lines = [
        ln for ln in (body or "").splitlines()
        if re.match(r"^\s*[-*•]\s+\S", ln)
    ]
    if len(bullet_lines) < 3:
        return [
            f"diagnostics beat missing: only {len(bullet_lines)} "
            f"bulleted line(s); the register requires 4–6"
        ]
    return []




# ── Level-2 trade table verification ─────────────────────────────
# Every article includes a trade table with six rows. The table
# must be internally consistent: valid trading days, arithmetic
# that matches the stated result, and stops on the correct side
# of entry.

_MARKET_HOLIDAYS = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Presidents Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-07-03",  # Independence Day observed
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
    "2027-01-01",
    "2027-01-18",
    "2027-02-15",
    "2027-03-26",
    "2027-05-31",
    "2027-07-05",
    "2027-09-06",
    "2027-11-25",
    "2027-12-24",
}


def _is_trading_day(d: "date") -> bool:
    if d.weekday() >= 5:
        return False
    if d.isoformat() in _MARKET_HOLIDAYS:
        return False
    return True


_MONTH_NUM = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}


def _parse_table_date(cell: str) -> "date | None":
    """Parse 'Tuesday, February 24, 2026' or the same with time."""
    from datetime import date as _date
    m = re.search(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{1,2}),\s+(\d{4})",
        cell or "", re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return _date(int(m.group(3)),
                     _MONTH_NUM[m.group(1).lower()],
                     int(m.group(2)))
    except (ValueError, KeyError):
        return None


def _parse_signed_dollars(cell: str) -> int | None:
    """Parse '+$2,640' / '-$1,800' / 'LOSS of $1,800' / 'WIN of $2,640'."""
    t = (cell or "").strip()
    m = re.search(r"([+-]?)\$\s*([\d,]+)", t)
    if not m:
        return None
    try:
        n = int(m.group(2).replace(",", ""))
    except ValueError:
        return None
    sign = m.group(1)
    if sign == "-":
        return -n
    if sign == "+":
        return n
    if "loss" in t.lower():
        return -n
    return n


def _parse_number(cell: str) -> float | None:
    t = (cell or "").strip().replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None




def _contract_multiplier(instrument: str | None) -> float:
    """Per-point or per-share multiplier for an instrument.

    NQ futures: $20 per point.
    Stocks: $1 per share (the contracts column is share count).
    """
    sym = (instrument or "").upper()
    if sym.startswith("NQ"):
        return 20.0
    return 1.0

def _check_trade_table(
    body: str, trades: list[dict[str, Any]],
) -> list[str]:
    """Verify the article's markdown trade table.

    The table is the six-trade verification block. Every row must:
      - be a valid US trading day (weekday, not a holiday)
      - have a result that matches (exit-entry)*contracts*20
      - for a long, have stop below entry

    Any failure is a rejection reason.
    """
    reasons: list[str] = []

    # Find the six-column table.
    lines = (body or "").splitlines()
    header_idx = None
    for i, ln in enumerate(lines):
        if (re.match(r"^\s*\|.*Date.*\|.*Contracts.*\|", ln)
                and "Entry" in ln and "Result" in ln):
            header_idx = i
            break
    if header_idx is None:
        return ["trade table: not found"]

    # Collect rows until the table ends.
    rows: list[str] = []
    for ln in lines[header_idx + 2:]:  # skip header + separator
        if not re.match(r"^\s*\|.*\|\s*$", ln):
            break
        rows.append(ln)

    if len(rows) != 6:
        reasons.append(
            f"trade table: expected 6 rows, got {len(rows)}"
        )

    # Build the set of valid (year, month, day) tuples from the
    # batch. Comparing on the calendar date, not the weekday name
    # — the model occasionally flips "Tuesday" to "Thursday"
    # while keeping the day right. That is cosmetic; only a wrong
    # calendar date is a real error.
    valid_dates: set[tuple[int, int, int]] = set()
    for t in trades:
        td = t.get("traded_at")
        if td is not None and hasattr(td, "year"):
            valid_dates.add((td.year, td.month, td.day))

    def _find_trade(d) -> dict | None:
        for t in trades:
            td = t.get("traded_at")
            if td is None or not hasattr(td, "year"):
                continue
            if (td.year, td.month, td.day) == (d.year, d.month, d.day):
                return t
        return None

    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) < 6:
            reasons.append(f"trade table: malformed row: {row[:60]}")
            continue
        date_cell, contracts_s, entry_s, stop_s, exit_s, result_s = cells[:6]

        d = _parse_table_date(date_cell)
        if d is None:
            reasons.append(f"trade table: unparseable date: {date_cell}")
            continue
        if not _is_trading_day(d):
            reasons.append(
                f"trade table: non-trading day: {date_cell}"
            )

        # The calendar date MUST match one of the batch's trades.
        # The model has invented dates before — a phantom March 3
        # that didn't exist in a January–March batch. The weekday
        # name is cosmetic and is not compared here.
        if (d.year, d.month, d.day) not in valid_dates:
            reasons.append(
                f"trade table: {date_cell} is not one of the "
                f"batch's trades"
            )
            continue

        contracts = _parse_number(contracts_s)
        entry = _parse_number(entry_s)
        stop = _parse_number(stop_s) if stop_s else None
        exit_p = _parse_number(exit_s)
        result = _parse_signed_dollars(result_s)

        if None in (contracts, entry, exit_p, result):
            reasons.append(
                f"trade table: unparseable row for {date_cell}"
            )
            continue

        # Direction is known because the date matched.
        t = _find_trade(d)
        direction = (t.get("direction") or "long").lower()

        # Math check. The multiplier depends on the instrument:
        # NQ is $20/point, stocks are $1/share. Using a fixed 20
        # would make every stock trade look like a math error.
        _mult = _contract_multiplier(
            t.get("instrument") or t.get("symbol")
        )
        if direction == "long":
            computed = (exit_p - entry) * contracts * _mult
        else:
            computed = (entry - exit_p) * contracts * _mult
        if abs(computed - result) > 1:
            reasons.append(
                f"trade table: math error on {date_cell}: "
                f"computed ${computed:.0f}, stated ${result}"
            )

        # Stop position check.
        if stop is not None:
            if direction == "long" and stop > entry:
                reasons.append(
                    f"trade table: stop above entry on long: {date_cell}"
                )
            if direction == "short" and stop < entry:
                reasons.append(
                    f"trade table: stop below entry on short: {date_cell}"
                )

    return reasons


def _natural_date_short(d) -> str:
    try:
        day = int(d.strftime("%d"))
        return f"{d.strftime('%A, %B')} {day}, {d.strftime('%Y')}"
    except Exception:
        return ""


def _check_reframe_relevance(
    body: str, trades: list[dict[str, Any]], analysis: str,
    violations_text: str = "",
) -> list[str]:
    """The reframe must be about the rule the article is about.

    The source of truth is the RULE VIOLATIONS block from the
    skeleton — not the setups, which may not mention ATR even
    when the trade violated ATR.
    """
    reframe = _extract_reframe(body)
    if not reframe:
        return []
    reframe_kw = _tokens(reframe) & _REFRAME_KEYWORDS
    if not reframe_kw:
        return [
            "reframe references none of the article's mechanism terms "
            "(stop, ATR, clearance, sizing, regime, rule, filter)"
        ]
    source_tokens: set[str] = set()
    if violations_text:
        source_tokens |= _tokens(violations_text)
    source_tokens |= _tokens(analysis)
    for t in trades:
        source_tokens |= _tokens(t.get("setup") or "")
    source_kw = source_tokens & _REFRAME_KEYWORDS
    if reframe_kw & source_kw:
        return []
    return [
        f"reframe keywords {sorted(reframe_kw)} do not intersect the "
        f"article's rule keywords {sorted(source_kw)}"
    ]


def _check_fabricated_atr_diagnostic(
    body: str, trades: list[dict[str, Any]],
) -> list[str]:
    """A sentence claiming the ATR filter was ignored or violated
    must correspond to a trade where ATR actually exceeded the
    threshold (30, from the process document)."""
    pattern = re.compile(
        r"(ignored|violated|skipped|overrode|bypassed)[^.]{0,40}"
        r"(ATR filter|ATR <|ATR below|ATR rule)",
        re.IGNORECASE,
    )
    if not pattern.search(body or ""):
        return []
    any_above = any(
        (t.get("atr_at_entry") is not None and t["atr_at_entry"] > 30)
        for t in trades
    )
    if not any_above:
        return [
            "body claims the ATR filter was ignored, but no trade "
            "in the log has ATR above the $30 threshold"
        ]
    return []


def _check_register_notation(body: str) -> list[str]:
    """The register forbids formula notation in the prose body.
    'floor(2000 / (ATR × 20))' should be written in words."""
    reasons: list[str] = []
    if re.search(r"\bfloor\s*\(", body or ""):
        reasons.append(
            "register notation: 'floor(...)' appears in the body; "
            "state the formula in words"
        )
    if re.search(r"\b\d[\d,]*\s*/\s*\(\s*ATR\b", body or ""):
        reasons.append(
            "register notation: '/ (ATR × ...)' appears in the body; "
            "state the formula in words"
        )
    return reasons


def _check_checklist_form(body: str) -> list[str]:
    """The checklist is a contiguous run of question sentences. A
    statement — a sentence ending in a period, beginning with a
    first-person subject — sitting BETWEEN two of those questions
    is a register violation. Handles both bulleted-list and
    inline-prose forms."""
    sentences = [
        s.strip() for s in re.split(r"(?<=[.?!])\s+", body or "")
        if s.strip()
    ]
    starters_q = ("can i ", "do i ", "have i ", "did i ", "will i ")
    is_q = [s.lower().startswith(starters_q) for s in sentences]
    q_idx = [i for i, q in enumerate(is_q) if q]
    if len(q_idx) < 3:
        return []
    first, last = q_idx[0], q_idx[-1]
    for i in range(first, last + 1):
        if is_q[i]:
            continue
        s = sentences[i]
        if not s.endswith("."):
            continue
        low = s.lower()
        if low.startswith((
            "i ", "before i ", "after i ", "before ", "after ",
        )):
            return [
                f"checklist contains a statement, not a question: "
                f"'{s[:60]}…'"
            ]
    return []

def _check_gap_caveat(body: str) -> list[str]:
    """If the body claims a stop 'would have capped' or 'would have
    limited' a loss, it must include a gap caveat. A stop-loss
    cannot protect against a gap fill.

    The caveat counts whether the article says "gap", "slipped",
    "slippage", "past the stop", or "beyond the stop" anywhere in
    the body — the point is that the reader is told a stop is a
    level, not a guaranteed fill price.
    """
    if not re.search(
        r"(would have capped|would have limited|would have prevented)",
        body or "", re.IGNORECASE,
    ):
        return []
    if re.search(
        r"\b(gap|slipped|slippage|past the stop|beyond the stop)\b",
        body or "", re.IGNORECASE,
    ):
        return []
    return [
        "body claims the stop would have capped a loss but does not "
        "acknowledge that a gap could alter the fill price"
    ]


# ── ATR attribution check ────────────────────────────────────────

def _check_atr_attribution(
    body: str, trades: list[dict[str, Any]],
) -> list[str]:
    """Every 'ATR X' in the body must match the trade cited in the
    same sentence. The model has invented ATR values and attached
    them to the wrong trade (e.g. 'ATR 26.0' for a trade that ran
    at ATR 23)."""
    reasons: list[str] = []
    for m in re.finditer(
        r"ATR\s*(?:of\s*|was\s*)?(\d+(?:\.\d+)?)",
        body or "",
    ):
        cited = float(m.group(1))
        # Look for a trade date within 150 chars of the citation.
        w0 = max(0, m.start() - 150)
        w1 = min(len(body), m.end() + 150)
        window = body[w0:w1]
        dm = re.search(r"(\d{4})[-‑](\d{1,2})[-‑](\d{1,2})", window)
        if not dm:
            # Try 'May 5' / 'May 5, 2026' style.
            months = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
            }
            dm2 = re.search(
                r"(January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\s+(\d{1,2})",
                window,
                re.IGNORECASE,
            )
            if not dm2:
                continue
            year_m = re.search(r"(\d{4})", window)
            if year_m:
                year = int(year_m.group(1))
            else:
                # The body may say "May 5" without repeating the
                # year. Use the most common year among the trades.
                _years = [
                    t["traded_at"].year for t in trades
                    if t.get("traded_at") and hasattr(t["traded_at"], "year")
                ]
                if not _years:
                    continue
                year = max(set(_years), key=_years.count)
            dt = (year, months[dm2.group(1).lower()], int(dm2.group(2)))
        else:
            dt = (int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))

        trade = next(
            (t for t in trades
             if t.get("traded_at") and hasattr(t["traded_at"], "year")
             and (t["traded_at"].year,
                  t["traded_at"].month,
                  t["traded_at"].day) == dt),
            None,
        )
        if trade is None:
            continue
        trade_atr = trade.get("atr_at_entry")
        if trade_atr is None:
            continue
        if abs(cited - float(trade_atr)) > 0.1:
            reasons.append(
                f"ATR {cited} is attributed to trade "
                f"{dt[0]:04d}-{dt[1]:02d}-{dt[2]:02d}, but that trade's "
                f"ATR is {trade_atr}"
            )
    return reasons


# ── Main entry point ─────────────────────────────────────────────

def validate(
    body: str,
    analysis: str,
    trades: list[dict[str, Any]],
    account_equity: float | None = None,
    performance: dict[str, Any] | None = None,
    violations_text: str = "",
    skeleton_text: str = "",
) -> list[str]:
    """Return rejection reasons. Empty list = article passes."""
    reasons: list[str] = []

    if not body:
        return ["empty body"]

    # 1. Every dollar figure must be in the source material.
    allowed_d = _allowed_dollars(analysis, trades, account_equity,
                                  performance)
    for n in sorted(_dollars_from(body)):
        if n not in allowed_d:
            reasons.append(f"fabricated dollar figure: ${n:,}")

    # 2. Every percent figure must be in the source material.
    # The skeleton text is threaded in — it carries the trade's
    # percent_of_equity, which the model quotes directly.
    allowed_p = _allowed_percents(
        analysis, trades, account_equity, skeleton_text=skeleton_text,
    )
    for p in sorted(_percents_from(body)):
        # 0.05 tolerance — 0.17 matches 0.2 matches 0.15; a true
        # fabrication (0.9% when nothing supports it) still fails.
        if not any(abs(p - a) < 0.05 for a in allowed_p):
            reasons.append(f"unsourced percent: {p}%")

    # 3. The reframe must not be a variant of a forbidden mechanism.
    reframe_reason = _semantic_reframe_violation(body)
    if reframe_reason:
        reasons.append(reframe_reason)

    # 4. A VIOLATED trade must not be cited for a different rule.
    for t in trades:
        if (t.get("clearance") is not None
                and t["clearance"] < 0.5):
            date = t.get("traded_at")
            if date and hasattr(date, "strftime"):
                ds = date.strftime("%Y-%m-%d")
                if ds in body:
                    idx = body.find(ds)
                    window = body[max(0, idx - 200): idx + 300]
                    if "clearance" not in window.lower():
                        reasons.append(
                            f"trade {ds} is VIOLATED (clearance) but cited "
                            f"outside a clearance sentence"
                        )

    # 5–11. Structural checks.
    reasons.extend(_check_hook_stop_state(body, trades))
    reasons.extend(_check_irrelevant_rule(body, trades))
    reasons.extend(_check_trade_table(body, trades))
    reasons.extend(_check_diagnostics_beat(body))
    reasons.extend(_check_reframe_relevance(body, trades, analysis, violations_text))
    reasons.extend(_check_fabricated_atr_diagnostic(body, trades))
    reasons.extend(_check_register_notation(body))
    reasons.extend(_check_checklist_form(body))
    reasons.extend(_check_gap_caveat(body))
    reasons.extend(_check_atr_attribution(body, trades))

    return reasons
