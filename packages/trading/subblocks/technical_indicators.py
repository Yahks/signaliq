"""technical_indicators: which indicators and why.

Reads entry_logic + chart_overlay from the trader's process.
Picks the next unused coordinate from D2 / D8 × the subblock's
angles. Writes the piece in the practitioner-process register.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from ..editorial import compose_system_prompt
from ..registry import pick_coordinate, covered_dimensions
from ..registers._selector import pick_register
from ..trader_process import get_process_excerpt
from ..technical_indicators_skeleton import build_technical_skeleton
from .base import call_llm_json_verified, persist_post

log = get_logger(__name__)


PROMPT_TEMPLATE = """Coordinate: {dimension_label} / {angle_label}

Framing: {angle_framing}

Your process document is below. The entry-logic and chart-overlay
sections are the emphasis. Write about which indicators you use,
which you rejected, and why — an indicator earns its place by
removing a specific named error.

--- BEGIN PROCESS DOCUMENT ---
{process_excerpt}
--- END PROCESS DOCUMENT ---

STRUCTURED SKELETON — computed by Python. Every trade, date, and
dollar figure you cite MUST come from this block. Do not invent a
trade.

{skeleton}

Write the piece in the register selected by the system prompt.

FOCUS. Name the specific indicator. Say what it measures, why
it earns its place, and where it fails. Do NOT drift into the
scan's thresholds — that is a different article.

LENGTH. The article must be at least 500 words, aiming for
600. Give each beat a budget:

  Hook            60-90 words
  Name the problem 30-50 words
  Mechanism      100-150 words
  Price action   80-120 words (see below)
  Diagnostics     5 items, 40-60 words each
  Fixes           5 items, 40-60 words each
  Reframe         15-25 words

PRICE ACTION (required paragraph, after the mechanism). Describe
the subject trade's entry candle and the bars around it, using
ONLY the BARS and OVERLAY blocks. Cite the date and at least two
OHLC values from the BARS block. State the 20-EMA and 50-EMA
values and the relationship the OVERLAY block reports.

  - If the OVERLAY state says "converging", the EMAs are within
    0.5% and NO direction is supportable. Do NOT call it bullish,
    bearish, a downtrend, or an uptrend. Say they are converging
    and that the pair gives no trend signal at this bar.
  - If the state says above/below, you may describe the bias, but
    only in the direction the state reports.
  - If the skeleton flags a clearance shortfall, name it: the
    entry did not clear the prior 10-bar high by the required
    0.5%.
  - The BAR ACTION block states each bar's shape and the move
    from the previous bar. Quote those phrases. Do NOT
    characterise a move in your own words ("fell sharply",
    "surged") — the computed percentages are the fact.

Every diagnostic cites a different trade or observation. Every
fix is a before/after: what the trader used to do, what they do
now, and the arithmetic in words.

CHART DISCUSSION. The BARS, OVERLAY and BAR ACTION blocks are
the primary evidence. Use them to explain what the price actually
did — the entry bar's shape, how the next bars moved, whether the
breakout held or reversed, what the EMA state was at the entry.
Quote the computed phrases and values. Lead the analysis from the
chart; use the rule arithmetic only to explain why the chart
mattered.

RULES. You may cite only percentages that appear in the RULES
block (current process), a trade's percent of equity, or a batch
figure. Do NOT invent a new threshold ("0.2% above the 50-EMA",
"±0.05% neutral") — those rules do not exist in the process.

Continuous prose. No headings, no bold labels, no bullets, no
numbered lists — except the ONE diagnostics list, which uses
'-' at the start of each item.

Do NOT end with a CTA, a URL, an email address, or a marketing
line. End on the reframe.

EVIDENCE RULE. Every number you write must come from the STRUCTURED
SKELETON. Do not claim a process statistic (a percentage
improvement, a variance reduction, a win-rate change) that is not
in the BATCH block. For each diagnostic, explain what the trade's
own numbers show — do not invent a cause (an earnings spike, a news
event) that the skeleton does not state.
"""


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    slug = subblock["slug"]

    tier_index = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM trading_posts
        WHERE subblock_id = :sid AND deleted_at IS NULL
    """), {"sid": subblock["id"]})).scalar() or 0

    from ..registers import pick_tier, target_words_for
    tier = pick_tier(tier_index)

    covered = await covered_dimensions(session, org_id)
    if not covered:
        log.warning("technical_indicators.no_process_covered", slug=slug)
        return {"status": "no_process", "slug": slug}

    coord = await pick_coordinate(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        subblock_slug=slug,
        covered_dimensions=covered,
    )
    process_excerpt = await get_process_excerpt(
        session, org_id, coord["dimension_id"], instrument_type="stock",
        current_process_only=True,
    )

    # Pick a symbol, generate its trades, and use one as the subject
    # (the same flow market_analysis uses). Guarantees a trade log
    # entry for the picked coordinate.
    from ..scrapers.trade_generator import ensure_trades_for
    from .market_analysis_rotation import pick_unused_trade
    from .market_analysis_rotation_stock import pick_stock_and_timeframe

    _subject = None
    _stock_pick = await pick_stock_and_timeframe(
        session, org_id=org_id, subblock_id=subblock["id"],
    )
    if _stock_pick is not None:
        _sym, _tf, _ftype, _fsection = _stock_pick
        await session.commit()
        await ensure_trades_for(
            session, org_id=org_id, symbol=_sym, timeframe=_tf, count=5,
        )
        _subject = await pick_unused_trade(
            session, org_id=org_id, symbol=_sym, timeframe=_tf,
        )

    skeleton, subject_trade = await build_technical_skeleton(
        session, org_id, coord, process_excerpt=process_excerpt or "",
        subject_trade=_subject,
    )
    user_prompt = PROMPT_TEMPLATE.format(
        dimension_label=coord["dimension_label"],
        angle_label=coord["angle_label"],
        angle_framing=coord["angle_framing"],
        process_excerpt=process_excerpt or "(no process excerpt)",
        skeleton=skeleton,
    )

    register = await pick_register(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        subblock_slug=slug,
        tier=tier,
    )
    target_words = target_words_for(slug, register, tier)

    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=compose_system_prompt(
            None,
            subblock.get("style_guide"),
            "You write trading process pieces.",
            form=None, tone=None, slug=slug,
            register=register, tier=None,
        ),
        user=user_prompt,
        prompt_name="trading.technical_indicators",
        source_payload={
            "register": register, "tier": tier,
            "coordinate": coord["coord"],
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "angle_framing": coord["angle_framing"],
            "process_excerpt": process_excerpt,
            "skeleton": skeleton,
        },
        target_words=target_words,
        form=None, tone=None, voice=None,
        skip_verify=True,
        use_cache=False,
    )

    # ── Render the subject trade's candlestick and insert it ──
    if subject_trade:
        try:
            from packages.trading.charts.article_charts import (
                render_trade_candlestick,
            )
            _url = await render_trade_candlestick(
                session,
                org_id=org_id,
                symbol=str(subject_trade.get("symbol") or ""),
                timeframe=str(subject_trade.get("timeframe") or "1h"),
                entry=(float(subject_trade["entry"])
                       if subject_trade.get("entry") is not None else None),
                stop=(float(subject_trade["stop"])
                      if subject_trade.get("stop") is not None else None),
                target=(float(subject_trade["target"])
                        if subject_trade.get("target") is not None else None),
                exit_price=(float(subject_trade["exit"])
                            if subject_trade.get("exit") is not None else None),
                entry_time=subject_trade.get("traded_at"),
            )
            if _url:
                _img = f"![{subject_trade.get('symbol')} chart]({_url})"
                _paras = post.body.split("\n\n")
                # Insert above the diagnostics list (the first
                # paragraph whose lines start with "- "), so the
                # reader sees the candles before reading about the
                # trades. Fall back to above the mechanism
                # (third paragraph) if no list is found.
                _idx = None
                for _i, _par in enumerate(_paras):
                    _lines = [_ln for _ln in _par.splitlines() if _ln.strip()]
                    if _lines and all(_ln.lstrip().startswith("-")
                                      for _ln in _lines):
                        _idx = _i
                        break
                if _idx is None:
                    _idx = min(2, len(_paras))
                _paras.insert(_idx, _img)
                post.body = "\n\n".join(_paras)
                log.info("technical_indicators.chart_inserted",
                         symbol=subject_trade.get("symbol"), url=_url,
                         position=_idx)
        except Exception as _ce:
            log.warning("technical_indicators.chart_failed",
                        error=str(_ce)[:200])

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "register": register, "tier": tier,
            "coordinate": coord["coord"],
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "angle_framing": coord["angle_framing"],
            "skeleton": skeleton,
        },
        source_urls=[], symbols=[],
    )
    await session.commit()

    log.info(
        "technical_indicators.persisted",
        post_id=post_id[:8], coord=coord["coord"],
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok", "slug": slug,
        "post_id": post_id,
        "coordinate": coord["coord"],
        "word_count": post.word_count,
    }
