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
    summarize_historical_context,
    summarize_market_events,
)
from ..post_process import post_process_article
from ..registers._selector import pick_register
from ..registers import REGISTERS
from .base import call_llm_json_verified, persist_post
from .market_analysis_templates import pick_template


def _insert_table_into_body(
    body: str, table: str, trade_date: str | None,
) -> tuple[str, bool]:
    """Insert the recent-trades markdown table into the article body.

    Called after the model returns. The model reliably writes the
    trade paragraph but refuses to reproduce the markdown table in
    continuous prose, so we insert it in code.

    Returns (new_body, inserted). If `table` is empty, or the body
    already contains a markdown table header, returns (body, False).

    Insertion point: the paragraph that contains the featured trade's
    date. Insert after that paragraph. If the date is not found, fall
    back to inserting before the final one-sentence paragraph (the
    reframe line) and before the checklist.
    """
    if not table or not body:
        return body, False
    if "| Date | Instrument |" in body:
        return body, False

    # Paragraphs are separated by a blank line.
    paras = body.split("\n\n")

    # Normalize unicode hyphens/dashes so the date search matches
    # regardless of which hyphen character the model emitted.
    def _norm(s: str) -> str:
        return (
            s.replace("\u2010", "-")
             .replace("\u2011", "-")
             .replace("\u2012", "-")
             .replace("\u2013", "-")
             .replace("\u2014", "-")
             .replace("\u2212", "-")
        )

    # Preferred: find the paragraph that contains the trade date.
    insert_idx = None
    if trade_date:
        for i, para in enumerate(paras):
            if trade_date in _norm(para):
                insert_idx = i + 1
                break

    # Fallback: insert before the last short paragraph (typically
    # the reframe sentence, which is 15-30 words).
    if insert_idx is None:
        for i in range(len(paras) - 1, -1, -1):
            words = len(paras[i].split())
            if 10 <= words <= 40:
                insert_idx = i
                break

    # Last-resort fallback: before the final paragraph.
    if insert_idx is None:
        insert_idx = max(0, len(paras) - 1)

    new_paras = paras[:insert_idx] + [table] + paras[insert_idx:]
    return "\n\n".join(new_paras), True

from .market_analysis_account import (
    summarize_account_equity,
    format_account_block,
)
from .market_analysis_rotation import (
    pick_combination,
    pick_unused_trade,
    get_recent_trades,
    mark_trade_used,
    log_run,
    reset_trades_if_due,
    _recent_combinations,
)

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

    # 2. Rotation picks. Reset trade pool if every trade has
    # been used (or the reset window has passed).
    await reset_trades_if_due(session, org_id=org_id)

    recent_combos = await _recent_combinations(session, org_id=org_id)
    window_days, angle, focus = pick_combination(recent_combos)
    # Trade block disabled: trade_log rows are synthetic.
    # Re-enable when real trades are available.
    featured_trade = None
    recent_trades = []

    # 3. The market's own report — last 24 hours of events.
    events_summary = await summarize_market_events(session, org_id=org_id)

    # 4. Historical context, over the picked window.
    historical_context = await summarize_historical_context(
        session, org_id=org_id, window_days=window_days,
    )

    # 5. Trade block — featured trade + recent trades table.
    def _fmt(v):
        if v is None:
            return "None"
        try:
            f = float(v)
            return str(int(f)) if f == int(f) else format(f, ".2f")
        except Exception:
            return str(v)

    # Featured trade paragraph (feeds the source-data section).
    featured_parts = []
    if featured_trade:
        t = featured_trade
        featured_parts.append(
            "--- THE TRADE I WANT TO TALK ABOUT ---"
        )
        line = (
            f"{t['instrument']} {t['direction']} on "
            f"{str(t['traded_at'])[:10]}. "
            f"Entry {_fmt(t['entry'])}, stop {_fmt(t.get('stop'))}, "
            f"target {_fmt(t.get('target'))}, exit {_fmt(t['exit'])}. "
            f"Result {_fmt(t['result'])}. "
            f"Hold time {t.get('hold_time')}. "
            f"Exit reason: {t.get('exit_reason')}."
        )
        featured_parts.append(line)
        if t.get("lesson"):
            featured_parts.append(
                "Lesson I wrote at the time: " + str(t["lesson"])
            )
        if t.get("setup"):
            featured_parts.append("Setup: " + str(t["setup"]))
    featured_trade_block = (
        "\n".join(featured_parts) if featured_parts else ""
    )

    # Recent-trades markdown table (feeds the beat 11 instruction).
    table_parts = []
    if recent_trades:
        table_parts.append(
            "| Date | Instrument | Direction | Entry | Exit | Result |"
        )
        table_parts.append(
            "|------|------------|-----------|-------|------|--------|"
        )
        for t in recent_trades:
            table_parts.append(
                f"| {str(t['traded_at'])[:10]} | {t['instrument']} | "
                f"{t['direction']} | {_fmt(t['entry'])} | {_fmt(t['exit'])} | "
                f"{_fmt(t['result'])} |"
            )
    recent_trades_table = "\n".join(table_parts) if table_parts else ""

    # Account equity block. Computed from trade_log; inserted
    # into the article in code, attached to the trade table.
    # Account block disabled: computed from synthetic trades.
    account_block = ""

    # 6. Build the source block.
    conditions_block = format_conditions_block(mc, research_block=None)
    source_block = (
        "--- MY READINGS AND PROCESS ---\n"
        + conditions_block
    )
    if events_summary:
        source_block += (
            "\n\n--- THE MARKET'S OWN REPORT (last 24 hours) ---\n"
            + events_summary
        )
    if historical_context:
        source_block += (
            f"\n\n--- HISTORICAL CONTEXT (last {window_days} days) ---\n"
            + historical_context
        )

    # 7. Register and CTA.
    register = "market_analysis"
    target_words = 1000

    from packages.core.config import get_settings as _get_settings
    _url = _get_settings().product_url
    cta_text = (
        "I keep a scan log that records each candidate, the regime at "
        "entry, and what my system did with it — so I can see which "
        "regimes produce the trades I actually want. If that's useful, "
        f"email me and I'll send it: {_url}"
    )

    # 8. Template and user prompt.
    template = pick_template()
    template_id = "observation"  # single template for now
    user_prompt = template.format(
        source_block=source_block,
        angle=angle,
        focus=focus,
        window_days=window_days,
        cta_text=cta_text,
    )

    # 9. Call the LLM. skip_standard=True means the template is
    # the only shape contract; no STANDARD, no OUTPUT FORMAT.
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
            "historical_context": historical_context,
            "featured_trade_block": featured_trade_block,
            "recent_trades_table": recent_trades_table,
            "account_block": account_block,
            "angle": angle,
            "focus": focus,
            "window_days": window_days,
            "template_id": template_id,
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

    # Insert the recent-trades table in code. The model refuses to
    # reproduce it in continuous prose, so we do it deterministically.
    if recent_trades_table:
        trade_date = None
        if featured_trade:
            try:
                trade_date = str(featured_trade["traded_at"])[:10]
            except Exception:
                trade_date = None
        combined_block = recent_trades_table
        if account_block:
            combined_block = recent_trades_table + "\n\n" + account_block
        new_body, inserted = _insert_table_into_body(
            post.body, combined_block, trade_date,
        )
        if inserted:
            post.body = new_body
            post.word_count = len(new_body.split())
        else:
            log.warning("market_analysis.table_not_inserted",
                        post_id_pre=post.title[:40])

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
            "historical_context": historical_context,
            "featured_trade_block": featured_trade_block,
            "recent_trades_table": recent_trades_table,
            "account_block": account_block,
            "angle": angle,
            "focus": focus,
            "window_days": window_days,
            "template_id": template_id,
            "target_words": target_words,
        },
        source_urls=[],
        symbols=[],
    )

    # 9b. Render the AAPL yearly chart and append it to the body.
    # Failure is non-fatal — the article persists without a chart.
    try:
        from ..chart import render_yearly_chart
        chart_url_path = await render_yearly_chart("AAPL")
        if chart_url_path:
            chart_md = (
                "\n\n![AAPL yearly chart](" + chart_url_path + ")\n\n"
                + "[Download the chart](" + chart_url_path + ")\n"
            )
            post.body = post.body.rstrip() + chart_md
            await session.execute(sql_text(
                "UPDATE trading_posts SET body = :body, "
                "word_count = :wc WHERE id = :pid"
            ), {
                "body": post.body,
                "wc": len(post.body.split()),
                "pid": post_id,
            })
            log.info("market_analysis.chart_attached",
                     post_id=post_id[:8], url=chart_url_path)
    except Exception as exc:
        log.warning("market_analysis.chart_failed",
                    error=str(exc)[:200])

    # 10. Mark the featured trade as used and log the run.
    if featured_trade:
        try:
            await mark_trade_used(
                session,
                trade_id=featured_trade["id"],
                post_id=post_id,
            )
        except Exception as exc:
            log.warning("market_analysis.mark_trade_failed",
                        error=str(exc)[:200])
    try:
        await log_run(
            session,
            org_id=org_id,
            trade_id=featured_trade["id"] if featured_trade else None,
            window_days=window_days,
            angle=angle,
            focus=focus,
            template_id=template_id,
            post_id=post_id,
        )
    except Exception as exc:
        log.warning("market_analysis.log_run_failed",
                    error=str(exc)[:200])

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
