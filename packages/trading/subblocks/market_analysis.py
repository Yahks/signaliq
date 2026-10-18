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
    _recent_combinations,
)
from .market_analysis_rotation_stock import pick_stock_and_timeframe
from .market_analysis_pattern import detect_pattern
from .market_analysis_quality import validate_block, log_rejection, verify_article
from ..scrapers.market_history import build_stock_report

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


def _format_pattern(pattern: dict) -> str:
    """Render a detected pattern's stats as readable text for the block."""
    def _pct(x):
        return f"{x * 100:.1f}%"

    def _dollars(x):
        return f"${x:,.0f}"

    n_total = pattern["n_in"] + pattern["n_out"]
    return (
        f"Across the last {n_total} trades:\n"
        f"  - {pattern['n_in']} trades matching the pattern. "
        f"Win rate {_pct(pattern['wr_in'])}. "
        f"Average loss {_dollars(pattern['al_in'])}.\n"
        f"  - {pattern['n_out']} trades not matching the pattern. "
        f"Win rate {_pct(pattern['wr_out'])}. "
        f"Average loss {_dollars(pattern['al_out'])}.\n"
        f"  - Win-rate gap: {pattern['gap'] * 100:.1f} pp."
    )


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

    recent_combos = await _recent_combinations(session, org_id=org_id)
    window_days, angle, focus = pick_combination(recent_combos)

    # 2b. Stock + timeframe rotation. The pipeline picks a
    # (stock, timeframe) pair. The article is about that pair.
    stock_pick = await pick_stock_and_timeframe(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
    )
    if stock_pick is None:
        log.warning("market_analysis.no_stock_pick",
                    org_id=org_id[:8])
        return {"status": "no_stock_pick", "slug": subblock["slug"]}
    picked_symbol, picked_timeframe, finding_type, finding_section = stock_pick

    # Commit the fetched timeframes so the next queries (stock
    # report, trade generator) see them. Without this, a
    # first-fetch symbol can produce an empty trade series
    # because the trades query runs before the market_history
    # commit propagates to the session's connection.
    await session.commit()

    _TIMEFRAME_LABEL = {
        "1y":  "10-year view",
        "1mo": "5-year monthly view",
        "1wk": "1-year weekly view",
        "1d":  "60-day daily view",
        "1h":  "recent intraday view",
    }

    # 2c. Build the stock report — the model's primary source.
    stock_report = await build_stock_report(
        session,
        org_id=org_id,
        symbol=picked_symbol,
        timeframe=picked_timeframe,
    )
    if not stock_report:
        log.warning(
            "market_analysis.no_stock_report",
            symbol=picked_symbol,
            timeframe=picked_timeframe,
        )
        return {"status": "no_stock_report", "slug": subblock["slug"]}
    # Trade block: trades must match the article's instrument
    # and chart window. Generate them from the stock's own
    # market_history series before reading them back.
    from ..scrapers.trade_generator import ensure_trades_for
    await ensure_trades_for(
        session,
        org_id=org_id,
        symbol=picked_symbol,
        timeframe=picked_timeframe,
        count=5,
    )
    # Detect the pattern in the trader's own history first. If a
    # pattern exists, its featured trade is the article's subject.
    # Otherwise fall back to the next unused trade.
    pattern = await detect_pattern(
        session, org_id=org_id, symbol=picked_symbol,
    )
    if pattern:
        featured_trade = pattern["featured_trade"]
    else:
        featured_trade = await pick_unused_trade(
            session,
            org_id=org_id,
            symbol=picked_symbol,
            timeframe=picked_timeframe,
        )
        pattern = None

    if featured_trade is None:
        log.warning(
            "market_analysis.no_unused_trade",
            symbol=picked_symbol,
            timeframe=picked_timeframe,
        )
        await log_rejection(
            session,
            org_id=org_id,
            symbol=picked_symbol,
            timeframe=picked_timeframe,
            stage="block_validation",
            reason="no_unused_trade",
        )
        return {
            "status": "no_unused_trade",
            "slug": subblock["slug"],
            "symbol": picked_symbol,
            "timeframe": picked_timeframe,
        }

    # Layer 1 — validate block before the LLM call. If any field
    # is missing, the model would have to invent it. Reject now.
    block_errors = validate_block(featured_trade)
    if block_errors:
        await log_rejection(
            session,
            org_id=org_id,
            symbol=picked_symbol,
            timeframe=picked_timeframe,
            stage="block_validation",
            reason=block_errors[0],
            detail=",".join(block_errors),
            block_snapshot=featured_trade,
        )
        return {
            "status": "block_invalid",
            "slug": subblock["slug"],
            "symbol": picked_symbol,
            "timeframe": picked_timeframe,
            "errors": block_errors,
        }
    recent_trades = await get_recent_trades(
        session,
        org_id=org_id,
        limit=5,
        symbol=picked_symbol,
        timeframe=picked_timeframe,
    )

    # 3. The market's own report — last 24 hours of events.
    events_summary = await summarize_market_events(session, org_id=org_id)

    # 4. Historical context, over the picked window.
    historical_context = await summarize_historical_context(
        session, org_id=org_id, window_days=window_days,
        symbol=picked_symbol,
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

    # Featured trade — FULL DETAILED RECORD.
    #
    # The reviewer wants the article to read like a moment-by-moment
    # trading log, not a summary. Every number the reader needs to
    # see the trade must be in this block, formatted as data, and
    # the article's prose must render each one.
    #
    # Do NOT drop a field. If a value is None on this trade, skip
    # that line — never invent a value.
    featured_parts = []
    if featured_trade:
        t = featured_trade

        # Date and time of the fill. The article body must show both.
        # Prefer entry_time (real intraday timestamp) over
        # traded_at (date-only midnight). Fall back through the
        # chain if entry_time is missing.
        entry_time = t.get("entry_time")
        traded_at = t.get("traded_at")

        def _fmt_dt(dt):
            if dt is None:
                return ("", "")
            try:
                return (dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M UTC"))
            except Exception:
                return (str(dt)[:10], "")

        if entry_time is not None:
            trade_date, trade_time = _fmt_dt(entry_time)
        elif traded_at is not None:
            trade_date, trade_time = _fmt_dt(traded_at)
        else:
            trade_date, trade_time = ("", "")

        # Contract count. The reviewer explicitly wants size.
        contracts = t.get("contracts")
        contracts_str = (
            f"{contracts} contract" + ("s" if contracts and contracts != 1 else "")
            if contracts is not None else None
        )

        # Stop in ATR multiples. ATR lives in atr_at_entry.
        stop_val = t.get("stop")
        entry_val = t.get("entry")
        atr_val = t.get("atr_at_entry")
        stop_atr_multiple = None
        if stop_val is not None and entry_val is not None and atr_val:
            try:
                stop_atr_multiple = abs(float(stop_val) - float(entry_val)) / float(atr_val)
            except (TypeError, ValueError):
                stop_atr_multiple = None

        # Dollar P&L: result × contracts × point value. We do not
        # know the point value in this module, so emit the raw
        # `result` and the `contracts` and let the article say
        # "X points × N contracts" without a dollar conversion.
        # `percent_of_equity` is the dollar-impact number.
        result_val = t.get("result")
        pct_equity = t.get("percent_of_equity")
        if pct_equity is None and result_val is not None:
            # Compute from the trader's starting equity. The
            # account block below shows $200,000 as the starting
            # balance. Compute % of equity from that constant.
            try:
                pct_equity = (float(result_val) / 200000.0) * 100.0
            except (TypeError, ValueError):
                pct_equity = None

        featured_parts.append("--- THE TRADE I WANT TO TALK ABOUT ---")
        featured_parts.append(
            f"Traded at: {trade_date} {trade_time}".strip()
        )
        # Exit time — the reviewer's log needs both entry and exit
        # times. Format matches the entry.
        _exit_dt = t.get("exit_time")
        if _exit_dt is not None:
            _ex_d, _ex_t = _fmt_dt(_exit_dt)
            featured_parts.append(
                f"Exited at: {_ex_d} {_ex_t}".strip()
            )
        featured_parts.append(
            f"Instrument: {t.get('instrument')}"
        )
        featured_parts.append(
            f"Direction: {t.get('direction')}"
        )
        if contracts_str:
            featured_parts.append(f"Size: {contracts_str}")
        if t.get("setup"):
            featured_parts.append(f"Setup: {t['setup']}")
        featured_parts.append(f"Entry: {_fmt(entry_val)}")
        if stop_val is not None:
            stop_line = f"Stop: {_fmt(stop_val)}"
            if stop_atr_multiple is not None:
                stop_line += f" ({stop_atr_multiple:.2f} × ATR)"
            featured_parts.append(stop_line)
        if atr_val is not None:
            featured_parts.append(f"ATR at entry: {_fmt(atr_val)}")
        if t.get("target") is not None:
            featured_parts.append(f"Target: {_fmt(t['target'])}")
        if t.get("exit") is not None:
            featured_parts.append(f"Exit: {_fmt(t['exit'])}")
        if t.get("exit_reason"):
            featured_parts.append(
                f"Exit reason: {t['exit_reason']}"
            )
        if result_val is not None:
            featured_parts.append(
                f"Result: {_fmt(result_val)}"
            )
        if pct_equity is not None:
            featured_parts.append(
                f"Percent of equity: {_fmt(pct_equity)}%"
            )
        if t.get("hold_time"):
            featured_parts.append(
                f"Hold time: {t['hold_time']}"
            )
        if t.get("lesson"):
            featured_parts.append(
                f"Lesson I wrote at the time: {t['lesson']}"
            )
        # Price action — real OHLC bars from market_history.
        # Each line is one bar between entry and exit. The
        # template's THE MOMENT and THE HESITATION beats read
        # from this section.
        _sd = t.get("setup_detail")
        if _sd:
            featured_parts.append("")
            featured_parts.append(_sd)
    featured_trade_block = (
        "\n".join(featured_parts) if featured_parts else ""
    )

    # The pattern and behavioral note are added to source_block
    # separately, above the trade block. Not here.

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
    # Account block: the trader's overall book. Not scoped
    # to the picked stock - it shows the account as it stands.
    try:
        account_data = await summarize_account_equity(
            session, org_id=org_id,
        )
        account_block = format_account_block(account_data)
    except Exception as _acc:
        log.warning("market_analysis.account_failed",
                    error=str(_acc)[:200])
        account_block = ""

    # 6. Build the source block.
    # Two-source block:
    #   1. The trader's dashboard — what they track and see.
    #   2. The market's own report — the last 24 hours.
    #   3. Historical context over the picked window.
    #   4. The stock report — a specific stock with its chart and stats.
    # The trader's process columns only. The stale CL
    # readings are not this article's dashboard; the picked
    # stock's own readings are in the stock report.
    conditions_block = format_conditions_block(
        mc, research_block=None, include_readings=False,
    )
    # Assemble the source block. Order matters: the model reads
    # top-to-bottom and weights what it sees first. The pattern is
    # the subject, so it comes first. The trade is the proof, so
    # it comes second. The dashboard, events, historical context,
    # and stock report are context, so they come after.
    _source_parts = []
    if pattern:
        _source_parts.append(
            "--- THE PATTERN IN MY HISTORY ---\n"
            + _format_pattern(pattern)
            + "\n\n--- THE BEHAVIORAL MOMENT ---\n"
            + (pattern.get("behavioral_note") or "")
        )
    if featured_trade_block:
        _source_parts.append(featured_trade_block)
    _source_parts.append(
        "--- MY READINGS AND PROCESS ---\n" + conditions_block
    )
    source_block = "\n\n".join(_source_parts)
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
    source_block += (
        "\n\n--- THE STOCK REPORT ---\n"
        + stock_report
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
    # Count prior posts for this subblock to rotate templates.
    _cnt_row = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM trading_posts
        WHERE subblock_id = :sid AND org_id = :oid
          AND deleted_at IS NULL
    """), {"sid": subblock["id"], "oid": org_id})).first()
    article_count_for_template = int(_cnt_row[0] or 0) if _cnt_row else 0
    template, template_id = pick_template(article_count_for_template)
    timeframe_label = _TIMEFRAME_LABEL.get(
        picked_timeframe, picked_timeframe,
    )
    user_prompt = template.format(
        symbol=picked_symbol,
        timeframe_label=timeframe_label,
        finding_type=finding_type,
        finding_section=finding_section,
        source_block=source_block,
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
            "stock_report": stock_report,
            "symbol": picked_symbol,
            "timeframe": picked_timeframe,
        },
        target_words=target_words,
        form=None,
        tone=None,
        voice=None,
        skip_verify=False,
        skip_standard=True,
        temperature=0.3,
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

    # Layer 2 — verify the article before persisting. Every check
    # that failed (times, prices, equity, emotion, invented
    # numbers, meta-commentary) rejects the article. The
    # rejection is logged to market_analysis_rejections with a
    # named reason. The article does not enter trading_posts.
    article_errors = verify_article(post.body, featured_trade)
    if article_errors:
        await log_rejection(
            session,
            org_id=org_id,
            symbol=picked_symbol,
            timeframe=picked_timeframe,
            stage="article_verification",
            reason=article_errors[0],
            detail=",".join(article_errors),
            block_snapshot=featured_trade,
            article_body=post.body[:8000],
        )
        return {
            "status": "article_rejected",
            "slug": subblock["slug"],
            "symbol": picked_symbol,
            "timeframe": picked_timeframe,
            "errors": article_errors,
        }

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
            "stock_report": stock_report,
            "symbol": picked_symbol,
            "timeframe": picked_timeframe,
        },
        source_urls=[],
        symbols=[],
    )

    # 9b. Render the AAPL yearly chart and append it to the body.
    # Failure is non-fatal — the article persists without a chart.
        # Render the price chart and the four supporting charts.
    # Each is optional; failures are logged but never raised.
    try:
        from ..chart import render_stock_chart
        from ..charts.article_charts import (
            render_distribution_chart,
            render_setup_history_chart,
            render_timeframe_returns_chart,
            render_correlation_chart,
        )

        chart_urls: list[tuple[str, str]] = []

        price_url = await render_stock_chart(
            session,
            org_id=org_id,
            symbol=picked_symbol,
            timeframe=picked_timeframe,
        )
        if price_url:
            chart_urls.append((f"{picked_symbol} {timeframe_label}", price_url))

        dist_url = await render_distribution_chart(
            session, org_id=org_id, symbol=picked_symbol,
        )
        if dist_url:
            chart_urls.append((f"{picked_symbol} 90-day move distribution", dist_url))

        setup_url = await render_setup_history_chart(
            session, org_id=org_id, symbol=picked_symbol,
        )
        if setup_url:
            chart_urls.append((f"{picked_symbol} prior setup instances", setup_url))

        tf_url = await render_timeframe_returns_chart(
            session, org_id=org_id, symbol=picked_symbol,
        )
        if tf_url:
            chart_urls.append((f"{picked_symbol} returns across windows", tf_url))

        corr_url = await render_correlation_chart(
            session, org_id=org_id, symbol=picked_symbol,
        )
        if corr_url:
            chart_urls.append((f"{picked_symbol} 20-day rolling correlation", corr_url))

        if chart_urls:
            chart_lines = ["\n\n## Charts\n"]
            for label, url in chart_urls:
                chart_lines.append(f"### {label}\n![{label}]({url})\n")
            post.body = post.body.rstrip() + "\n\n".join(chart_lines)
            await session.execute(sql_text(
                "UPDATE trading_posts SET body = :body, "
                "word_count = :wc WHERE id = :pid"
            ), {
                "body": post.body,
                "wc": len(post.body.split()),
                "pid": post_id,
            })
            log.info(
                "market_analysis.charts_attached",
                post_id=post_id[:8], count=len(chart_urls),
            )
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
            symbol=picked_symbol,
            timeframe=picked_timeframe,
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
