"""Quality gates for market_analysis articles.

Two layers: block validation (before LLM) and article
verification (before persist). Every failure is logged with
a named reason in market_analysis_rejections.

At 500/week, the rejection table is the source of truth for
"what's failing." Query it to see the distribution.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

# Access the same data helpers the chart renderers use so the
# CHARTS block describes the exact numbers the .png files show.
from ..scrapers.market_history import _percentile

log = get_logger(__name__)


async def log_rejection(
    session: AsyncSession,
    *,
    org_id: str,
    symbol: str | None,
    timeframe: str | None,
    stage: str,
    reason: str,
    detail: str | None = None,
    block_snapshot: dict | None = None,
    article_body: str | None = None,
) -> None:
    """Record a rejection. Never raises — a logging failure does
    not fail the caller."""
    try:
        await session.execute(sql_text("""
            INSERT INTO market_analysis_rejections
                (org_id, symbol, timeframe, stage, reason,
                 detail, block_snapshot, article_body)
            VALUES
                (:oid, :sym, :tf, :stage, :reason,
                 :detail, CAST(:snap AS jsonb), :body)
        """), {
            "oid": org_id,
            "sym": symbol,
            "tf": timeframe,
            "stage": stage,
            "reason": reason,
            "detail": detail,
            "snap": json.dumps(block_snapshot, default=str) if block_snapshot else None,
            "body": article_body,
        })
        await session.commit()
        log.warning(
            "market_analysis.rejected",
            symbol=symbol, timeframe=timeframe,
            stage=stage, reason=reason,
            detail=detail,
        )
    except Exception as exc:
        log.error(
            "market_analysis.log_rejection_failed",
            error=str(exc)[:200],
            stage=stage, reason=reason,
        )


# ──── Layer 1: block validation ────

def validate_block(block: dict[str, Any] | None) -> list[str]:
    """Return named errors. Empty list means the block is valid.

    Checks every field the article template requires. If a field
    is missing, the LLM will invent it.
    """
    if not block:
        return ["missing_block"]

    errors: list[str] = []

    if not block.get("symbol") or not str(block.get("symbol")).strip():
        errors.append("missing_symbol")
    if not block.get("timeframe") or not str(block.get("timeframe")).strip():
        errors.append("missing_timeframe")

    if block.get("entry_time") is None:
        errors.append("missing_entry_time")
    if block.get("exit_time") is None:
        errors.append("missing_exit_time")

    for field in ("entry", "stop", "target", "exit"):
        v = block.get(field)
        if v is None:
            errors.append(f"missing_{field}")
        else:
            try:
                float(v)
            except (TypeError, ValueError):
                errors.append(f"invalid_{field}")

    if block.get("result") is None:
        errors.append("missing_result")
    if block.get("contracts") is None:
        errors.append("missing_contracts")
    if block.get("atr_at_entry") is None:
        errors.append("missing_atr")

    reason = block.get("exit_reason")
    if not reason:
        errors.append("missing_exit_reason")
    elif reason not in ("stop hit", "target hit", "time exit",
                        "manual close", "market move"):
        errors.append(f"unknown_exit_reason:{reason}")

    return errors


# ──── Layer 2: article verification ────

_TIME_RE = re.compile(
    r"\b\d{1,2}:\d{2}\s*(?:[AaPp][Mm]|[Uu][Tt][Cc]|[Cc][Tt]|[Ee][Tt]|[Ee][Ss][Tt])"
)
_PRICE_RE = re.compile(r"\b\d{2,4}\.\d{2}\b")
_DOLLAR_RE = re.compile(r"\$\d[\d,]*")
_PCT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
_EMOTION_PATTERNS = [
    # Actions that name an emotional decision
    r"\b(?:I (?:ignored|refused|couldn'?t|could not|wanted|told myself|let|let my|let the|convinced myself))\b",
    # Physical reactions
    r"\b(?:my (?:hand|heart|jaw|breath)|I (?:froze|panicked|hesitated))\b",
    # Internal states
    r"\b(?:denial|refus(?:al|ed)|ego|tilt|impulse|tempted)\b",
    r"\b(?:I was (?:angry|frustrated|scared|afraid|desperate|ashamed))\b",
    # Force / override
    r"\b(?:I (?:forced|had to force|pushed|made myself))\b",
]
_META_PATTERNS = [
    r"\bin this (?:article|post|piece)\b",
    r"\blet me show you\b",
    r"\bby the end you'?ll\b",
    r"\bI wrote this (?:post|article|piece)\b",
    r"\bthis (?:article|post|piece) (?:will|shows|explains)\b",
]
_SOURCE_REF_PATTERNS = [
    r"\bthe (?:trade block|source block|payload) (?:says|shows|reads|states)\b",
    r"\bthe (?:pattern|setup detail|hesitation note) (?:says|shows|reads|states)\b",
]


def _extract_trade_numbers(article: str, block: dict) -> list[str]:
    """Extract prices from the article that don't appear in the block."""
    invented = []
    block_str = json.dumps(block, default=str)
    for m in _PRICE_RE.finditer(article):
        num = m.group(0)
        if num not in block_str:
            invented.append(num)
    return invented


def verify_article(
    article: str, block: dict[str, Any],
    source_block: str | None = None,
) -> list[str]:
    """Return named errors. Empty list means the article is valid."""
    if not article:
        return ["empty_article"]

    errors: list[str] = []
    word_count = len(article.split())

    if word_count < 630:
        errors.append(f"article_too_short:{word_count}")
    if word_count > 1500:
        errors.append(f"article_too_long:{word_count}")

    time_matches = _TIME_RE.findall(article)
    if len(time_matches) < 2:
        errors.append("precise_times_missing")

    price_matches = _PRICE_RE.findall(article)
    if len(price_matches) < 3:
        errors.append("exact_prices_missing")

    if not _DOLLAR_RE.search(article):
        errors.append("account_dollars_missing")
    if not _PCT_RE.search(article):
        errors.append("account_percent_missing")

    has_emotion = False
    for pattern in _EMOTION_PATTERNS:
        if re.search(pattern, article, re.IGNORECASE):
            has_emotion = True
            break
    if not has_emotion:
        errors.append("emotional_moment_missing")

    for pattern in _META_PATTERNS:
        if re.search(pattern, article, re.IGNORECASE):
            errors.append("meta_commentary")
            break

    for pattern in _SOURCE_REF_PATTERNS:
        if re.search(pattern, article, re.IGNORECASE):
            errors.append("source_reference")
            break

    # Check against the full source block if provided. A
    # price that appears anywhere in the source (trade, stock
    # report, dashboard) is not invented. Only prices that
    # appear in NEITHER are truly hallucinated.
    check_target = source_block if source_block else json.dumps(block, default=str)
    invented = []
    for m in _PRICE_RE.finditer(article):
        num = m.group(0)
        if num not in check_target:
            invented.append(num)
    if invented:
        errors.append(f"invented_numbers:{','.join(invented[:5])}")

    return errors


# ──── EXACT NUMBERS list ────

def build_trade_facts(t: dict[str, Any]) -> str | None:
    """Build the THE TRADE section in trader notation.

    Returns None if the trade data is internally inconsistent
    (result doesn't match exit-entry x shares). The caller
    should skip to the next trade rather than let the model
    reconcile broken data.

    Trader notation, not form labels:
        SBAC short 183.76
        stop 208.02 (2.71 ATR)
        target 135.24 (2.0R)
        size 100 sh
        exit 184.03
        2026-06-22  14:46 -> 17:16 (2h30)
        result: scratch (-27.00 USD, -0.01R)
        ATR at entry: 8.96 pts
        stop distance: 24.26 (13.2% of entry)
    """
    from decimal import Decimal, ROUND_HALF_UP

    def _q(x):
        try:
            return Decimal(str(x)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
        except Exception:
            return Decimal("0.00")

    entry = _q(t["entry"])
    stop = _q(t["stop"])
    target = _q(t["target"])
    exit_ = _q(t["exit"])
    atr = _q(t["atr_at_entry"]) if t.get("atr_at_entry") else Decimal("0")
    shares = int(t["contracts"]) if t.get("contracts") else 0
    side = t.get("direction", "long")
    result = _q(t["result"]) if t.get("result") is not None else Decimal("0")
    sign = -1 if side == "short" else 1

    # Data integrity check. If the result doesn't match
    # (exit - entry) * sign * shares, the trade data is
    # internally inconsistent. Return None so the caller
    # can skip to the next trade.
    per_share = (exit_ - entry) * sign
    expected_result = per_share * shares
    if int(expected_result) != int(result):
        return None

    stop_dist = abs(stop - entry)
    stop_atr = (stop_dist / atr) if atr else Decimal("0")
    target_dist = abs(target - entry)
    target_r = (target_dist / stop_dist) if stop_dist else Decimal("0")
    result_r = (per_share / stop_dist) if stop_dist else Decimal("0")

    # Outcome label. Scratch for trades within 5% of flat.
    # Target for trades above 1.5R. Stop for trades below -0.95R.
    if abs(result_r) < Decimal("0.05"):
        outcome = "scratch"
    elif result_r > Decimal("1.5"):
        outcome = "target"
    elif result_r < Decimal("-0.95"):
        outcome = "stop"
    else:
        outcome = "partial"

    def _fmt_time(dt):
        if dt is None:
            return ""
        try:
            return dt.strftime("%H:%M")
        except Exception:
            return str(dt)[:5]

    def _duration(t0, t1):
        if t0 is None or t1 is None:
            return ""
        try:
            mins = int((t1 - t0).total_seconds() // 60)
            h, m = divmod(mins, 60)
            return f"{h}h{m:02d}"
        except Exception:
            return ""

    entry_t = _fmt_time(t.get("entry_time"))
    exit_t = _fmt_time(t.get("exit_time"))
    held = _duration(t.get("entry_time"), t.get("exit_time"))

    trade_date = ""
    try:
        if t.get("entry_time"):
            trade_date = t["entry_time"].strftime("%Y-%m-%d")
        elif t.get("traded_at"):
            trade_date = str(t["traded_at"])[:10]
    except Exception:
        pass

    lines = [
        "--- THE TRADE ---",
        f"{t.get('symbol', '?')} {side} {entry}",
        f"stop {stop} ({stop_atr:.2f} ATR)",
        f"target {target} ({target_r:.1f}R)",
        f"size {shares} sh",
        f"exit {exit_}",
    ]
    if trade_date and entry_t and exit_t:
        lines.append(f"{trade_date}  {entry_t} -> {exit_t} ({held})")
    lines.append(
        f"result: {outcome} ({result:+.2f} USD, {result_r:+.2f}R)"
    )
    if atr:
        lines.append(f"ATR at entry: {atr} pts")
    if entry:
        lines.append(
            f"stop distance: {stop_dist} "
            f"({stop_dist / entry * 100:.1f}% of entry)"
        )

    lines.append("")
    lines.append(
        "Every number above is the trade's own. Do not add "
        "$ signs or 'USD per share' or other decorations. The "
        "units are in the labels (sh, ATR, %, R)."
    )
    return "\n".join(lines)





# ──── CHARTS block ────

async def build_charts_block(
    session: AsyncSession, *,
    org_id: str, symbol: str, timeframe: str,
    as_of=None,
) -> str:
    """Build the CHARTS section from the same data the chart
    renderers use. Describes five charts with the numbers they
    show as of the trade's entry time.

    When `as_of` is set, every window ends at that date. The
    series is truncated to bars with trade_date <= as_of, so
    closes[-1] is the entry bar's close, recent[-60:] is the
    60 bars before entry, and setup history only counts setups
    whose forward window ended before entry.

    Text only. No image, no graphic. The model reads this text
    and references the charts in prose.

    Returns "" if the price series is too short.
    """
    import math

    _asof_clause = ""
    _params = {"oid": org_id, "sym": symbol, "tf": timeframe}
    if as_of is not None:
        _asof_date = as_of.date() if hasattr(as_of, "date") else as_of
        _asof_clause = "AND trade_date <= :asof "
        _params["asof"] = _asof_date

    bars = (await session.execute(sql_text(
        "SELECT trade_date, close FROM market_history "
        "WHERE org_id = :oid AND symbol = :sym "
        "AND timeframe = :tf AND deleted_at IS NULL "
        + _asof_clause +
        "ORDER BY trade_date ASC"
    ), _params)).mappings().all()

    if len(bars) < 3:
        return ""

    closes = [float(b["close"]) for b in bars]
    dates = [str(b["trade_date"]) for b in bars]

    lines = [
        "--- CHARTS (evidence the article can reference) ---",
        "",
        "Five charts accompany this article. Each is evidence "
        "the article can reference wherever the argument needs "
        "it. Reference at least two by name. State what the "
        "chart shows and what it means for the trade.",
        "",
    ]

    # 1. Daily view.
    current = closes[-1]
    recent = closes[-60:] if len(closes) >= 60 else closes
    recent_dates = dates[-60:] if len(dates) >= 60 else dates
    hi = max(recent); hi_i = recent.index(hi)
    lo = min(recent); lo_i = recent.index(lo)
    change_pct = ((recent[-1] - recent[0]) / recent[0]) * 100.0 if recent[0] else 0.0
    trend = "up" if change_pct > 0 else ("down" if change_pct < 0 else "flat")
    lines.append(f"THE DAILY VIEW: {symbol} {len(recent)}-bar close chart.")
    lines.append(f"  Current: {current:.2f}")
    lines.append(f"  Recent high: {hi:.2f} ({recent_dates[hi_i]})")
    lines.append(f"  Recent low: {lo:.2f} ({recent_dates[lo_i]})")
    lines.append(f"  Trajectory: {trend} {change_pct:+.2f}% over the window.")
    lines.append("")

    # 2. Distribution (90 days).
    if len(closes) >= 21:
        d90 = closes[-91:] if len(closes) >= 91 else closes
        rets = []
        for k in range(1, len(d90)):
            if d90[k - 1]:
                rets.append((d90[k] - d90[k - 1]) / d90[k - 1] * 100.0)
        ups = [r for r in rets if r > 0]
        downs = [r for r in rets if r < 0]
        max_up = max(ups) if ups else 0.0
        max_down = min(downs) if downs else 0.0
        skew = ("downside tail extends farther" if abs(max_down) > max_up
                else ("upside tail extends farther" if max_up > abs(max_down)
                      else "roughly symmetric"))
        lines.append(f"THE DISTRIBUTION: {symbol} 90-day daily-return histogram.")
        lines.append(f"  Up days: {len(ups)}. Down days: {len(downs)}.")
        lines.append(f"  Largest up move: +{max_up:.2f}%. Largest down move: {max_down:.2f}%.")
        lines.append(f"  Skew: {skew}.")
        lines.append("")

    # 3. Returns across windows.
    _tf_params = {"oid": org_id, "sym": symbol}
    _tf_asof = ""
    if as_of is not None:
        _tf_asof = "AND trade_date <= :asof "
        _tf_params["asof"] = _asof_date
    tf_rows = (await session.execute(sql_text(
        "SELECT timeframe, trade_date, close FROM market_history "
        "WHERE org_id = :oid AND symbol = :sym AND deleted_at IS NULL "
        "AND timeframe IN ('1d', '1wk', '1mo', '1y') "
        + _tf_asof +
        "ORDER BY timeframe, trade_date ASC"
    ), _tf_params)).mappings().all()
    tf_series: dict[str, list[float]] = {}
    for r in tf_rows:
        tf_series.setdefault(r["timeframe"], []).append(float(r["close"]))
    windows = [("1d", 20, "20-day"), ("1wk", 12, "12-week"),
               ("1mo", 6, "6-month"), ("1y", 5, "5-year")]
    window_returns = []
    for tf_key, n_ret, label in windows:
        series = tf_series.get(tf_key) or []
        if len(series) >= n_ret + 1 and series[-n_ret - 1]:
            old = series[-n_ret - 1]
            new = series[-1]
            window_returns.append((label, (new - old) / old * 100.0))
    if window_returns:
        neg = [r for _, r in window_returns if r < 0]
        pos = [r for _, r in window_returns if r > 0]
        lines.append(f"THE RETURNS: {symbol} return by window.")
        for label, r in window_returns:
            lines.append(f"  {label}: {r:+.2f}%")
        if neg and pos:
            lines.append(
                f"  Interpretation: {len(neg)} of {len(window_returns)} "
                f"horizons are negative, {len(pos)} positive. The stock "
                f"has not been uniformly strong."
            )
        lines.append("")

    # 4. Setup history.
    if len(closes) >= 30:
        cur_pct = _percentile(closes, closes[-1])
        if cur_pct is not None:
            band_lo = max(0, cur_pct - 5)
            band_hi = min(100, cur_pct + 5)
            hits = []
            for k in range(len(closes) - 16, 20, -1):
                window_k = closes[:k+1]
                pct_k = _percentile(window_k, window_k[-1])
                if pct_k is None:
                    continue
                if band_lo <= pct_k <= band_hi:
                    f5 = ((closes[k+5] - closes[k]) / closes[k] * 100.0) if k + 5 < len(closes) and closes[k] else None
                    f10 = ((closes[k+10] - closes[k]) / closes[k] * 100.0) if k + 10 < len(closes) and closes[k] else None
                    hits.append((dates[k], f5, f10))
                    if len(hits) >= 3:
                        break
            if hits:
                lines.append(
                    f"THE SETUP HISTORY: {symbol} last {len(hits)} times in "
                    f"the {band_lo}-{band_hi} percentile band."
                )
                for d, f5, f10 in hits:
                    f5s = f"{f5:+.2f}%" if f5 is not None else "n/a"
                    f10s = f"{f10:+.2f}%" if f10 is not None else "n/a"
                    lines.append(f"  {d}: +5 days {f5s}, +10 days {f10s}")
                downs = [f5 for _, f5, _ in hits if f5 is not None and f5 < 0]
                if downs:
                    lines.append(
                        f"  Interpretation: {len(downs)} of "
                        f"{len(hits)} went down within 5 days."
                    )
                lines.append("")

    # 5. Correlation.
    _spy_params = {"oid": org_id}
    _spy_asof = ""
    if as_of is not None:
        _spy_asof = "AND trade_date <= :asof "
        _spy_params["asof"] = _asof_date
    spy_bars = (await session.execute(sql_text(
        "SELECT trade_date, close FROM market_history "
        "WHERE org_id = :oid AND symbol = 'SPY' AND timeframe = '1d' "
        "AND deleted_at IS NULL "
        + _spy_asof +
        "ORDER BY trade_date ASC"
    ), _spy_params)).mappings().all()
    # Keep only the last 60 SPY bars before as_of.
    if as_of is not None and spy_bars and len(spy_bars) > 60:
        spy_bars = spy_bars[-60:]
    if spy_bars and len(spy_bars) >= 21:
        spy = [float(b["close"]) for b in spy_bars]
        n = min(20, len(spy), len(closes))
        sr = [(closes[k] - closes[k-1]) / closes[k-1] for k in range(len(closes)-n, len(closes)) if closes[k-1]]
        pr = [(spy[k] - spy[k-1]) / spy[k-1] for k in range(len(spy)-n, len(spy)) if spy[k-1]]
        if len(sr) == len(pr) and len(sr) > 2:
            ms = sum(sr) / len(sr)
            mp = sum(pr) / len(pr)
            cov = sum((sr[k]-ms)*(pr[k]-mp) for k in range(len(sr)))
            vs = sum((sr[k]-ms)**2 for k in range(len(sr)))
            vp = sum((pr[k]-mp)**2 for k in range(len(pr)))
            denom = math.sqrt(vs * vp)
            corr = cov / denom if denom else 0.0
            lines.append(
                f"THE CORRELATION: {symbol} vs SPY, {n}-day rolling correlation."
            )
            lines.append(f"  Current: {corr:+.2f}.")
            lines.append("")

    return "\n".join(lines)
