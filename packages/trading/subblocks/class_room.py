"""class_room generator: a curriculum lesson for Class Room."""

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
from ..editorial import compose_system_prompt
from ..registers._selector import pick_register
from ..registry import (
    SUBBLOCK_ANGLES,
    covered_dimensions,
    pick_coordinate,
)
from ..ctas import pick_cta
from ..trader_process import get_process_excerpt

log = get_logger(__name__)


PROMPT_TEMPLATE = """Coordinate: {dimension_label} / {angle_label}

Framing: {angle_framing}

Your process document is below. The emphasis section is marked. Every
number, instrument, and paper citation in the piece must come from it.

--- BEGIN PROCESS DOCUMENT ---
{process_excerpt}
--- END PROCESS DOCUMENT ---

Write the piece now. First person. 700-1000 words. Follow the
register's shape. End with this CTA, nothing after it:

{cta_text}
"""


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    slug = subblock["slug"]

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

    user_prompt = PROMPT_TEMPLATE.format(
        topic=coord["topic"],
        dimension_label=coord["dimension_label"],
        angle_label=coord["angle_label"],
        angle_framing=coord["angle_framing"],
        process_excerpt=process_excerpt or "(no process excerpt available)",
        cta_text=cta_text,
    )


    register = await pick_register(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        subblock_slug=subblock["slug"],
        tier=tier,
    )
    target_words = target_words_for(subblock["slug"], register, tier)

    # S.8.97 — every post goes to draft (project rule). The delivery
    # check runs inside persist_post and flags violations with
    # __delivery__ markers, but the post is still persisted so a
    # reviewer can see what was produced and what failed.
    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=compose_system_prompt(
            None,                              # no voice — register carries it
            subblock.get("style_guide"),
            "You write clear trading education.",
            form=None,                         # forms are for market wire
            tone=None,                         # tones are for market wire
            slug=subblock["slug"],
            register=register,
            tier=None,                         # no tier directive
        ),
        user=user_prompt,
        prompt_name="trading.class_room",
        source_payload={
            "topic": coord["topic"],
            "register": register,
            "tier": tier,
            "coordinate": coord["coord"],
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "angle_framing": coord["angle_framing"],
            "cta_text": cta_text,
            "process_excerpt": process_excerpt,
        },
        target_words=target_words,
        form=None,
        tone=None,
        voice=None,
        skip_verify=True,
    )

    # S.8.99 — the model writes an article, then optionally a
    # GATE REPORT. Extract by what's actually present, not by
    # the labels the prompt asks for.
    review_notes: dict[str, str] = {}
    body = (post.body or "").strip()

    def _split_off(body: str, marker: str) -> tuple[str, str]:
        """Split body at the first line that is exactly `marker:`
        (or starts with `marker:`). Return (before, from_marker_on)."""
        idx = body.find(marker + ":")
        if idx < 0:
            return body, ""
        return body[:idx].rstrip(), body[idx:].strip()

    # Peel GATE REPORT off the tail if it's there.
    article_block, gate_block = _split_off(body, "GATE REPORT")
    # Peel PLANNING off the head if it's there.
    planning_block = ""
    if article_block.lstrip().startswith("PLANNING"):
        before, after = _split_off(article_block, "ARTICLE")
        if after:
            planning_block, article_block = before, after
        else:
            # No ARTICLE label — cut at the first blank line after
            # the planning block ends. Simple heuristic: the planning
            # block ends at the first double-newline.
            planning_block, _, article_block = article_block.partition(
                "\n\n\n"
            )
    # If an ARTICLE: label survived, drop it.
    if article_block.lstrip().startswith("ARTICLE:"):
        article_block = article_block.lstrip()[len("ARTICLE:"):].lstrip()

    wc = len(article_block.split())
    if wc >= 150:
        if planning_block:
            review_notes["planning"] = planning_block[:4000]
        if gate_block:
            review_notes["gate_report"] = gate_block[:4000]
        post.body = article_block
        post.word_count = wc
        log.info(
            "class_room.extracted",
            article_words=wc,
            has_planning=bool(planning_block),
            has_gate_report=bool(gate_block),
        )

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "topic": coord["topic"],
            "generator": "llm_curated",
            "register": register,
            "tier": tier,
            "coordinate": coord["coord"],
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "angle_framing": coord["angle_framing"],
            "cta_text": cta_text,
            "process_excerpt": process_excerpt,
            "review_notes": review_notes,
        },
        source_urls=[],
        symbols=[],
    )
    await session.commit()

    log.info(
        "class_room.persisted",
        post_id=post_id[:8],
        topic=coord["topic"],
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "topic": coord["topic"],
        "word_count": post.word_count,
    }
