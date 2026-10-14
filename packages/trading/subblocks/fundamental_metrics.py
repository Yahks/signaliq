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

VOICE. First person for what the trader does. Impersonal for
how the condition behaves. Never diagnose the reader. No 'you
can check,' 'you should,' 'your portfolio.'

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

  1. Hook — first person. The trader's own view on the
     condition. 'I used to treat X as Y. Then I saw...'
  2. Problem — one sentence. What the trader missed.
  3. Mechanism — impersonal. How the condition behaves.
     Qualitative. Do not invent a formula.
  4. Diagnostics — first person. 4-6 lived observations from
     the trader's own process.
  5. Fixes — 4-5 before/after changes.
  6. Reframe — one sentence, standalone, impersonal.
  7. Checklist — five questions woven into prose. Each
     begins with 'Can I' or 'Do I' or 'Have I.' Not a bulleted
     list.
  8. CTA — one paragraph, verbatim from the payload.

Continuous prose. No headings, no bold labels, no bullets,
no numbered lists. Do not print beat names.

End with this CTA, exactly:

{cta_text}
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

    from packages.core.config import get_settings as _get_settings
    _url = _get_settings().product_url
    cta_text = (
        "I turned this into a one-page checklist for recognizing "
        "the condition in your own process. Email me and I'll "
        f"send it: {_url}"
    )

    user_prompt = PROMPT_TEMPLATE.format(
        coordinate=coord["label"],
        trader_section=trader_section,
        cta_text=cta_text,
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
            "cta_text": cta_text,
        },
        target_words=target_words,
        form=None,
        tone=None,
        voice=None,
        skip_verify=True,
    )

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
            "cta_text": cta_text,
        },
        source_urls=[],
        symbols=[],
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
