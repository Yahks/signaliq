"""market_condition_validator: checks for the market_condition subblock.

Separate from article_validator.validate(), which is trade-centric
(batch stats, trade table, hook stop state, ATR attribution, floor
claim) and would mis-fire on a regime note that has no trade table.

Every check here compares the article to the SKELETON the model was
given (market_conditions_skeleton.build_conditions_skeleton), the
same way article_validator._check_batch_stats_match compares to the
skeleton's BATCH PERFORMANCE block. The premise: the model may
quote the skeleton, and must not contradict it.
"""
from __future__ import annotations

import re
from typing import Any


_REGIME_NAMES = (
    "Compressed-Volatility",
    "Expanded-Volatility",
    "Thin-Liquidity",
    "High-Correlation",
    "Narrow-Breadth",
    "Elevated-Volatility-Index",
    "Crowded-Longs",
)


def _skeleton_regime_states(skeleton: str) -> dict[str, str]:
    """Parse ACTIVE / INACTIVE / UNKNOWN from the REGIMES block."""
    states: dict[str, str] = {}
    for line in skeleton.splitlines():
        m = re.match(r"\s+(" + "|".join(re.escape(n) for n in _REGIME_NAMES)
                     + r"):\s+(ACTIVE|INACTIVE|UNKNOWN)\b", line)
        if m:
            states[m.group(1)] = m.group(2)
    return states


def _skeleton_readings(skeleton: str) -> dict[str, str]:
    """Parse the READINGS block: name -> numeric string."""
    out: dict[str, str] = {}
    for line in skeleton.splitlines():
        m = re.match(r"\s+([a-z_]+):\s+(-?[\d.]+)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _skeleton_active_rules(skeleton: str) -> list[str]:
    """The rules listed under ACTIVE_RULES, stripped of the
    '[Regime] ' prefix. Used to check that an applied rule came
    from an active regime, not a dormant one."""
    rules: list[str] = []
    in_block = False
    for line in skeleton.splitlines():
        if line.startswith("ACTIVE_RULES"):
            in_block = True
            continue
        if in_block:
            if not line.strip():
                break
            if line.startswith("  ["):
                rules.append(line.split("]", 1)[1].strip())
    return rules


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _rule_in_article(rule: str, body: str) -> bool:
    """Loose match: does the article contain a distinctive
    fragment of this rule? Rules are paraphrased by the model, so
    match on a few keywords rather than the exact string."""
    b = _normalize(body)
    keywords = [
        k for k in (
            "cap total concurrent units", "concurrent units at three",
            "concurrent units at 3", "cut base size", "0.5 unit",
            "tighten stops", "suspend the 0.75% stop-loss",
            "skip the lowest-edge", "skip the day's scan",
            "raise the stop-loss to 1%", "split orders",
            "limit orders", "exit any open long", "ban new longs",
            "raise the profit target to 2%", "widen the trailing stop",
        ) if k in _normalize(rule)
    ]
    if not keywords:
        return False
    return any(k in b for k in keywords)


def _check_metrics_sourced(body: str, skeleton: str) -> list[str]:
    """Every metric the article cites must be a skeleton reading.

    The reviewer's central complaint: numbers with no source. The
    skeleton is the only source; a metric the article quotes that
    is not in READINGS is fabricated (or a unit variant we can't
    verify). We match on the numeric value: each number in the
    body that looks like a metric must appear in the skeleton's
    READINGS block.

    This is deliberately narrow: it only inspects numbers that
    carry a metric-ish unit (%/day, bp, or bare correlation in
    0..1), not every number in the prose (dates, counts of
    trades, etc. are permitted).
    """
    reasons: list[str] = []
    # The allowed set is every number in the skeleton, not just the
    # five READINGS values. Rule thresholds ("vol < 0.60%/day",
    # "net-long below 70%") live in the Threshold detail and rule
    # text, and the article may cite them. Matching only READINGS
    # flagged correct rule thresholds as fabricated (0.60%, 1.2%,
    # 70%, 0.75%, 1%).
    # Numeric, not string: the body says "0.80%" and the skeleton
    # prints "0.8" (trailing zero stripped) — same number, different
    # strings. Compare parsed floats with a tight tolerance so
    # 0.80 matches 0.8 but a genuinely unsourced 0.93 still fails.
    allowed_floats = []
    for tok in re.findall(r"-?\d+(?:\.\d+)?", skeleton):
        try:
            allowed_floats.append(float(tok))
        except ValueError:
            pass
    # Percent-per-day and bp readings.
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:%|bp|percent)", body or ""):
        v = m.group(1)
        try:
            _vf = float(v)
        except ValueError:
            continue
        if any(abs(_vf - a) < 1e-9 for a in allowed_floats):
            continue
        # A bare integer like "0.5 unit" or "2%" from a rule is fine;
        # only flag readings above 0.0 that don't appear in the
        # skeleton AND are followed by a metric unit AND sit in a
        # sentence with a metric name (correlation/volatility/spread/
        # range/breadth). This avoids flagging rule thresholds.
        window = body[max(0, m.start() - 80):m.end() + 80].lower()
        if any(k in window for k in (
            "correlation", "volatility", "realized vol",
            "spread", "range", "breadth", "candidate count",
        )):
            reasons.append(
                f"metric reading {v}{m.group(0)[len(v):].strip()} "
                f"is not in the skeleton's READINGS block"
            )
    return reasons


def _check_regime_claims(body: str, skeleton: str) -> list[str]:
    """A regime the article asserts must match the skeleton state.

    - claims "X regime active" but skeleton says INACTIVE → reject
    - claims "X regime inactive" but skeleton says ACTIVE → reject
    - asserts either way for a regime the skeleton marks UNKNOWN
      → reject (the reading is absent; the article must say so)
    """
    reasons: list[str] = []
    states = _skeleton_regime_states(skeleton)
    b = _normalize(body)
    for name, state in states.items():
        key = name.lower().replace("-", " ")
        # look for "<regime>" within 60 chars of "active"/"inactive"
        for m in re.finditer(re.escape(key), b):
            window = b[m.start(): m.end() + 60]
            claims_active = bool(re.search(r"\b(active|in force|in effect)\b", window))
            claims_inactive = bool(re.search(r"\b(inactive|not in force|dormant)\b", window))
            if state == "ACTIVE" and claims_inactive:
                reasons.append(
                    f"article calls {name} inactive, but the skeleton "
                    f"shows it ACTIVE"
                )
            elif state == "INACTIVE" and claims_active:
                reasons.append(
                    f"article calls {name} active, but the skeleton "
                    f"shows it INACTIVE"
                )
            elif state == "UNKNOWN" and (claims_active or claims_inactive):
                reasons.append(
                    f"article asserts {name} is {'active' if claims_active else 'inactive'}, "
                    f"but the skeleton marks it UNKNOWN (reading absent) — "
                    f"state that the reading is unavailable"
                )
    return reasons


def _check_rules_from_active(body: str, skeleton: str) -> list[str]:
    """A process adjustment applied in the article must come from
    an ACTIVE regime's rule set. Applying the Narrow-Breadth
    scan-skip while Narrow-Breadth is INACTIVE (or the
    Elevated-VIX stop-widening while that regime is UNKNOWN) is
    the reviewer's Errors 6 and 7."""
    reasons: list[str] = []
    active_rules = _skeleton_active_rules(skeleton)
    # All rules in the skeleton (active + inactive) — we only flag
    # a rule that is NOT in active_rules.
    all_rules = list(active_rules)
    for name in _REGIME_NAMES:
        # A rule from an inactive regime is not listed under
        # ACTIVE_RULES; we can detect it by the rule fragment in
        # the article plus the regime name appearing nearby.
        pass
    # Positive check: any rule fragment that appears in the body
    # and is NOT in an active rule → reject.
    INACTIVE_RULE_FRAGMENTS = {
        "skip the day's scan": "Narrow-Breadth",
        "skip the scan": "Narrow-Breadth",
        "raise the stop-loss to 1%": "Elevated-Volatility-Index",
        "raise my stop-loss to 1%": "Elevated-Volatility-Index",
        "split orders": "Thin-Liquidity",
        "split any order": "Thin-Liquidity",
        "limit-order discipline": "Thin-Liquidity",
        "exit any open long": "Crowded-Longs",
        "ban new longs": "Crowded-Longs",
        "raise the profit target to 2%": "Expanded-Volatility",
        "widen the trailing stop": "Expanded-Volatility",
        "keep full size": "Expanded-Volatility",
    }
    b = _normalize(body)
    states = _skeleton_regime_states(skeleton)
    for frag, regime in INACTIVE_RULE_FRAGMENTS.items():
        if frag in b:
            st = states.get(regime, "UNKNOWN")
            if st != "ACTIVE":
                reasons.append(
                    f"article applies the {regime} rule '{frag}', "
                    f"but the skeleton shows {regime} as {st} — "
                    f"that rule is not in force"
                )
    return reasons


def _check_no_persistence_claim(body: str, skeleton: str) -> list[str]:
    """The skeleton says: single snapshot, no persistence. The
    article must not claim the regime has persisted for N days or
    that correlation 'has been above 0.50 for N days'."""
    if "SINGLE snapshot" not in skeleton:
        return []
    reasons: list[str] = []
    patterns = (
        r"has been above [\d.]+ for \d+ (?:days|sessions|weeks)",
        r"(?:for|over) (?:the )?(?:last|past) \d+ (?:days|sessions|weeks)"
        r"[^.]{0,60}(?:above|below) [\d.]+",
        r"persist(?:s|ed|ing)? for \d+ (?:days|sessions|weeks)",
        r"correlation has (?:stayed|remained) (?:above|below)",
    )
    for pat in patterns:
        if re.search(pat, body or "", re.IGNORECASE):
            reasons.append(
                "article claims regime persistence ('for N days'), "
                "but the skeleton is a single snapshot with no "
                "history — do not claim persistence"
            )
            break
    return reasons


def _check_no_cta(body: str) -> list[str]:
    """No CTA, no URL (the market_condition prompt previously
    hardcoded one; it has been removed)."""
    if re.search(
        r"(?i)\b(?:email me|reach out|sign up|subscribe|follow me|"
        r"dm me|link in bio|click (?:here|the link))\b",
        body or "",
    ):
        return ["body contains a CTA / marketing line"]
    if re.search(r"https?://(?!signaliq\.com/static)", body or ""):
        return ["body contains a URL"]
    return []


def _skeleton_linkage(skeleton: str) -> dict[str, str] | None:
    """Parse n_trades, wins, losses, net, and window-end from the
    TRADE LINKAGE block."""
    m = re.search(
        r"TRADE LINKAGE.*?window:\s*(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2}).*?"
        r"n_trades:\s*(\d+)\s*\(wins\s*(\d+),\s*losses\s*(\d+),\s*net\s*\$(-?[\d,.]+)\)",
        skeleton, re.DOTALL,
    )
    if not m:
        return None
    return {
        "start": m.group(1), "end": m.group(2),
        "n": m.group(3), "wins": m.group(4),
        "losses": m.group(5), "net": m.group(6),
    }


def _skeleton_top_symbols(skeleton: str) -> dict[str, int]:
    """Parse the top-symbols line: 'BA (12), Q (11), DOW (10), ...'"""
    m = re.search(r"top symbols:\s*(.+)", skeleton)
    if not m:
        return {}
    out: dict[str, int] = {}
    for sym, cnt in re.findall(r"([A-Z][A-Z0-9.-]*)\s*\((\d+)\)", m.group(1)):
        out[sym] = int(cnt)
    return out


def _check_linkage_numbers(body: str, skeleton: str) -> list[str]:
    """Trade-linkage figures in the article must match the skeleton.

    Two checks:
      - the TOTAL trade count, win/loss split, and net must match
        the linkage block. A bare "N trades" is only a total claim
        when it is qualified ("N total trades", "recorded N
        trades") — a per-symbol "DOW with 10 trades" is not.
      - each per-symbol count ("SYM with N trades") must appear in
        the skeleton's top-symbols list with that count.

    The stored article fabricated both: "583 total trades, 204
    wins, 379 losses, net -$86,078" against 678/239/439/
    -$110,842, and per-symbol counts (CMCSA 12, SBAC 10) for
    symbols not in the skeleton's top five.
    """
    lk = _skeleton_linkage(skeleton)
    if not lk:
        return []
    reasons: list[str] = []
    b = body or ""

    # ── Total trade count: only qualified totals. ──
    total_pats = (
        r"(\d[\d,]*)\s+total\s+trades",
        r"(?:recorded|logged|took|closed|executed)\s+(\d[\d,]*)\s+trades",
        r"(\d[\d,]*)\s+trades\s+(?:in|across|over)\s+(?:the|my|this)\s+",
    )
    seen_total = False
    for pat in total_pats:
        for m in re.finditer(pat, b, re.IGNORECASE):
            seen_total = True
            v = m.group(1).replace(",", "")
            if v != lk["n"]:
                reasons.append(
                    f"trade-linkage mismatch: article total {v} trades, "
                    f"skeleton {lk['n']}"
                )
    # wins / losses / net — only when a total was claimed, else a
    # per-symbol context could be misread.
    if seen_total or re.search(r"\bwins?\b.*\blosses?\b", b, re.IGNORECASE):
        for m in re.finditer(r"(\d[\d,]*)\s+wins?\b", b, re.IGNORECASE):
            v = m.group(1).replace(",", "")
            if v != lk["wins"]:
                reasons.append(
                    f"trade-linkage mismatch: article {v} wins, "
                    f"skeleton {lk['wins']}"
                )
        for m in re.finditer(r"(\d[\d,]*)\s+losses?\b", b, re.IGNORECASE):
            v = m.group(1).replace(",", "")
            if v != lk["losses"]:
                reasons.append(
                    f"trade-linkage mismatch: article {v} losses, "
                    f"skeleton {lk['losses']}"
                )
    try:
        _sk_net = float(lk["net"].replace(",", ""))
    except ValueError:
        _sk_net = None
    for m in re.finditer(
        r"net\s+(loss|gain|P&L|pnl)?[^.$]{0,40}\$(-?[\d,]+(?:\.\d+)?)",
        b, re.IGNORECASE,
    ):
        qualifier = (m.group(1) or "").lower()
        raw = m.group(2).replace(",", "").rstrip(".")
        try:
            art_net = float(raw)
        except ValueError:
            continue
        # The qualifier carries the sign: "a net loss of $89,439.54"
        # means -89439.54. Without this, a correct sentence was
        # rejected (548b8742: "net loss of $89,439.54" vs skeleton
        # net -89439.54).
        if qualifier == "loss":
            art_net = -abs(art_net)
        elif qualifier == "gain":
            art_net = abs(art_net)
        if _sk_net is not None and abs(art_net - _sk_net) > 0.01:
            reasons.append(
                f"trade-linkage mismatch: article net {art_net:+.2f}, "
                f"skeleton net {_sk_net:+.2f}"
            )

    # ── Per-symbol counts: each must be in the skeleton's list. ──
    top = _skeleton_top_symbols(skeleton)
    for m in re.finditer(
        r"\b([A-Z][A-Z0-9.-]*)\s+(?:with|at|:)\s*(\d+)\s+trades\b", b
    ):
        sym, cnt = m.group(1), int(m.group(2))
        if sym not in top:
            reasons.append(
                f"trade-linkage mismatch: article cites {sym} with {cnt} "
                f"trades, but {sym} is not in the skeleton's top symbols"
            )
        elif top[sym] != cnt:
            reasons.append(
                f"trade-linkage mismatch: article says {sym} {cnt} trades, "
                f"skeleton says {top[sym]}"
            )

    return reasons
def _check_no_checklist(body: str) -> list[str]:
    """The checklist beat was removed. Reject a run of Confirm.../
    Verify... statements or the literal word Checklist."""
    b = body or ""
    if re.search(r"\bchecklist\b", b, re.IGNORECASE):
        return ["article contains a checklist beat — removed from this subblock"]
    confirms = len(re.findall(r"(?:^|\n)\s*(?:Confirm|Verify|Check)\b", b))
    if confirms >= 3:
        return [f"article contains a {confirms}-item Confirm/Verify list — checklist removed"]
    return []


async def load_frozen_skeleton(session, post_id: str) -> str:
    """Return the skeleton a post was generated against.

    The skeleton is stored in source_payload at generation time so
    re-validation compares against what the model actually saw,
    not a freshly rebuilt skeleton (whose trade-linkage figures
    drift as new articles link trades into the same window).
    """
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


def _check_derived_analysis_stated(body: str, skeleton: str) -> list[str]:
    """If the skeleton carries a DERIVED ANALYSIS block, the article
    must state the win rate and the equity impact. The reviewer's #1
    omission was reporting raw counts (678 trades, 239/439) and never
    computing win rate, expectancy, or drawdown.
    """
    if "DERIVED ANALYSIS" not in skeleton:
        return []
    reasons: list[str] = []
    # pull win_rate_pct and equity_impact_pct out of the skeleton
    m_wr = re.search(r"win_rate_pct:\s*([\d.]+)%", skeleton)
    m_dd = re.search(r"equity_impact_pct:\s*(-?[\d.]+)%", skeleton)
    if m_wr:
        wr = m_wr.group(1)
        # accept "35.25" or "35.2" or "35%"
        if not re.search(re.escape(wr[:4]) + r"\d*\s*%", body or ""):
            reasons.append(
                f"DERIVED ANALYSIS present but the article does not "
                f"state the win rate ({wr}%)"
            )
    if m_dd:
        dd = m_dd.group(1).lstrip("-")
        if not re.search(re.escape(dd[:3]) + r"\d*\s*%", body or ""):
            reasons.append(
                f"DERIVED ANALYSIS present but the article does not "
                f"state the equity impact ({dd}%)"
            )
    return reasons


def _check_rule_conflict_resolved(body: str, skeleton: str) -> list[str]:
    """If the skeleton flags a stop tighten/suspend conflict, the
    article must not present both as active."""
    if "RULE_CONFLICT" not in skeleton:
        return []
    b = (body or "").lower()
    # A conflict is a rejection only if the two appear in the SAME
    # SENTENCE. Across sentences ("I tighten the stop. Elsewhere the
    # rule is suspended.") is a coherent statement of two regimes;
    # within one sentence ("I tighten stops while suspending the
    # rule, which resolves in favor of tightening") is the
    # contradiction the reviewer flagged — it names a resolution but
    # still contains both policies.
    for sentence in re.split(r"(?<=[.!?])\s+", b):
        # Allow inflections: the article writes "while
        # suspending the 0.75% stop-loss rule", and suspend\b does
        # not match "suspending" (word boundary fails mid-word).
        if (re.search(r"tighten(?:s|ing|ed)?\b.{0,60}?\bstop", sentence)
                and re.search(r"suspend(?:s|ing|ed)?\b.{0,60}?\bstop", sentence)):
            return [
                "the article puts a tightened stop and a suspended stop "
                "in the same sentence — the resolution supersedes the "
                "rule, it does not suspend it. Rewrite: 'the 0.5% stop "
                "replaces the 0.75% rule.'"
            ]
    return []


def _check_window_dates_stated(body: str, skeleton: str) -> list[str]:
    """If the skeleton carries window_start/window_end, both must
    appear in the body."""
    m = re.search(
        r"window_start:\s*(\d{4}-\d{2}-\d{2}).*?"
        r"window_end:\s*(\d{4}-\d{2}-\d{2})",
        skeleton, re.DOTALL,
    )
    if not m:
        return []
    reasons: list[str] = []
    for label, d in (("window_start", m.group(1)), ("window_end", m.group(2))):
        # accept the date in ISO form or as "Month D, YYYY"
        y, mo, da = d.split("-")
        _months = ("January","February","March","April","May","June",
                   "July","August","September","October","November","December")
        spelled = f"{_months[int(mo)-1]} {int(da)}, {y}"
        if d not in body and spelled not in body:
            reasons.append(
                f"the skeleton states {label} {d} but the article does "
                f"not name it"
            )
    return reasons


def _check_per_symbol_reported(body: str, skeleton: str) -> list[str]:
    """If the skeleton carries per_symbol_pnl, the article must name
    at least one symbol from it, so the concentration of the loss is
    visible."""
    if "per_symbol_pnl" not in skeleton:
        return []
    syms = re.findall(r"^\s{4}([A-Z][A-Z0-9.-]*):\s+\d+ trades",
                      skeleton, re.MULTILINE)
    if not syms:
        return []
    if any(s in body for s in syms):
        return []
    return [
        "the skeleton carries a per-symbol P&L breakdown but the "
        "article names none of its symbols — report which names lost"
    ]


def _check_significance_stated(body: str, skeleton: str) -> list[str]:
    """If the skeleton carries a win_rate_95ci_pp, the article must
    say something about significance (CI, n, or conclusive)."""
    if "win_rate_95ci_pp" not in skeleton:
        return []
    b = body or ""
    if re.search(r"(\b95\s*%?\s*(CI|confidence)|\bconfidence interval|"
                 r"\bstatistically (conclusive|significant)|"
                 r"\bwithin (the )?(noise|sampling)|\bn\s*=\s*\d+)",
                 b, re.IGNORECASE):
        return []
    return [
        "the skeleton gives a 95% CI on the win rate but the article "
        "makes no significance statement"
    ]


def _check_budget_fork_committed(body: str, skeleton: str) -> list[str]:
    """If the skeleton's WARNING asks the article to pick a branch,
    'either ... or' is a rejection."""
    if "budget was not enforced" not in skeleton and \
       "pre-rule trades" not in skeleton:
        return []
    b = body or ""
    if re.search(r"either[^.]{0,80}(budget|pre-rule)", b, re.IGNORECASE):
        return [
            "the article writes 'either/or' on the budget fork — pick "
            "one branch and commit to it"
        ]
    # And require a branch: the WARNING asks the article to reconcile
    # the drawdown, so one of the two explanations must appear.
    _committed = (
        re.search(r"budget (was )?not (been )?enforced", b, re.IGNORECASE)
        or re.search(r"pre-rule|pre\s*rule|before the rule", b, re.IGNORECASE)
    )
    if not _committed:
        return [
            "the skeleton's WARNING requires reconciling the drawdown "
            "against the risk budget; the article names neither branch "
            "('budget was not enforced' or 'pre-rule trades')"
        ]
    return []


def validate_market_condition(
    body: str,
    skeleton: str,
) -> list[str]:
    """Return rejection reasons. Empty = pass."""
    if not body:
        return ["empty body"]
    if not skeleton:
        return ["market_condition validator: no skeleton supplied"]
    reasons: list[str] = []
    reasons.extend(_check_metrics_sourced(body, skeleton))
    reasons.extend(_check_regime_claims(body, skeleton))
    reasons.extend(_check_rules_from_active(body, skeleton))
    reasons.extend(_check_no_persistence_claim(body, skeleton))
    reasons.extend(_check_linkage_numbers(body, skeleton))
    reasons.extend(_check_derived_analysis_stated(body, skeleton))
    reasons.extend(_check_rule_conflict_resolved(body, skeleton))
    reasons.extend(_check_window_dates_stated(body, skeleton))
    reasons.extend(_check_per_symbol_reported(body, skeleton))
    reasons.extend(_check_significance_stated(body, skeleton))
    reasons.extend(_check_budget_fork_committed(body, skeleton))
    reasons.extend(_check_no_checklist(body))
    reasons.extend(_check_no_cta(body))
    return reasons
