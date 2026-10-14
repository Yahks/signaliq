"""market_analysis: F1 regime report — volatility.

Reads the trader's market-conditions block. Picks the market-regime
register. Writes a regime report on the current volatility state.

No Yahoo fetch. No computed numbers. The trader's observations are
the source.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from ..editorial import compose_system_prompt
from ..market_conditions import (
    format_conditions_block,
    get_market_conditions,
    summarize_market_events,
)
from ..post_process import post_process_article
from ..registers._selector import pick_register
from ..registers import REGISTERS
from .base import call_llm_json_verified, persist_post
from .market_analysis_templates import pick_template

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Subject selection — the pipeline writes about ONE subject per article.
# The six subjects map to the trader's five readings. Each has a match
# rule against the current reading; the selector picks the least-recently
# used active subject so consecutive articles never share a subject.
# ─────────────────────────────────────────────────────────────────────

_SUBJECT_RULES = {
    # key                field                    op   threshold
    "correlation":       ("correlation_current",   ">", 0.50),
    "vol_compressed":    ("realized_vol_current",  "<", 0.65),
    "vol_expanded":      ("realized_vol_current",  ">", 1.15),
    "thin_liquidity":    ("spread_current",        ">=", 0.50),
    "narrow_breadth":    ("breadth_current",       "<=", 1.0),
    "range_contraction": ("range_current",         "<", 1.35),
}

_SUBJECT_ORDER = [
    "correlation",
    "vol_compressed",
    "vol_expanded",
    "thin_liquidity",
    "narrow_breadth",
    "range_contraction",
]


def _active_subjects(mc: dict) -> list[str]:
    """Return the subject keys whose reading currently trips the rule."""
    active = []
    for key, (field, op, thresh) in _SUBJECT_RULES.items():
        v = mc.get(field)
        if v is None or v == "":
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        ok = (
            (op == ">"  and val >  thresh) or
            (op == ">=" and val >= thresh) or
            (op == "<"  and val <  thresh) or
            (op == "<=" and val <= thresh)
        )
        if ok:
            active.append(key)
    return active


async def pick_subject(
    session: AsyncSession,
    *,
    org_id: str,
    subblock_id: str,
    mc: dict,
    window: int = 10,
) -> str:
    """Pick the subject for this article.

    Reads the last `window` posts' subjects. Returns the least-recently
    used subject among the currently active ones. If no active subject
    is available (all readings normal), returns the subject whose
    reading is closest to its threshold.
    """
    active = _active_subjects(mc)

    rows = (await session.execute(sql_text("""
        SELECT subject FROM trading_posts
        WHERE org_id = :o AND subblock_id = :s
          AND subject IS NOT NULL AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT :n
    """), {"o": org_id, "s": subblock_id, "n": window})).all()
    recent = [r[0] for r in rows if r[0]]

    if active:
        for key in _SUBJECT_ORDER:
            if key in active and key not in recent[:3]:
                log.info("market_analysis.subject_picked",
                         subject=key, reason="active_and_not_recent")
                return key
        for key in reversed(_SUBJECT_ORDER):
            if key in active:
                log.info("market_analysis.subject_picked",
                         subject=key, reason="active_fallback")
                return key

    best = None
    best_gap = None
    for key, (field, op, thresh) in _SUBJECT_RULES.items():
        v = mc.get(field)
        if v is None or v == "":
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        gap = abs(val - thresh)
        if key in recent[:3]:
            gap *= 10
        if best_gap is None or gap < best_gap:
            best = key
            best_gap = gap

    if best is None:
        best = "correlation"

    log.info("market_analysis.subject_picked", subject=best,
             reason="closest_to_threshold")
    return best


async def pick_subject_from_payload(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict,
    mc: dict,
) -> str:
    """Wrapper for generate_subblock."""
    return await pick_subject(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        mc=mc,
    )


# ─────────────────────────────────────────────────────────────────────
# Block filter — each block register reads only its own readings.
# The block is a hard boundary: the model cannot pivot to another
# regime because the other readings are not in its payload.
# ─────────────────────────────────────────────────────────────────────

# Which columns each block's conditions block carries.
_BLOCK_READINGS = {
    "block-b1-correlation": (
        "correlation_current",
        "correlation",
    ),
    "block-b2-volatility": (
        "realized_vol_current",
        "realized_vol",
    ),
    "block-b3-liquidity": (
        "spread_current",
        "spread",
    ),
    "block-b4-breadth": (
        "breadth_current",
        "breadth",
    ),
    "block-b5-range": (
        "range_current",
        "range",
    ),
    "block-b6-trend": (
        "realized_vol_current",
        "breadth_current",
        "trend",
    ),
}

# Which research keys each block may cite from.
_BLOCK_RESEARCH_KEYS = {
    "block-b1-correlation": {"correlation", "regime_duration"},
    "block-b2-volatility":  {"vol_compressed", "vol_expanded", "regime_duration"},
    "block-b3-liquidity":   {"thin_liquidity", "regime_duration"},
    "block-b4-breadth":     {"regime_duration"},
    "block-b5-range":       {"regime_duration"},
    "block-b6-trend":       {"vol_compressed", "vol_expanded", "regime_duration"},
}


def block_key_from_register(register: str) -> str | None:
    """Return the block-* key if `register` is a block register."""
    if register and register.startswith("block-"):
        return register
    return None


def filter_mc_for_block(mc: dict, block_key: str) -> dict:
    """Return a copy of `mc` containing only the block's readings.

    Keeps the descriptive columns (what_i_track, thresholds, etc.)
    unchanged — the model still needs them. Only the CURRENT
    READING fields are masked: non-block readings are removed.
    """
    spec = _BLOCK_READINGS.get(block_key)
    if not spec:
        return mc

    keep_fields = {spec[0]}
    if block_key == "block-b6-trend":
        keep_fields = {"realized_vol_current", "breadth_current"}

    out = dict(mc)
    for field in (
        "realized_vol_current", "spread_current", "range_current",
        "correlation_current", "breadth_current",
    ):
        if field not in keep_fields:
            out.pop(field, None)
    return out


def filter_research_for_block(block_key: str, research_block: list) -> list:
    """Keep only the research rows whose key is in the block's allowlist."""
    allowed = _BLOCK_RESEARCH_KEYS.get(block_key)
    if not allowed:
        return research_block
    filtered = [r for r in research_block if r.get("key") in allowed]
    return filtered if filtered else research_block


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    """Generate one market_analysis blog post.

    Reads two reports: the trader's own dashboard (market_conditions)
    and the market's own record of the last 24 hours (market_events).
    Hands both to the model categorically. The model connects them.
    """
    # 1. The trader's dashboard.
    mc = await get_market_conditions(session, org_id)
    if not mc.get("what_i_track"):
        log.warning("market_analysis.no_conditions", org_id=org_id[:8])
        return {"status": "no_market_conditions", "slug": subblock["slug"]}

    # 2. The market's own report — last 24 hours of events, summarized.
    events_summary = await summarize_market_events(session, org_id=org_id)

    # 3. Build the source block. Two sections, labeled.
    conditions_block = format_conditions_block(mc, research_block=None)
    if events_summary:
        source_block = (
            "--- MY READINGS AND PROCESS ---\n"
            + conditions_block
            + "\n\n--- THE MARKET'S OWN REPORT (last 24 hours) ---\n"
            + events_summary
        )
    else:
        source_block = (
            "--- MY READINGS AND PROCESS ---\n"
            + conditions_block
        )

    # 4. Register — always "market_analysis", single.
    register = "market_analysis"
    target_words = 1000

    # 5. CTA. One form. The blog offers the trader's scan log.
    from packages.core.config import get_settings as _get_settings
    _url = _get_settings().product_url
    cta_text = (
        "I keep a scan log that records each candidate, the regime at "
        "entry, and what my system did with it — so I can see which "
        "regimes produce the trades I actually want. If that's useful, "
        f"email me and I'll send it: {_url}"
    )

    # 6. Assemble the user prompt.
    template = pick_template()
    user_prompt = template.format(
        source_block=source_block,
        cta_text=cta_text,
    )

    # 7. Call the LLM. No form/tone/voice — the register covers it.
    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=compose_system_prompt(
            None,
            subblock.get("style_guide"),
            "You write a personal market blog for systematic traders.",
            form=None,
            tone=None,
            slug=subblock["slug"],
            register=register,
            tier=None,
        ),
        user=user_prompt,
        prompt_name="trading.market_analysis",
        source_payload={
            "register": register,
            "cta_text": cta_text,
            "market_conditions": mc,
            "events_summary": events_summary,
            "target_words": target_words,
        },
        target_words=target_words,
        form=None,
        tone=None,
        voice=None,
        skip_verify=False,
        skip_standard=True,
    )

    post.subject = register

    try:
        post.body = post_process_article(post.body)
    except Exception as _pp:
        log.warning("market_analysis.post_process_failed",
                    error=str(_pp)[:200])

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "register": register,
            "cta_text": cta_text,
            "market_conditions": mc,
            "events_summary": events_summary,
            "target_words": target_words,
        },
        source_urls=[],
        symbols=[],
    )
    await session.commit()

    log.info(
        "market_analysis.persisted",
        post_id=post_id[:8],
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
        register=register,
    )
    return {
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "word_count": post.word_count,
        "register": register,
    }
