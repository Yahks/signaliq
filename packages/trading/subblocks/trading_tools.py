"""trading_tools: a working trader explaining a tool in their stack.

Reads the trader's process document. The tool is the topic — the
daily scan, the chart overlay, the rolling schedule, the strategy
blend. The register is concept-tools.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from .base import (
    TradingPost,
    call_llm_json_verified,
    persist_post,
)
from ._llm_shared import pick_next_topic
from ..editorial import compose_system_prompt, get_org_voice
from ..forms import pick_form_and_tone
from ..registers._selector import pick_register
from ..registry import (
    SUBBLOCK_ANGLES,
    covered_dimensions,
    pick_coordinate,
)
from ..ctas import pick_cta
from ..trader_process import get_process_excerpt

log = get_logger(__name__)


PROMPT_TEMPLATE = """Write a first-person teaching piece on this
tool:

**{topic}**

The piece uses the trader's actual process. Every rule, every
threshold, every tool comes from the process excerpt below.

Write in the register selected by the system prompt.

BEAT SHAPE. Follow the register's eight beats in order. No
section headings. No bold labels. No bullets — except the ONE
diagnostics list, which uses '-' at the start of each item.
Every fix is a before/after: 'I used to X. Now I Y.'

End with the CTA provided below.
"""


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    topic = await pick_next_topic(session, org_id=org_id, subblock=subblock)
    if not topic:
        log.warning("trading_tools.no_topic", org_id=org_id)
        return {"status": "no_topic", "slug": subblock["slug"]}

    user_prompt = PROMPT_TEMPLATE.format(topic=topic)

    # S.7.8b-4 — editorial voice + form/tone rotation
    form, tone = await pick_form_and_tone(
        session, org_id=org_id, subblock_id=subblock["id"],
    )

    from ..registers import pick_tier, target_words_for
    tier_index = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM trading_posts
        WHERE subblock_id = :sid AND deleted_at IS NULL
    """), {"sid": subblock["id"]})).scalar() or 0
    tier = pick_tier(tier_index)

    # S.8.94 — pick the next content coordinate and a CTA.
    covered = await covered_dimensions(session, org_id)
    if not covered:
        log.warning("subblock.no_process_covered", slug=subblock["slug"])
        return {"status": "no_process", "slug": subblock["slug"]}
    coord = await pick_coordinate(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        covered_dimensions=covered,
        allowed_angles=SUBBLOCK_ANGLES.get(subblock["slug"]),
    )
    cta_text = pick_cta(
        coord["dimension_keywords"], post_index=tier_index,
    )
    process_excerpt = await get_process_excerpt(
        session, org_id, coord["dimension_id"], instrument_type="stock",
    
    )

    # Prepend the coordinate to the user prompt so the
    # model sees the dimension and the angle before the
    # template.
    user_prompt = (
        f"Write a teaching piece on: {coord['dimension_label']}\n"
        f"Framed through: {coord['angle_label']} — "
        f"{coord['angle_framing']}\n\n"
        f"Here is the trader's actual process for this topic. "
        f"Every rule, every threshold, every named effect in "
        f"the article must come from this. Do NOT use generic "
        f"SMA/RSI examples or textbook defaults. Use the "
        f"trader's rules:\n\n"
        f"{process_excerpt}\n\n"
        "---\n\n"
        + user_prompt
        + f"\n\nEnd with this CTA: {cta_text}\n"
    )

    register = await pick_register(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        subblock_slug=subblock["slug"],
        tier=tier,
    )
    target_words = target_words_for(subblock["slug"], register, tier)
    voice = await get_org_voice(session, org_id)

    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=compose_system_prompt(
            voice,
            subblock.get("style_guide"),
            "You write clear trading education.",
            form=form,
            tone=tone,
            slug=subblock["slug"],
            register=register,
            tier=tier,
        ),
        user=user_prompt,
        prompt_name="trading.trading_tools",
        source_payload={
            "topic": topic,
            "coordinate": coord["coord"],
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "angle_framing": coord["angle_framing"],
            "cta_text": cta_text,
            "process_excerpt": process_excerpt,
            "register": register,
            "tier": tier,
        },
        target_words=target_words,
        form=form,
        tone=tone,
        voice=voice,
        skip_verify=True,   # education subblock, no source data
    )

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "topic": topic,
            "generator": "llm_curated",
            "coordinate": coord["coord"],
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "cta_text": cta_text,
            "register": register,
            "tier": tier,
        },
        source_urls=[],
        symbols=[],
    )
    await session.commit()

    log.info(
        "trading_tools.persisted",
        post_id=post_id[:8],
        topic=topic,
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "topic": topic,
        "word_count": post.word_count,
    }
