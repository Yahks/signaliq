"""market_condition: F1 regime report — breadth.

Reads the trader's market-conditions block. Picks market-regime.
Writes a regime report on the current breadth state.

No Yahoo fetch. The trader's observations are the source.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from ..editorial import compose_system_prompt
from ..market_conditions import format_conditions_block, get_market_conditions
from ..post_process import post_process_article
from ..registers._selector import pick_register
from .base import call_llm_json_verified, persist_post

log = get_logger(__name__)


PROMPT_TEMPLATE = """Form: F1 — Regime Report (breadth).

You are writing a regime report on the current breadth state.

Here is what the trader tracks, in their own words. This is the only
source of facts about the trader's process and their current
observations. Every number in the piece must come from here.

{conditions_block}

Write the F1 Regime Report now.

  - The current breadth state, named explicitly (wide or narrow).
  - The structural reason — a named mechanism (breadth narrows
    before declines; index moves weaken when few names carry the
    index).
  - What it means for index-correlated strategies versus
    individual-name setups.
  - Four to six observable diagnostics the reader can check
    (advance/decline, percentage above the 50-day, new highs vs.
    new lows).
  - Four to five process adjustments. Rules, not trades.

BEAT SHAPE — eight beats, in order. Do NOT skip any:

  1. Hook — first person, with tension.
  2. Problem — one sentence. What the reading did to your read.
  3. Mechanism — impersonal. How breadth behaves.
  4. Diagnostics — first person. 4-6 lived observations.
  5. Fixes — 4-5 before/after changes.
  6. Reframe — ONE SENTENCE, STANDALONE, before the checklist.
  7. Checklist — FIVE QUESTIONS woven into one or two short
     paragraphs. Each begins with 'Can I' or 'Do I' or 'Have I.'
     MANDATORY. The article is incomplete without it.
  8. CTA — one paragraph, verbatim from the payload.

WORD BUDGET — you MUST write at least 600 words total:

  Opening        80-120 words
  Problem        50-70 words
  Mechanism     120-180 words
  Diagnostics    4-6 items, 60-90 words each
  Fixes          4-5 items, 60-90 words each
  Reframe        15-25 words
  Checklist      80-120 words
  CTA            40-60 words

Total minimum: 500. Aim for 700. If your draft is under 600,
extend the mechanism, the diagnostics, and the fixes.

FIXES ARE BEFORE/AFTER. Every fix in the fixes beat begins with
'I used to' or 'I now.' Never a bare imperative. Wrong: 'Reduce
the NQ exposure.' Right: 'I used to keep full NQ exposure. Now
I reduce it.'

VOICE. The mechanism is impersonal. The hook, diagnostics, and
fixes are FIRST PERSON. Never 'you,' 'your,' 'you can.'

CHECKLIST IS MANDATORY. The piece ENDS with FIVE QUESTIONS
woven into one or two short paragraphs. Each question begins
with 'Can I' or 'Do I' or 'Have I.' Do NOT skip this beat.
The article is incomplete without it.

REFRAME IS STANDALONE. One sentence. Before the checklist.
Impersonal. Not embedded in the problem or mechanism.

NO LABELS. Do NOT write '**Checklist**' or any bold label. Do NOT write 'Checklist:' or any beat name as a header. The checklist begins with 'Can I...' — nothing above it. Continuous prose. No headings, no bullets. End with this CTA:

{cta_text}
"""


PROMPT_TEMPLATE_F2 = """Form: F2 — Process Diary (breadth).

You are writing a first-person process diary. Report what your
system produced this period and what it reveals about the
current breadth state.

Here is what the trader tracks, in their own words. The only
source of facts.

{conditions_block}

Write the F2 Process Diary now.

  - My scan returned [result] this week.
  - The current breadth state (wide, narrow, or transitional).
  - The structural reason — why breadth changes.
  - What it means for my strategies.
  - Four to six lived observations.
  - Four to five process adjustments I made.

FIRST PERSON throughout. The hook has tension. The fixes are
before/after: 'I used to X. Now I Y.'

WORD BUDGET — 600+ words. Extend the mechanism and the
diagnostics if under.

NO LABELS. Do NOT write '**Checklist**' or any bold label. Do NOT write 'Checklist:' or any beat name as a header. The checklist begins with 'Can I...' — nothing above it. Continuous prose. No headings, no bullets. End with this CTA:

{cta_text}
"""


PROMPT_TEMPLATE_F3 = """Form: F3 — Structural Explainer (breadth).

You are teaching how breadth behaves and how it interacts with
systematic strategies. Not tied to the current moment.

Here is what the trader tracks, in their own words. The only
source of facts.

{conditions_block}

Write the F3 Structural Explainer now.

  - Here is how breadth behaves.
  - Here is what it does to a systematic strategy.
  - Here is how to recognize it.
  - Four to six observable diagnostics.
  - Four to five process adjustments.

VOICE. The mechanism is impersonal. The hook, diagnostics, and
fixes are FIRST PERSON. Never 'you,' 'your,' 'you can.'

FIXES ARE BEFORE/AFTER. Every fix begins with 'I used to' or
'I now.' Never a bare imperative.

WORD BUDGET — 600+ words. Extend the mechanism and the
diagnostics if under.

NO LABELS. Do NOT write '**Checklist**' or any bold label. Do NOT write 'Checklist:' or any beat name as a header. The checklist begins with 'Can I...' — nothing above it. Continuous prose. No headings, no bullets. End with this CTA:

{cta_text}
"""


_FORMS = ["F1", "F2", "F3"]
_TEMPLATES = {
    "F1": PROMPT_TEMPLATE,
    "F2": PROMPT_TEMPLATE_F2,
    "F3": PROMPT_TEMPLATE_F3,
}


import re as _re


def _strip_bold_labels(body: str) -> str:
    """Remove '**Label**' lines and '*Label*' lines at line starts.
    The register forbids bold labels and section headers.
    Also strips 'Checklist:' style trailing-colon labels."""
    if not body:
        return body
    lines = []
    for ln in body.split("\n"):
        stripped = ln.strip()
        # '**Checklist**' on its own line.
        if _re.match(r"^\*\*[^*]+\*\*\s*$", stripped):
            continue
        # '**Checklist**' inline at line start followed by text.
        ln = _re.sub(r"^\s*\*\*[^*]+\*\*\s*", "", ln)
        # 'Checklist:' at line start.
        ln = _re.sub(r"^\s*Checklist:\s*", "", ln)
        lines.append(ln)
    return "\n".join(lines)


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    mc = await get_market_conditions(session, org_id)
    if not mc.get("what_i_track"):
        log.warning("market_condition.no_conditions", org_id=org_id[:8])
        return {"status": "no_market_conditions", "slug": subblock["slug"]}

    conditions_block = format_conditions_block(mc)

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

    from packages.core.config import get_settings as _gs
    _url = _gs().product_url
    cta_text = (
        "I built a regime-tracking sheet that logs candidate counts, "
        "realized volatility, and average range alongside my trades — "
        "so I can see which environments my rules work in. If that's "
        f"useful, email me and I'll send it: {_url}"
    )

    # Rotate the form: F1 regime report, F2 process diary,
    # F3 structural explainer.
    form_choice = _FORMS[tier_index % len(_FORMS)]
    user_prompt = _TEMPLATES[form_choice].format(
        conditions_block=conditions_block,
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
        prompt_name="trading.market_condition",
        source_payload={
            "form": form_choice,
            "register": register,
            "tier": tier,
            "cta_text": cta_text,
            "market_conditions": mc,
        },
        target_words=target_words,
        form=None,
        tone=None,
        voice=None,
        skip_verify=True,
    )

    try:
        post.body = _strip_bold_labels(post.body)
    except Exception:
        pass

    try:
        post.body = post_process_article(post.body)
    except Exception as _pp:
        log.warning("market_condition.post_process_failed",
                    error=str(_pp)[:200])

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "form": form_choice,
            "register": register,
            "tier": tier,
            "cta_text": cta_text,
        },
        source_urls=[],
        symbols=[],
    )
    await session.commit()

    log.info(
        "market_condition.persisted",
        post_id=post_id[:8],
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "word_count": post.word_count,
    }
