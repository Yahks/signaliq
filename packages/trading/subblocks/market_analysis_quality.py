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

def build_exact_numbers(t: dict[str, Any]) -> str:
    """Build the EXACT NUMBERS section. Only called after
    validate_block() returns empty - the block is guaranteed
    to have every field."""
    def _fmt_time(dt):
        try:
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return str(dt)

    # percent_of_equity may be None on older trades. Compute
    # it from the trader's starting balance. Every article's
    # account block shows $200,000; use that constant.
    pct = t.get("percent_of_equity")
    if pct is None:
        try:
            pct = (float(t["result"]) / 200000.0) * 100.0
        except (TypeError, ValueError, KeyError):
            pct = 0.0
    pct_str = f"{pct:.2f}"

    # Format every numeric field to 2 decimals so the model
    # reads a clean value instead of a 47-digit float.
    def _f(key):
        try:
            return f"{float(t[key]):.2f}"
        except (TypeError, ValueError, KeyError):
            return "unknown"

    # Detect the price unit. Stocks are in USD per share.
    # Futures would use points; the trade generator only
    # produces stock trades today.
    price_unit = "USD per share"

    return (
        "--- USE THESE EXACT TRADE NUMBERS ---\n"
        f"Entry price ({price_unit}): {_f('entry')}\n"
        f"Stop price ({price_unit}): {_f('stop')}\n"
        f"Target price ({price_unit}): {_f('target')}\n"
        f"Exit price ({price_unit}): {_f('exit')}\n"
        f"Result (USD): {_f('result')}\n"
        f"Contracts (shares): {t['contracts']}\n"
        f"ATR at entry (points): {_f('atr_at_entry')}\n"
        f"Entry time (UTC): {_fmt_time(t['entry_time'])}\n"
        f"Exit time (UTC): {_fmt_time(t['exit_time'])}\n"
        f"Percent of equity: {pct_str}%\n"
        f"Symbol: {t['symbol']}\n"
        f"Direction: {t['direction']}\n"
        "\n"
        "Every trade number you write in the article MUST "
        "come from this list, verbatim. Do NOT invent a "
        "different entry, stop, target, exit, result, or "
        "size.\n"
        "\n"
        "KEEP THE UNIT WHEN YOU QUOTE IT:\n"
        f"  - Price: write $ before the number. \"$113.93\" "
        f"not \"113.93\".\n"
        f"  - Result: write $ before the number. "
        f"\"-161.00 dollars\" or \"-$161.00\".\n"
        f"  - ATR: write \"points\" after the number. "
        f"\"0.83 points\" not \"0.83\".\n"
        f"  - Contracts: write \"shares\" or \"contracts\" "
        f"after. \"100 shares\" not \"100\".\n"
        f"  - Time: write UTC (or CT/ET) after. \"14:46 UTC\" "
        f"not \"14:46\".\n"
        f"  - Percent: write the % sign. \"0.99%\" not "
        f"\"0.99\".\n"
        f"\n"
        f"The reader must never have to guess what a number "
        f"means. Every number carries its unit."
    )

# ──── CHARTS block ────

async def build_charts_block(
    session: AsyncSession, *,
    org_id: str, symbol: str, timeframe: str,
) -> str:
    """Build the CHARTS section from the same data the chart
    renderers use. Describes five charts with the numbers they
    show and what those numbers mean for the trade.

    Text only. No image, no graphic. The model reads this text
    and references the charts in prose.

    Returns "" if the price series is too short.
    """
    import math

    bars = (await session.execute(sql_text(
        "SELECT trade_date, close FROM market_history "
        "WHERE org_id = :oid AND symbol = :sym "
        "AND timeframe = :tf AND deleted_at IS NULL "
        "ORDER BY trade_date ASC"
    ), {"oid": org_id, "sym": symbol, "tf": timeframe})).mappings().all()

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
    tf_rows = (await session.execute(sql_text(
        "SELECT timeframe, trade_date, close FROM market_history "
        "WHERE org_id = :oid AND symbol = :sym AND deleted_at IS NULL "
        "AND timeframe IN ('1d', '1wk', '1mo', '1y') "
        "ORDER BY timeframe, trade_date ASC"
    ), {"oid": org_id, "sym": symbol})).mappings().all()
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
    spy_bars = (await session.execute(sql_text(
        "SELECT trade_date, close FROM market_history "
        "WHERE org_id = :oid AND symbol = 'SPY' AND timeframe = '1d' "
        "AND deleted_at IS NULL ORDER BY trade_date ASC LIMIT 60"
    ), {"oid": org_id})).mappings().all()
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
