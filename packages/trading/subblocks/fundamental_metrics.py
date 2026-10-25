"""fundamental_metrics: F3 Structural Explainer.

The subblock reads the trader's `fundamental_metrics` section (a
text column on trader_process), picks the next unused FM coordinate,
and writes a structural explainer for that coordinate using the
trader's section as the source material.

No-wrap: after all 8 FM coordinates are used for this process
version, the subblock returns {status: exhausted}.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from ..editorial import compose_system_prompt
from ..fundamental_metrics import get_fundamental_metrics_section
from ..registry import pick_fm_coordinate
from ..registers._selector import pick_register
from .base import call_llm_json_verified, persist_post

log = get_logger(__name__)


PROMPT_TEMPLATE = """Form: F3 — Structural Explainer.

Coordinate: {coordinate}

You are teaching how a fundamental condition behaves and how it
interacts with systematic strategies. This is evergreen — not
tied to today's moment.

Here is the trader's fundamental-metrics section, in their own
words. This is the only source of facts. Quote from it where it
strengthens the piece. Every number in the piece must come from
here.

--- BEGIN TRADER'S SECTION ---
{trader_section}
--- END TRADER'S SECTION ---

SIGNAL DIRECTIONS (from the trader's process, authoritative):
{signal_directions}

Whenever you name a signal — a draw, a cut, a widening spread —
you MUST state whether you treat it as bullish or bearish, and it
must match the direction above. Do not imply a direction by
describing price action; say the word.

VOICE. First person for what the trader does. Impersonal for
how the condition behaves. Never diagnose the reader. No 'you
can check,' 'you should,' 'your portfolio.'

WORD BUDGET — 820 words MINIMUM. Hard floor: an article under 820
words is rejected. Reach it by developing these beats, not by
padding:
  - Mechanism: 150-200 words on how the condition behaves.
  - Diagnostics: 4-6 observations, 70-100 words each, each drawn
    from the trader's own process.
  - Fixes: 4-5 before/after changes, 70-100 words each.
  - Reframe: one sentence, but develop the paragraph around it.
Do not repeat a point to add length. If short, add depth to the
mechanism and the diagnostics, not summary sentences.

FIXES ARE BEFORE/AFTER. Every fix begins with 'I used to' or
'I now.' Never 'Reduce,' 'Cap,' 'Shift,' 'Monitor,' 'Adjust.'

BEAT SHAPE — seven beats, in order:

  1. Hook — first person. The trader's own view on the
     condition. 'I used to treat X as Y. Then I saw...'
  2. Problem — one sentence. What the trader missed.
  3. Mechanism — impersonal. How the condition behaves.
     Qualitative. Do not invent a formula.
  4. Diagnostics — first person. 4-6 lived observations from
     the trader's own process. Each one states the reading, the
     trader's threshold, and what it means.
  5. Fixes — 4-5 before/after changes.
  6. Reframe — one sentence, standalone, impersonal.
  7. End — end on the reframe. No checklist, no CTA, no URL.

CHART. Where the candle chart belongs, write the tag
[CHART: daily] on its own line. It is replaced with a daily
candlestick chart of CL. Place it where a visual of price action
supports the mechanism, usually after beat 4.

Continuous prose. No headings, no bold labels, no bullets,
no numbered lists. Do not print beat names.
"""


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    # 1. Read the trader's fundamental-metrics section.
    trader_section = await get_fundamental_metrics_section(session, org_id)
    if not trader_section:
        log.warning("fundamental_metrics.no_section", org_id=org_id[:8])
        return {"status": "no_section", "slug": subblock["slug"]}

    # 2. Pick the next FM coordinate (no-wrap).
    coord = await pick_fm_coordinate(
        session, org_id=org_id, subblock_id=subblock["id"],
    )
    if coord is None:
        log.info("fundamental_metrics.exhausted", org_id=org_id[:8])
        return {"status": "exhausted", "slug": subblock["slug"]}

    # 3. Pick the register (market-regime via SUBBLOCK_TIERS).
    from ..registers import pick_tier, target_words_for

    tier_index = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM trading_posts
        WHERE subblock_id = :sid AND deleted_at IS NULL
    """), {"sid": subblock["id"]})).scalar() or 0
    tier = pick_tier(tier_index)

    register = await pick_register(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        subblock_slug=subblock["slug"],
        tier=tier,
    )
    target_words = target_words_for(subblock["slug"], register, tier)

    # Structured signal directions: the article must state the
    # direction it treats each signal as, and the validator compares
    # to this table.
    _dir_rows = (await session.execute(sql_text("""
        SELECT signal, direction FROM fundamental_signal_directions
        WHERE org_id = :oid AND deleted_at IS NULL
    """), {"oid": org_id})).mappings().all()
    _directions = {r["signal"]: r["direction"] for r in _dir_rows}
    _signal_words = {
        "api_draw_gt_5mb": "draw",
        "opec_cut_gt_0_5mbpd": "cut",
        "spread_widening_gt_0_50": "widening spread",
    }

    _dir_lines = "\n".join(
        f"  - a {_signal_words.get(k, k)} is {v}"
        for k, v in sorted(_directions.items())
    ) or "  (none on file)"

    user_prompt = PROMPT_TEMPLATE.format(
        coordinate=coord["label"],
        trader_section=trader_section,
        signal_directions=_dir_lines,
    )

    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=compose_system_prompt(
            None,
            subblock.get("style_guide"),
            "You write market analysis for systematic traders.",
            form=None,
            tone=None,
            slug=subblock["slug"],
            register=register,
            tier=None,
        ),
        user=user_prompt,
        prompt_name="trading.fundamental_metrics",
        source_payload={
            "form": "F3",
            "register": register,
            "tier": tier,
            "coordinate": coord["coord"],
            "coordinate_label": coord["label"],
        },
        target_words=target_words,
        form=None,
        tone=None,
        voice=None,
        skip_verify=False,
    )

    # ── Validation ──
    from packages.trading.fundamental_metrics_validator import (
        validate_fundamental_metrics,
    )
    _reasons = validate_fundamental_metrics(
        post.body, trader_section,
        directions=_directions, signals=_signal_words,
    )
    if _reasons:
        log.warning("fundamental_metrics.validator_rejected",
                    reasons=_reasons)
        post.verification_ok = False
        post.unsupported_claims = list(
            getattr(post, "unsupported_claims", None) or []
        ) + _reasons

    # ── Candle chart (CL daily) ──
    # The trader's process is CL throughout (entry_logic,
    # position_sizing, exit_logic), and every FM coordinate is a
    # crude-market concept, so the chart shows CL daily candles.
    # No trade markers: this is a conceptual essay, not a trade
    # note. Failure never blocks the article.
    try:
        from packages.trading.charts.article_charts import (
            render_trade_candlestick,
        )
        from packages.db.session import get_session_factory as _gsf
        from packages.db.tenant import set_current_org as _set_org

        _sym = "CL"
        async with _gsf()() as _chart_s:
            await _set_org(_chart_s, org_id)
            _candle_url = await render_trade_candlestick(
                _chart_s,
                org_id=org_id,
                symbol=_sym,
                timeframe="1d",
            )
        if _candle_url:
            import re as _re
            _img = f"\n\n![{_sym} 1d candles]({_candle_url})\n\n"
            _new, _n = _re.subn(
                r"\[CHART:\s*daily\s*\]", _img, post.body,
                flags=_re.IGNORECASE,
            )
            if _n:
                post.body = _new
            else:
                post.body = post.body.rstrip() + _img
                log.warning("fundamental_metrics.chart_tag_missing",
                            symbol=_sym)
            log.info("fundamental_metrics.candle_inserted",
                     symbol=_sym, url=_candle_url, replaced=_n)
    except Exception as _ce:
        log.warning("fundamental_metrics.candle_failed",
                    error=str(_ce)[:200])

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "form": "F3",
            "register": register,
            "tier": tier,
            "coordinate": coord["coord"],
            "coordinate_label": coord["label"],
        },
        source_urls=[],
        symbols=["CL"],
    )
    await session.commit()

    log.info(
        "fundamental_metrics.persisted",
        post_id=post_id[:8],
        coord=coord["coord"],
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "coordinate": coord["coord"],
        "word_count": post.word_count,
    }
