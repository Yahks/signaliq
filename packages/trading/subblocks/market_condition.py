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
from ..market_conditions_skeleton import build_conditions_skeleton
from ..market_condition_validator import validate_market_condition
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

STRUCTURED SKELETON — computed by Python. Every number you cite
MUST come from this block. Do not invent a metric.

{skeleton}

If the skeleton has a RESOLVED_RULE block, quote the resolution
verbatim: the tightened stop replaces the suspended rule. Do NOT
write "suspend" for that regime — the rule is superseded, not
suspended. Do NOT present the tightened stop and the suspension as
both active. Do NOT add a clause claiming to resolve the conflict;
state the resolved rule as a fact.

The mechanism beat draws ONLY from the MECHANISMS block. Quote the
trader's phrasing; do not sharpen it. If the block says "after the
daily settlement window" without a time, do not write "4pm" or any
specific hour — the trader never stated one.

If the skeleton has a DERIVED ANALYSIS block, it is quoted in the
diagnostics beat (beat 4) as one of the observations — not as a
separate closing paragraph. The WARNING's budget branch is part of
that observation; commit to one branch.

Write the F1 Regime Report now.

  - Name the regime states the skeleton reports: which regimes
    are ACTIVE, which are INACTIVE, which are UNKNOWN. Do not call
    a regime active that the skeleton marks INACTIVE or UNKNOWN.
  - Apply ONLY the rules listed under ACTIVE_RULES. A rule tied
    to an INACTIVE or UNKNOWN regime is not in force — do not
    apply it, and do not cite its numbers even as historical
    context.
  - Each regime's rules are its own. Stop-loss percentages, unit
    caps, scan-skip conditions, and order-splitting instructions
    belong to the regime whose rule text states them and to no
    other. Do not carry a rule from a regime that was active in a
    previous article into this one.
  - State the LIMITS plainly: single snapshot, no persistence, no
    base rate, no conditional expectancy. Do not claim the regime
    has persisted for N days.
  - If TRADE LINKAGE shows a negative net, say so. A
    rules-compliant regime note can still sit on a losing trade
    record — name the loss.

BEAT SHAPE — seven beats, in order:

  1. Hook — first person, with tension.
  2. Problem — one sentence. What the reading did to your read.
  3. Mechanism — impersonal. How the named regimes behave.
  4. Diagnostics — first person. 4-6 observations drawn from
     READINGS, REGIMES, and the TRADE LINKAGE / DERIVED ANALYSIS
     block. One observation MUST be the trade record, stated in
     full: the window dates (window_start to window_end), the win
     rate, the expectancy per trade, the equity impact, at least
     one per-symbol name from per_symbol_pnl, and which budget
     branch applies — "the budget was not enforced in this sample"
     OR "the sample includes pre-rule trades". This is a required
     observation, not a closing remark. State the win-rate CI
     (±Xpp) or say the edge is statistically conclusive.
  5. Fixes — 4-5 before/after changes drawn ONLY from
     ACTIVE_RULES.
  6. Reframe — ONE SENTENCE, STANDALONE. Impersonal.
  7. End — end on the reframe. No checklist, no CTA, no URL.

WORD BUDGET — 820 words MINIMUM. Hard floor: an article under 820
words is rejected. Reach it by developing these beats, not by
padding:
  - Mechanism: 150-200 words on how the named regimes interact.
  - Diagnostics: 4-6 observations, 70-100 words each; the trade-
    record observation is 100-140 words on its own.
  - Fixes: 4-5 adjustments, 70-100 words each, each naming the
    rule that governs it.
  - LIMITS: 40-60 words on what the snapshot cannot tell you.
Do not repeat a point to add length. If you are short, add depth
to the mechanism and the diagnostics, not summary sentences.

FIXES ARE BEFORE/AFTER. Every fix in the fixes beat begins with
'I used to' or 'I now.' Never a bare imperative.

VOICE. The mechanism is impersonal. The hook, diagnostics, and
fixes are FIRST PERSON. Never 'you,' 'your,' 'you can.'

NO LABELS. Do NOT write '**Checklist**' or any bold label. Do
NOT write 'Checklist:' or any beat name as a header. Continuous
prose. No headings, no bullets. Do NOT end with a CTA, a URL, an
email address, or a marketing line.
"""


PROMPT_TEMPLATE_F2 = """Form: F2 — Process Diary (breadth).

You are writing a first-person process diary. Report what your
system produced this period and what it reveals about the
current breadth state.

Here is what the trader tracks, in their own words. The only
source of facts.

{conditions_block}

STRUCTURED SKELETON — computed by Python. Every number you cite MUST come from this block.

{skeleton}

If the skeleton has a RESOLVED_RULE block, quote the resolution
verbatim: the tightened stop replaces the suspended rule. Do NOT
write "suspend" for that regime — the rule is superseded, not
suspended. Do NOT present the tightened stop and the suspension as
both active. Do NOT add a clause claiming to resolve the conflict;
state the resolved rule as a fact.

The mechanism beat draws ONLY from the MECHANISMS block. Quote the
trader's phrasing; do not sharpen it. If the block says "after the
daily settlement window" without a time, do not write "4pm" or any
specific hour — the trader never stated one.

If the skeleton has a DERIVED ANALYSIS block, it is quoted in the
diagnostics beat (beat 4) as one of the observations — not as a
separate closing paragraph. The WARNING's budget branch is part of
that observation; commit to one branch.

Write the F2 Process Diary now.

  - Name the regime states the skeleton reports: which regimes
    are ACTIVE, which are INACTIVE, which are UNKNOWN. Do not call
    a regime active that the skeleton marks INACTIVE or UNKNOWN.
  - Apply ONLY the rules listed under ACTIVE_RULES. A rule tied
    to an INACTIVE or UNKNOWN regime is not in force — do not
    apply it, and do not cite its numbers even as historical
    context.
  - Each regime's rules are its own. Stop-loss percentages, unit
    caps, scan-skip conditions, and order-splitting instructions
    belong to the regime whose rule text states them and to no
    other. Do not carry a rule from a regime that was active in a
    previous article into this one.
  - State the LIMITS plainly: single snapshot, no persistence, no
    base rate, no conditional expectancy. Do not claim the regime
    has persisted for N days.
  - The trade record is one of the diagnostics: state the window
    dates, the win rate with its CI, the expectancy per trade, the
    equity impact, at least one per-symbol name from
    per_symbol_pnl, and ONE budget branch committed. This is
    required, not optional.

  - My scan returned [result] this week.
  - The current breadth state (wide, narrow, or transitional).
  - The structural reason — why breadth changes.
  - What it means for my strategies.
  - Four to six lived observations.
  - Four to five process adjustments I made.

FIRST PERSON throughout. The hook has tension. The fixes are
before/after: 'I used to X. Now I Y.'

WORD BUDGET — 820 words MINIMUM. Hard floor: an article under 820
words is rejected. Reach it by developing these beats, not by
padding:
  - Mechanism: 150-200 words on how the named regimes interact.
  - Diagnostics: 4-6 observations, 70-100 words each; the trade-
    record observation is 100-140 words on its own.
  - Fixes: 4-5 adjustments, 70-100 words each, each naming the
    rule that governs it.
  - LIMITS: 40-60 words on what the snapshot cannot tell you.
Do not repeat a point to add length. If you are short, add depth
to the mechanism and the diagnostics, not summary sentences.

NO LABELS. Do NOT write '**Checklist**' or any bold label. Do NOT write 'Checklist:' or any beat name as a header. Continuous prose. No headings, no bullets. Do NOT end with a CTA, a URL, an email address, or a marketing line.
"""


PROMPT_TEMPLATE_F3 = """Form: F3 — Structural Explainer (breadth).

You are teaching how breadth behaves and how it interacts with
systematic strategies. Not tied to the current moment.

Here is what the trader tracks, in their own words. The only
source of facts.

{conditions_block}

STRUCTURED SKELETON — computed by Python. Every number you cite MUST come from this block.

{skeleton}

If the skeleton has a RESOLVED_RULE block, quote the resolution
verbatim: the tightened stop replaces the suspended rule. Do NOT
write "suspend" for that regime — the rule is superseded, not
suspended. Do NOT present the tightened stop and the suspension as
both active. Do NOT add a clause claiming to resolve the conflict;
state the resolved rule as a fact.

The mechanism beat draws ONLY from the MECHANISMS block. Quote the
trader's phrasing; do not sharpen it. If the block says "after the
daily settlement window" without a time, do not write "4pm" or any
specific hour — the trader never stated one.

If the skeleton has a DERIVED ANALYSIS block, it is quoted in the
diagnostics beat (beat 4) as one of the observations — not as a
separate closing paragraph. The WARNING's budget branch is part of
that observation; commit to one branch.

Write the F3 Structural Explainer now.

  - Name the regime states the skeleton reports: which regimes
    are ACTIVE, which are INACTIVE, which are UNKNOWN. Do not call
    a regime active that the skeleton marks INACTIVE or UNKNOWN.
  - Apply ONLY the rules listed under ACTIVE_RULES. A rule tied
    to an INACTIVE or UNKNOWN regime is not in force — do not
    apply it, and do not cite its numbers even as historical
    context.
  - Each regime's rules are its own. Stop-loss percentages, unit
    caps, scan-skip conditions, and order-splitting instructions
    belong to the regime whose rule text states them and to no
    other. Do not carry a rule from a regime that was active in a
    previous article into this one.
  - State the LIMITS plainly: single snapshot, no persistence, no
    base rate, no conditional expectancy. Do not claim the regime
    has persisted for N days.
  - The trade record is one of the diagnostics: state the window
    dates, the win rate with its CI, the expectancy per trade, the
    equity impact, at least one per-symbol name from
    per_symbol_pnl, and ONE budget branch committed. This is
    required, not optional.

  - Here is how breadth behaves.
  - Here is what it does to a systematic strategy.
  - Here is how to recognize it.
  - Four to six observable diagnostics.
  - Four to five process adjustments.

VOICE. The mechanism is impersonal. The hook, diagnostics, and
fixes are FIRST PERSON. Never 'you,' 'your,' 'you can.'

FIXES ARE BEFORE/AFTER. Every fix begins with 'I used to' or
'I now.' Never a bare imperative.

WORD BUDGET — 820 words MINIMUM. Hard floor: an article under 820
words is rejected. Reach it by developing these beats, not by
padding:
  - Mechanism: 150-200 words on how the named regimes interact.
  - Diagnostics: 4-6 observations, 70-100 words each; the trade-
    record observation is 100-140 words on its own.
  - Fixes: 4-5 adjustments, 70-100 words each, each naming the
    rule that governs it.
  - LIMITS: 40-60 words on what the snapshot cannot tell you.
Do not repeat a point to add length. If you are short, add depth
to the mechanism and the diagnostics, not summary sentences.

NO LABELS. Do NOT write '**Checklist**' or any bold label. Do NOT write 'Checklist:' or any beat name as a header. Continuous prose. No headings, no bullets. Do NOT end with a CTA, a URL, an email address, or a marketing line.
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

    # Build the structured skeleton the prompt quotes. This is
    # the only source of numbers for the article; the validator
    # checks the body against it.
    skeleton = await build_conditions_skeleton(session, org_id, mc)

    # Rotate the form: F1 regime report, F2 process diary,
    # F3 structural explainer.
    form_choice = _FORMS[tier_index % len(_FORMS)]
    user_prompt = _TEMPLATES[form_choice].format(
        conditions_block=conditions_block,
        skeleton=skeleton,
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

    # Validate against the skeleton. Separate from
    # article_validator.validate(), which is trade-centric and would
    # mis-fire on a regime note.
    reasons = validate_market_condition(post.body, skeleton)
    if reasons:
        log.warning(
            "market_condition.validator_rejected",
            slug=subblock["slug"],
            reasons=reasons,
        )
        post.verification_ok = False
        post.unsupported_claims = list(
            getattr(post, "unsupported_claims", None) or []
        ) + reasons

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "form": form_choice,
            "register": register,
            "tier": tier,
            "skeleton": skeleton,
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
