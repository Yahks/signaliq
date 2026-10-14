"""market_cap: Level 2 of the market-structure hierarchy.

Reads the latest market_events record. Writes about the segments
the event moved — cap is a proxy for liquidity.

No fetch. The record is the source.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from ..editorial import compose_system_prompt
from ..market_events import format_event_block, get_latest_event
from ..registers._selector import pick_register
from .base import call_llm_json_verified, persist_post

log = get_logger(__name__)


PROMPT_TEMPLATE = """Level 2 — Market Cap.

You are writing about the market's segments, not about a trade.
The event record below is the only source of market facts.

{event_block}

Write the Level 2 piece. Focus on the segments block above
(large_cap / mid_cap / small_cap).

VOICE. First person for what the trader saw. Impersonal for
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

  1. Hook — first person. The moment you saw the segment data.
     Use a specific number. 'When I looked at the segments this
     session, large-cap moved X% while small-cap moved Y%.'
  2. Problem — one sentence. What the split did to your read.
  3. Mechanism — impersonal. Why large/mid/small cap diverge
     or align. Index concentration. Breadth. Cap-weighting.
     Use the segment numbers above.
  4. Diagnostics — first person. 4-6 lived observations
     comparing the segment numbers to your own thresholds.
  5. Fixes — 4-5 before/after changes. Segment by liquidity,
     re-derive the liquidity floor after events.
  6. Reframe — one sentence, standalone, impersonal.
     Example: 'The index is the most liquid part of the
     market, not the whole market.'
  7. Checklist — five questions woven into prose. Each begins
     with 'Can I' or 'Do I' or 'Have I.' Not a bulleted list.
  8. CTA — one paragraph, verbatim from the payload.

Continuous prose. No headings, no bold labels, no bullets,
no numbered lists. Do not print beat names.

SAFETY: never tell the reader which cap segment to prefer.
Describe the structure, not the preference.

End with this CTA, exactly:

{cta_text}
"""


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    event = await get_latest_event(session, org_id)
    if not event:
        log.warning("market_cap.no_event", org_id=org_id[:8])
        return {"status": "no_event", "slug": subblock["slug"]}

    event_block = format_event_block(event)

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

    from packages.core.config import get_settings as _get_settings
    _url = _get_settings().product_url
    cta_text = (
        "I built a liquidity-first universe filter that segments "
        "by spread and depth rather than cap. Email me and I'll "
        f"send it: {_url}"
    )

    user_prompt = PROMPT_TEMPLATE.format(
        event_block=event_block,
        cta_text=cta_text,
    )

    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=compose_system_prompt(
            None,
            subblock.get("style_guide"),
            "You write market-structure pieces for systematic traders.",
            form=None, tone=None, slug=subblock["slug"],
            register=register, tier=None,
        ),
        user=user_prompt,
        prompt_name="trading.market_cap",
        source_payload={
            "register": register,
            "tier": tier,
            "level": 2,
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
            "level": 2,
            "cta_text": cta_text,
            "event_at": event.get("_event_at"),
        },
        source_urls=[], symbols=[],
    )
    await session.commit()

    log.info(
        "market_cap.persisted",
        post_id=post_id[:8],
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok", "slug": subblock["slug"],
        "post_id": post_id,
        "word_count": post.word_count,
    }
