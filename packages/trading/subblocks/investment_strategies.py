"""investment_strategies: a distribution review of one strategy.

Picks a strategy (long_breakout or short_breakdown), computes the
full distribution from trade_log, and writes a review. Every
number in the article comes from the distribution block, not the
model's imagination.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from ..editorial import compose_system_prompt
from ..registers._selector import pick_register
from .base import call_llm_json_verified, persist_post
from .investment_strategies_distribution import (
    pick_strategy,
    build_distribution_block,
)

log = get_logger(__name__)


PROMPT_TEMPLATE = """You are writing a strategy review for a systematic trader.

You have a full distribution of trades of one strategy type. Your job
is to write a review — the sample size, the win rate, the expectancy,
the biggest win, the biggest loss, and what it tells you about the
strategy's real edge.

VOICE & STYLE
- Write in first person. ("I run", "I watched", "My sample")
- Do NOT write "the trader". Write "I".
- Do NOT write a portfolio essay. Do NOT write about "how I allocate
  across strategies." Write about THIS ONE STRATEGY and the trades
  in its sample.

HARD RULES (the delivery check will fail if you break these)
1. EVERY NUMBER MUST COME FROM THE SOURCE BLOCK. Do NOT invent a
   win rate, an expectancy, a sample size, or a dollar amount.
   If a number is not in the SOURCE BLOCK below, do not write it.
2. DO NOT CHERRY-PICK. The article's core is the SAMPLE SIZE and
   the MATH. The five recent trades and the outliers are
   illustrations, not the evidence.
3. YOU MUST STATE THE MATH. Explicitly write the win rate, the
   average win, the average loss, the payoff ratio, and the
   expectancy per trade. Show the arithmetic, not just the
   conclusion.
4. NO ENUMERATED LISTS. Strictly forbidden: "First", "Second",
   "Third", "One", "Two", "Three", "Next", "Finally". Continuous
   narrative prose.
5. NO META-COMMENTARY. Do NOT write "In this article", "Let me
   explain", "Here is what I learned", "Follow my blog", "If you
   found this useful."
6. NO CAUSAL LANGUAGE. Do NOT write "signaled", "suggested",
   "indicated", "implied". State the number, state what happened.
7. THE REFRAME. End the prose with ONE sentence that makes a
   distinction. Form: 'X isn't Y, it's Z.' (e.g., "A breakout
   isn't a prediction, it's a bet on continuation.")

BEAT SHAPE — nine beats, in order. Do NOT skip any.

1. THE HOOK. The most recent trade of this type, in detail: symbol,
   timeframe, entry, exit, result, timestamp.

2. THE RULE. What setup fires. Entry condition, stop placement,
   target. In exact terms, from the STRATEGY block.

3. THE SAMPLE. How many trades. Over what period. State the number.
   Then use the SYMBOL FREQUENCY, TIMEFRAME DISTRIBUTION, and
   MONTHLY DISTRIBUTION sections. Name the top symbols by count
   (not by win rate — the model's previous claim of "tech cluster"
   was wrong). Name which timeframe dominates. Name which month had
   the most trades and which had the best and worst win rates. If
   the distributions show a real finding — e.g., "the 1-hour chart
   bleeds at 32.9% win rate while the daily chart wins 49.5%" —
   state it. That is a finding. Give the numbers.

4. THE MATH. Win rate, average win, average loss, payoff ratio,
   expectancy. Show the arithmetic. e.g., "42.3% win rate times
   an average win of $2,212 plus 57.7% loss rate times an average
   loss of $1,483 gives an expectancy of +$80.75 per trade."

5. THE BEST AND THE WORST. The biggest win and the biggest loss
   from the sample, in detail. What made them outliers.

6. THE FAILURE MODE. When this strategy loses. What conditions.
   What it can't detect. What the losing trades have in common.

7. THE FIX. One concrete rule change. You may describe what the
   fix WOULD do if it works. You may NOT claim that the fix has
   already improved the win rate, the average loss, or the
   expectancy — the source block contains no post-fix data.
   End the beat with the honest admission that the fix is
   unproven.

8. THE REFRAME. One sentence, a distinction.

9. THE CHECKLIST. Five questions you ask before the next trade of
   this type. Specific to THIS strategy — reference the entry
   condition, the stop, or the sample.

Aim for 850 to 1230 words.

SOURCE BLOCK:
{source_block}

End with the checklist. Do NOT write a CTA, a link, a product pitch,
or an invitation to contact. The article ends with the last checklist
question. Nothing after it.
"""


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    slug = subblock["slug"]

    strategy = await pick_strategy(
        session, org_id=org_id, subblock_id=subblock["id"],
    )

    distribution_block = await build_distribution_block(
        session, org_id=org_id, strategy=strategy,
    )
    if not distribution_block:
        log.warning("investment_strategies.no_distribution",
                    slug=slug, strategy=strategy)
        return {"status": "no_distribution", "slug": slug}

    user_prompt = PROMPT_TEMPLATE.format(
        source_block=distribution_block,
    )

    tier_index = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM trading_posts
        WHERE subblock_id = :sid AND deleted_at IS NULL
    """), {"sid": subblock["id"]})).scalar() or 0
    from ..registers import pick_tier, target_words_for
    tier = pick_tier(tier_index)
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
            "You write strategy reviews for systematic traders.",
            form=None, tone=None, slug=slug,
            register=register, tier=None,
        ),
        user=user_prompt,
        prompt_name="trading.investment_strategies",
        source_payload={
            "register": register, "tier": tier,
            "strategy": strategy,
            "distribution_block": distribution_block,
        },
        target_words=target_words,
        form=None, tone=None, voice=None,
        skip_verify=False,
    )

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "register": register, "tier": tier,
            "strategy": strategy,
            "distribution_block": distribution_block,
        },
        source_urls=[], symbols=[],
    )
    await session.commit()

    log.info(
        "investment_strategies.persisted",
        post_id=post_id[:8],
        strategy=strategy,
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok", "slug": slug,
        "post_id": post_id,
        "strategy": strategy,
        "word_count": post.word_count,
    }
