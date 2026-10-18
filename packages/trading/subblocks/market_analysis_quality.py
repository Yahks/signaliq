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
    r"\b(?:I (?:ignored|refused|couldn'?t|could not|wanted|told myself|let|let my|let the|convinced myself))\b",
    r"\b(?:denial|refus(?:al|ed)|ego|tilt|impulse|tempted)\b",
    r"\b(?:I was (?:angry|frustrated|scared|afraid|desperate))\b",
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

    return (
        "--- USE THESE EXACT TRADE NUMBERS ---\n"
        f"Entry price: {t['entry']}\n"
        f"Stop price: {t['stop']}\n"
        f"Target price: {t['target']}\n"
        f"Exit price: {t['exit']}\n"
        f"Result: {t['result']} dollars\n"
        f"Contracts: {t['contracts']}\n"
        f"ATR at entry: {t['atr_at_entry']}\n"
        f"Entry time: {_fmt_time(t['entry_time'])}\n"
        f"Exit time: {_fmt_time(t['exit_time'])}\n"
        f"Percent of equity: {t.get('percent_of_equity', 'unknown')}%\n"
        f"Symbol: {t['symbol']}\n"
        f"Direction: {t['direction']}\n"
        "\n"
        "Every trade number you write in the article MUST "
        "come from this list, verbatim. Do NOT invent a "
        "different entry, stop, target, exit, result, or "
        "size. If you write a trade number that is not in "
        "this list, the article is wrong."
    )


