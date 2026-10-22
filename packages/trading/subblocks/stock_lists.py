"""stock_lists: Level 4 of the market-structure hierarchy.

Reads the latest market_events record. Writes about how a
systematic process builds the list of names it's allowed to
consider. The list is filter output, not picks.

Reads the trader's daily_scan section for the filter chain.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from ..ctas import pick_cta
from ..editorial import compose_system_prompt
from ..market_events import format_event_block, get_latest_event
from ..registry import covered_dimensions, pick_coordinate
from ..registers._selector import pick_register
from ..trader_process import get_process_excerpt
from .base import call_llm_json_verified, persist_post

log = get_logger(__name__)


PROMPT_TEMPLATE = """Level 4 — Stock List.

You are writing about the market's names — specifically, how a
systematic process constructs the list of names it's allowed to
consider. The list is filter output, not picks.

The event record below is context for the current regime. The
trader's process document grounds the filter chain.

{event_block}

--- BEGIN TRADER'S PROCESS ---
{process_excerpt}
--- END TRADER'S PROCESS ---

Coordinate: {dimension_label} / {angle_label}
Framing: {angle_framing}

Write the Level 4 piece. Focus on the filter chain —
liquidity first, then structure, then events. What survives
is the list.

VOICE. First person for what the trader does. Impersonal for
the mechanism. Never diagnose the reader. No 'you can check,'
'you should,' 'your portfolio.'

WORD BUDGET — you MUST write at least 600 words total:

  Hook          80-120 words
  Problem       50-70 words
  Mechanism     120-180 words
  Diagnostics    4-6 items, 60-90 words each
  Fixes          4-5 items, 60-90 words each
  Reframe       15-25 words
  Checklist     80-120 words
  CTA           40-60 words

Total minimum: 600. Aim for 700.

FIXES ARE BEFORE/AFTER. Every fix begins with 'I used to' or
'I now.' Never 'Reduce,' 'Cap,' 'Shift,' 'Monitor,' 'Adjust.'

BEAT SHAPE — eight beats, in order:

  1. Hook — first person. The moment the list changed. Use
     a specific number from the event block or the process
     excerpt. Example: 'After the event, my tradable list
     changed. The filters responded to the new regime.'
  2. Problem — one sentence. What the trader missed about
     the list.
  3. Mechanism — impersonal. The filter chain. Each filter
     removes a class of names for a reason. Name the filters
     in order.
  4. Diagnostics — first person. 4-6 lived observations from
     the trader's own list. Compare the list's current state
     to its recent state.
  5. Fixes — 4-5 before/after changes. Define filters
     explicitly. Re-run daily. No manual exceptions. Audit
     after events.
  6. Reframe — one sentence, standalone, impersonal. Example:
     'Filters, not opinions.'
  7. Checklist — five questions woven into prose. Each begins
     with 'Can I' or 'Do I' or 'Have I.' Not a bulleted list.
  8. CTA — one paragraph, verbatim from the payload.

Continuous prose. No headings, no bold labels, no bullets,
no numbered lists. Do not print beat names.

SAFETY: never publish a list of names as something to watch,
buy, or avoid. Describe the construction. The filters are the
content.

End with this CTA, exactly:

{cta_text}
"""


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    slug = subblock["slug"]

    event = await get_latest_event(session, org_id)
    if not event:
        log.warning("stock_lists.no_event", org_id=org_id[:8])
        return {"status": "no_event", "slug": slug}

    event_block = format_event_block(event)

    tier_index = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM trading_posts
        WHERE subblock_id = :sid AND deleted_at IS NULL
    """), {"sid": subblock["id"]})).scalar() or 0

    from ..registers import pick_tier, target_words_for
    tier = pick_tier(tier_index)

    covered = await covered_dimensions(session, org_id)
    if not covered:
        log.warning("stock_lists.no_process_covered", slug=slug)
        return {"status": "no_process", "slug": slug}

    coord = await pick_coordinate(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        subblock_slug=slug,
        covered_dimensions=covered,
    )
    cta_text = pick_cta(coord["dimension_keywords"], post_index=tier_index)
    process_excerpt = await get_process_excerpt(
        session, org_id, coord["dimension_id"], instrument_type="stock",
    
    )

    user_prompt = PROMPT_TEMPLATE.format(
        event_block=event_block,
        process_excerpt=process_excerpt or "(no process excerpt)",
        dimension_label=coord["dimension_label"],
        angle_label=coord["angle_label"],
        angle_framing=coord["angle_framing"],
        cta_text=cta_text,
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
            "You write market-structure pieces for systematic traders.",
            form=None, tone=None, slug=slug,
            register=register, tier=None,
        ),
        user=user_prompt,
        prompt_name="trading.stock_lists",
        source_payload={
            "register": register,
            "tier": tier,
            "level": 4,
            "coordinate": coord["coord"],
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "angle_framing": coord["angle_framing"],
            "cta_text": cta_text,
            "event_at": event.get("_event_at"),
        },
        target_words=target_words,
        form=None, tone=None, voice=None,
        skip_verify=True,
        use_cache=False,   # every event record changes
    )

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "register": register,
            "tier": tier,
            "level": 4,
            "coordinate": coord["coord"],
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "angle_framing": coord["angle_framing"],
            "cta_text": cta_text,
            "event_at": event.get("_event_at"),
        },
        source_urls=[], symbols=[],
    )
    await session.commit()

    log.info(
        "stock_lists.persisted",
        post_id=post_id[:8],
        coord=coord["coord"],
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok", "slug": slug,
        "post_id": post_id,
        "coordinate": coord["coord"],
        "word_count": post.word_count,
    }
