"""Draft the trader process — one full nine-section document."""
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
    "daily_scan", "entry_logic", "position_sizing", "exit_logic",
    "holding_period", "research_to_rules", "chart_overlay",
    "no_backtest_rule", "multi_strategy_blend",
    "rolling", "stocks_vs_options", "universes",
    "emotion_removal", "fundamental_metrics",
    "trade_history_summary",
)

SECTION_HINTS = {
    "daily_scan": "The daily scan. What you screen for. The universe it produces. When it runs. How many setups per day.",
    "entry_logic": "The effects you harvest. The specific entry conditions. The thresholds. The research behind each rule.",
    "position_sizing": "Base size. The cap. The size of the cap and why. Whether you scale. Whether you martingale.",
    "exit_logic": "The targets. The stops. Whether you roll. Whether you hold overnight.",
    "holding_period": "The range. The median. How the holding period is chosen.",
    "research_to_rules": "The papers. Author, year, the rule each produced. How each became a condition.",
    "chart_overlay": "What you still check by hand. The specific lines you draw. Why you do not automate it.",
    "no_backtest_rule": "Why you do not backtest. What you do instead.",
    "multi_strategy_blend": "The strategies. The percentage of trades each produces. Whether they run together.",
    "rolling": "How you roll options or futures positions. When. Why. What triggers a roll. What you give up by rolling.",
    "stocks_vs_options": "Why you use one or the other. What each is for. When the choice matters. Which you prefer and why.",
    "universes": "How your scan criteria define the tradable set. The filters that decide what's in and what's out. Why the universe is bounded this way.",
    "emotion_removal": "What's systematized. What still requires judgment. Where emotion still shows up in the process.",
    "fundamental_metrics": "Which fundamental checks you run before entering. Why. What you do with them. Not values — the awareness that filters setups.",
    "trade_history_summary": "Your aggregate performance over the trader's actual history. Number of trades taken, win rate, average winner and average loser in percent, max drawdown in percent and when it occurred, Sharpe ratio if known, longest winning and losing streaks, best and worst month. Specific numbers, in prose. This is the evidence source for any performance claim in a future article.",
}


UNIVERSES: list[str] = [
    "the E-mini S&P 500 futures (ES) on CME Globex, regular trading hours",
    "the E-mini Nasdaq futures (NQ) on CME, regular trading hours",
    "front-month WTI crude oil futures (CL) on NYMEX",
    "10-year Treasury note futures (ZN) on CBOT",
    "the top 500 US equities by 20-day dollar volume",
    "the Russell 2000 (IWM) constituents",
    "US sector ETFs (XLE, XLF, XLK, XLV, XLI, XLP)",
    "high-yield corporate credit ETFs (HYG, JNK, EMB)",
    "front-month gold futures (GC) on COMEX",
    "the top 50 crypto pairs by 30-day notional volume",
]


DRAFT_PROMPT = """You are drafting a complete retail systematic trader's process document.

The trader operates on: {universe}

Every section must be written for THIS universe only. Do not write
about "the S&P 500" unless that is the universe above. If the
universe is futures, the trader trades futures. If it is crypto,
crypto. If it is ETFs, ETFs. The instruments, the session hours,
the thresholds — all must fit the universe.

The trader will review and correct it. But the draft must already be at the level of a real trader's operating notes: specific instruments, specific thresholds, specific times, specific reasons, specific sources. Not generic rules.

For each section, write one entry of 3-6 sentences. Plain declarative first-person voice. Present tense.

REQUIRED in every section:
- A specific instrument, universe, or rule.
- A specific number — a threshold, size, cap, percentage, time.
- A specific reason for the number.
- For the research section, name a real paper with author and year (Ball and Brown 1968, Jegadeesh and Titman 1993, Faber 2007, O'Neil 1988, Crabel 1990, Novy-Marx 2013).

Sections:

{sections}

Return JSON, one key per section, one string value per section.
"""


def _render() -> str:
    return "\n".join(f"- {s}: {SECTION_HINTS[s]}" for s in SECTIONS)


async def draft_for_org(
    session: AsyncSession, org_id: str, *, force: bool = False,
) -> int:
    """Generate one full draft document. Returns 1 on success, 0 on failure.

    force=False (default, used by the Celery beat): respect the 6-hour
    guard — do not stack a new draft on top of an unreviewed one.

    force=True (used by POST /api/trader-process/generate, i.e. the
    user clicked Generate): always create a new draft. The trader
    explicitly asked for it.
    """
    if not force:
        existing = (await session.execute(sql_text("""
            SELECT 1 FROM trader_process_drafts
            WHERE org_id = :oid AND reviewed_at IS NULL
              AND created_at > now() - interval '6 hours'
            LIMIT 1
        """), {"oid": org_id})).first()
        if existing:
            log.info("process_drafter.recent_draft_exists", org_id=org_id[:8])
            return 0

    _ctx_org_id.set(org_id)

    try:
        raw = await complete(
            role="reasoning",
            system=(
                "You draft a systematic trader's process document. "
                "Specific instruments, numbers, reasons, sources. JSON only."
            ),
            user=(
                DRAFT_PROMPT.format(
                    sections=_render(),
                    universe=random.choice(UNIVERSES),
                )
                + f"\n\n[draft {uuid.uuid4().hex[:8]}]"
            ),
            org_id=org_id,
            prompt_version_id=None,
            purpose="trading.process_draft",
            temperature=0.85,
            max_tokens=4096,
            json_mode=False,
            reasoning_effort="medium",
            use_cache=False,   # S.9.03 — every Generate must produce
                               # fresh content; the cached response
                               # made every draft identical.
        )
    except Exception as exc:
        log.warning("process_drafter.llm_failed",
                    org_id=org_id[:8], error=str(exc)[:200])
        return 0

    if isinstance(raw, str):
        try:
            drafts = json.loads(raw)
        except Exception:
            log.warning("process_drafter.parse_failed", org_id=org_id[:8])
            return 0
    else:
        drafts = raw

    cols = ", ".join(SECTIONS)
    vals = ", ".join(f":{s}" for s in SECTIONS)
    params = {"oid": org_id}
    for s in SECTIONS:
        v = drafts.get(s) or ""
        params[s] = v.strip()[:8000] if isinstance(v, str) else ""

    await session.execute(sql_text(f"""
        INSERT INTO trader_process_drafts (org_id, {cols})
        VALUES (:oid, {vals})
    """), params)
    await session.commit()
    log.info("process_drafter.drafted", org_id=org_id[:8])
    return 1
