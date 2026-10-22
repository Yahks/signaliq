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

from ..ctas import pick_cta
from ..editorial import compose_system_prompt
from ..registry import pick_coordinate, covered_dimensions
from ..registers._selector import pick_register
from ..trader_process import get_process_excerpt
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

Write the piece in the register selected by the system prompt.

FOCUS. Name the specific indicator. Say what it measures, why
it earns its place, and where it fails. Do NOT drift into the
scan's thresholds — that is a different article.

LENGTH. The article must be at least 500 words, aiming for
600. Give each beat a budget:

  Hook            60-90 words
  Name the problem 30-50 words
  Mechanism      100-150 words
  Diagnostics     5 items, 40-60 words each
  Fixes           5 items, 40-60 words each
  Reframe         15-25 words
  Checklist       60-90 words
  CTA             30-50 words

Every diagnostic cites a different trade or observation. Every
fix is a before/after: what the trader used to do, what they do
now, and the arithmetic in words.

Continuous prose. No headings, no bold labels, no bullets, no
numbered lists — except the ONE diagnostics list, which uses
'-' at the start of each item.

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
    cta_text = pick_cta(coord["dimension_keywords"], post_index=tier_index)
    process_excerpt = await get_process_excerpt(
        session, org_id, coord["dimension_id"], instrument_type="stock",
    
    )

    user_prompt = PROMPT_TEMPLATE.format(
        dimension_label=coord["dimension_label"],
        angle_label=coord["angle_label"],
        angle_framing=coord["angle_framing"],
        process_excerpt=process_excerpt or "(no process excerpt)",
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
            "cta_text": cta_text,
            "process_excerpt": process_excerpt,
        },
        target_words=target_words,
        form=None, tone=None, voice=None,
        skip_verify=True,
    )

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
            "cta_text": cta_text,
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
