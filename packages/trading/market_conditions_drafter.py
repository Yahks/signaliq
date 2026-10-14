"""Draft the trader's market-conditions block.

Reads the trader's process document as the primary source of context
(what they trade, how they trade it, their instruments and
timeframes), then asks the model to draft the six market-observations
sections that fit that operation.

Same shape as process_drafter.py, different content.
"""
from __future__ import annotations

import json
import random
import uuid

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.router import complete
from packages.core.context import current_org_id as _ctx_org_id
from packages.core.logging import get_logger

log = get_logger(__name__)


SECTIONS = (
    "what_i_track",
    "baselines",
    "regimes",
    "thresholds",
    "adjustments",
    "mechanisms",
)


SECTION_HINTS = {
    "what_i_track": "The list of observations the trader actually measures. Candidate count, realized volatility (10-day, 20-day), median spread, average slippage, average daily range, pairwise correlation of positions. Fits the trader's actual process — a futures trader tracks different observations than an equity trader.",
    "baselines": "What 'normal' looks like for each observation. Candidate count 5-7/day. Realized vol mid-range. Spread ~1 bp. Slippage ~1 bp. Range ~1.5%. Correlation ~0.3. Specific numbers, matched to the trader's universe.",
    "regimes": "The named regimes the trader recognizes. Compressed volatility: bottom quartile of realized vol, ranges down 20%+. Expanded volatility: top quartile, ranges up. Thin liquidity: spreads 2x+. High correlation: pairwise >0.5. Narrow breadth: <40% above the 50-day.",
    "thresholds": "The specific numbers that trigger a regime change. Realized vol bottom quartile = compressed. Pairwise correlation >0.5 = high. Spread widening past 2 bp = thin. Concrete, measurable.",
    "adjustments": "What the trader does when each regime arrives. Compressed vol: no filter loosening, reduce size, watch the transition. High correlation: fewer positions, cap factor exposure. Thin liquidity: smaller orders, limit orders, liquid windows.",
    "mechanisms": "Why the state changes. Volatility clusters. Liquidity is a function of participation. Correlation rises in drawdowns. Breadth narrows before declines. Range compression precedes expansion.",
}


UNIVERSES: list[str] = [
    "US equities from the S&P 500",
    "US equity futures (ES, NQ, RTY) on CME",
    "commodity futures (GC, CL, ZN) on CME/NYMEX",
    "sector ETFs (XLK, XLF, XLE, XLV, XLI, XLP)",
    "high-yield credit ETFs (HYG, JNK, EMB)",
]


DRAFT_PROMPT = """You are drafting a retail systematic trader's market-conditions block.

The trader operates on: {universe}

Here is the trader's process document. This is the source of truth about what they actually trade and how. The market-conditions block must fit this process.

--- BEGIN TRADER'S PROCESS ---
{process_block}
--- END TRADER'S PROCESS ---

Draft the six market-conditions sections below. Each is a short
prose block written in the trader's voice, in the trader's own
register. Specific instruments, specific numbers, specific
thresholds. Not generic advice.

{section_list}

Every observation must fit the universe above. If the trader trades
ES futures, candidate count refers to ES setups, not equity
screens. If they trade GC, realized volatility is on GC.

Return a JSON object with exactly these six keys, one string value
each:

{json_keys}

Return only the JSON. No prose before or after.
"""


def _render() -> str:
    return "\n".join(f"- {s}: {SECTION_HINTS[s]}" for s in SECTIONS)


async def draft_for_org(
    session: AsyncSession, org_id: str, *, force: bool = False,
) -> int:
    """Generate one market-conditions draft. Returns 1 on success, 0 otherwise.

    force=True skips the recent-draft guard. Used by the manual
    Generate endpoint. force=False (the Celery beat) respects the
    6-hour guard.
    """
    if not force:
        existing = (await session.execute(sql_text("""
            SELECT 1 FROM market_conditions_drafts
            WHERE org_id = :oid AND reviewed_at IS NULL
              AND created_at > now() - interval '6 hours'
            LIMIT 1
        """), {"oid": org_id})).first()
        if existing:
            log.info("market_conditions_drafter.recent_draft_exists",
                     org_id=org_id[:8])
            return 0

    # Read the trader's process document as context.
    proc = (await session.execute(sql_text("""
        SELECT daily_scan, entry_logic, position_sizing, exit_logic,
               holding_period, research_to_rules, chart_overlay,
               no_backtest_rule, multi_strategy_blend,
               rolling, stocks_vs_options, universes,
               emotion_removal, fundamental_metrics
        FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
    """), {"oid": org_id})).first()
    if not proc:
        log.info("market_conditions_drafter.no_process",
                 org_id=org_id[:8])
        return 0

    proc_cols = (
        "daily_scan", "entry_logic", "position_sizing", "exit_logic",
        "holding_period", "research_to_rules", "chart_overlay",
        "no_backtest_rule", "multi_strategy_blend",
        "rolling", "stocks_vs_options", "universes",
        "emotion_removal", "fundamental_metrics",
    )
    process_block_parts: list[str] = []
    for c, v in zip(proc_cols, proc):
        s = (v or "").strip()
        if not s:
            continue
        process_block_parts.append(f"{c}: {s}")
    process_block = "\n".join(process_block_parts) or "(empty process document)"

    _ctx_org_id.set(org_id)

    universe = random.choice(UNIVERSES)
    section_list = _render()
    json_keys = json.dumps({s: "string" for s in SECTIONS}, indent=2)

    try:
        raw = await complete(
            role="reasoning",
            system=(
                "You draft a systematic trader's market-conditions "
                "block. Specific observations, baselines, regimes, "
                "thresholds. JSON only."
            ),
            user=(
                DRAFT_PROMPT.format(
                    universe=universe,
                    process_block=process_block,
                    section_list=section_list,
                    json_keys=json_keys,
                )
                + f"\n\n[draft {uuid.uuid4().hex[:8]}]"
            ),
            org_id=org_id,
            prompt_version_id=None,
            purpose="trading.market_conditions_draft",
            temperature=0.85,
            max_tokens=4096,
            json_mode=False,
            reasoning_effort="medium",
            use_cache=False,
        )
    except Exception as exc:
        log.warning("market_conditions_drafter.llm_failed",
                    org_id=org_id[:8], error=str(exc)[:200])
        return 0

    if isinstance(raw, str):
        try:
            drafts = json.loads(raw)
        except Exception:
            log.warning("market_conditions_drafter.parse_failed",
                        org_id=org_id[:8])
            return 0
    else:
        drafts = raw

    cols = ", ".join(SECTIONS)
    vals = ", ".join(f":{s}" for s in SECTIONS)
    params: dict = {"oid": org_id}
    for s in SECTIONS:
        v = drafts.get(s) or ""
        params[s] = v.strip()[:8000] if isinstance(v, str) else ""

    await session.execute(sql_text(f"""
        INSERT INTO market_conditions_drafts (org_id, {cols})
        VALUES (:oid, {vals})
    """), params)
    await session.commit()
    log.info("market_conditions_drafter.drafted", org_id=org_id[:8])
    return 1
