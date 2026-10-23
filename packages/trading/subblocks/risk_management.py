"""risk_management: the risk cap and why.

Reads the trade log. Picks the next unused trade (FIFO). Builds
a skeleton with the trade's real facts, its rule violations, and
the process rules the article may cite. Hands the skeleton to
the model. The model writes prose. The validator checks.

Three layers:
  1. Pre-computation (article_trades.py). Python picks the
     trade and computes the skeleton. The model quotes.
  2. Validator. After generation, the body is checked against
     the skeleton and the trade. Fabrications reject.
  3. No rewrite. A rejected article lands as a draft with the
     reasons visible. A human fixes.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from ..editorial import compose_system_prompt
from ..registry import pick_coordinate, covered_dimensions
from ..registers._selector import pick_register
from .base import call_llm_json_verified, persist_post

log = get_logger(__name__)


PROMPT_TEMPLATE = """Coordinate: {dimension_label} / {angle_label}
Framing: {angle_framing}

--- ARTICLE SKELETON ---
{skeleton}
--- END ARTICLE SKELETON ---

The skeleton is the only source of facts. Every number you
write must appear in it.

REGISTER: practitioner-risk. First person for what you did.
Impersonal for the mechanism and the reframe. Never second
person.

FORMAT: continuous prose, paragraphs of 2-5 sentences. No
headings, no bold labels, no beat names printed as titles.
No bullets. The ONLY table is the verification table.
Dates in prose: "Friday, October 9, 2026 at 2:30 PM."
Never ISO format.

THE FRAME. The skeleton's FRAME field tells you what the
article is about. There are three frames:

  FRAME = RISK. The trade risked the wrong dollar amount
    against the risk budget. The skeleton's RISK ASSESSMENT
    block has all the numbers: budget, actual_risk, deviation,
    deviation_pct, direction (over/under). The article is about
    the risk. Do not discuss entry rules.

  FRAME = ENTRY. The trade violated an entry rule (clearance,
    ATR filter, stop placement). The skeleton's PRIMARY
    VIOLATION field has the rule text. The article is about
    that rule. Do not discuss the risk budget.

  FRAME = NONE. The trade followed every rule. The article is
    about why a rules-compliant trade still produced an
    unremarkable result.

THE NINE BEATS, in order:

1. HOOK. One paragraph. First person, past tense. Copy from
   THE ARTICLE'S TRADE block:
     - date and time ("Friday, October 9, 2026 at 2:30 PM")
     - instrument
     - unit count (use "shares" for stocks, "contracts" for
       futures)
     - entry price
     - ATR at entry (write "the 5-day ATR was 0.268", not "$0.268")
     - stop price
     - exit price
     - result in dollars
     - percent of equity (copy from percent_of_equity field)

2. PROBLEM. One or two sentences. Names the PRIMARY VIOLATION.
   If FRAME is NONE, say the trade followed the rules.

   In BOTH cases, ALSO name which constraint bound the
   position size. The RISK ASSESSMENT block has:
     - binding_constraint: "risk" OR "leverage_cap"
     - n_risk_based: shares the risk formula alone would allow
     - n_leverage_based: shares the 1x equity cap alone would allow
     - traded_size: shares actually traded
   Say it plainly:
     - If binding_constraint is "risk": "The risk budget bound
       the size (N shares). The leverage cap was slack — the
       position used only X% of the account's notional capacity."
     - If binding_constraint is "leverage_cap": "The 1x leverage
       cap bound the size (N shares). The risk formula would
       have allowed more, but the account cannot safely hold
       that notional."

   ALSO state the R-multiple from RISK ASSESSMENT.r_multiple
   (e.g. "a -1R loss" or "a +1.63R win"). A stop-out is -1R.

3. MECHANISM. One paragraph. Why the framed rule matters.
   Use the rule's logic, not a generic statement.
   Frame-specific:
     - RISK: why dollars-at-risk matter more than stop
       distance. The budget is 1.5% of equity (~$3,000 on
       $200,000); the tolerance band is 1.0%-2.0% of equity
       (~$2,000 to ~$4,000). A trade that risks under the
       floor cannot move the account; over the ceiling it
       risks ruin.
     - ENTRY: why the specific entry rule (clearance, ATR
       filter, stop placement) matters.
     - NONE: why rules filter bad trades but do not
       guarantee good ones.

4. DIAGNOSTICS. Copy the VERBATIM DIAGNOSTICS BLOCK from the
   skeleton exactly. Every row, every number, every date. Do
   NOT invent a trade. Do NOT change a number. Do NOT reformat
   a date.

5. FIXES. Four to five changes, continuous prose, in one
   paragraph or two. Each references THE ARTICLE'S TRADE.

   HARD RULE: Every number you write in the fixes must be a
   number that appears in the PROCESS RULES block or the RISK
   ASSESSMENT block. Do NOT invent a threshold. Do NOT say
   "0.9x equity" if PROCESS RULES says "1.0x equity". Do NOT
   say "50% deviation" if no deviation tolerance is in
   PROCESS RULES. Do NOT say "ATR below 0.25" if PROCESS
   RULES says "ATR <= 30". The rules are the only source.

   If you want to describe an adjustment, use the rule as
   stated: "I now enforce the 1.5% risk budget on every
   entry." Do NOT add a tolerance or a cap that is not
   written.

   SLIPPAGE. If the RISK ASSESSMENT block has
   total_slippage_dollars and it is not zero, name it in one
   of the fixes. Say "slippage cost me $X on this trade" or
   "the total slip was $X — more than half the planned risk."
   The stop distance is not the whole risk. Slippage is the
   gap between planned and realized.

   BATCH PERFORMANCE. The skeleton has a BATCH PERFORMANCE
   block. In ONE sentence, quote two or three numbers from
   it VERBATIM. The block has: win_rate, expectancy_per_trade,
   profit_factor, net_r_sum, net_r_mean, avg_win_r,
   avg_loss_r, net_pnl_dollars, current_equity.

   HARD RULE: copy the numbers exactly. Do NOT round. Do
   NOT invent. Do NOT paraphrase. If the block says
   `win_rate: 20.0%` and `expectancy_per_trade: -$508.57`,
   write "the batch win rate was 20.0% with expectancy
   -$508.57 per trade". If it says `net_pnl_dollars:
   -$2,542.87`, do not write "+$391.53".

   WRONG (fabricated — do not do this):
     "my win rate was 40.0% with expectancy +$78.31
      and net P&L +$391.53"
     — but the block says win_rate: 20.0%,
       expectancy: -$508.57, net_pnl: -$2,542.87.

   RIGHT (verbatim):
     "the batch win rate was 20.0% with expectancy
      -$508.57 per trade and net P&L -$2,542.87"

   If the BATCH PERFORMANCE block shows negative expectancy
   or a negative net P&L, SAY SO. Do NOT flip the sign. Do
   NOT invent a positive outcome. A rules-compliant system
   can still lose money — name the loss, do not hide it.

   BREAKEVEN MATH. The BATCH PERFORMANCE block has a
   BREAKEVEN MATH section: payoff_ratio, breakeven_win_rate,
   actual_win_rate, edge_gap_pct. You MUST state these in
   the fixes.

   Write a sentence like: "The batch needs a 75.5% win rate
   to break even at a 0.33 payoff ratio; it has 40.0% — a
   35.5-point edge gap."

   This is the single most important line in the article.
   It converts "edge emerges across hundreds of iterations"
   from a hope into a computed gap. If the edge gap is
   negative, name the loss. If positive, name the surplus.
   Do NOT omit this sentence. Do NOT paraphrase the
   numbers. Do NOT round.

   Vary sentence openers. Do NOT write "would have prevented"
   or "would have capped" — state what you do now.

6. REFRAME. One sentence. Standalone. Impersonal. Matches the
   frame:
     - RISK: "Stop distance isn't the risk. Risk is stop
       distance times size."
     - ENTRY: a distinction about the entry rule.
     - NONE: distinguish single-trade risk from portfolio
       risk — rules bound the former, not the latter.
   Do NOT repeat the mechanism. Do NOT use a fixed "X isn't Y,
   it's Z" formula unless it genuinely fits. Wrap in
   <!-- REFRAME --> markers.

7. VERIFICATION TABLE. The Python-built table appears in the
   article after the reframe. Do not write your own table —
   it will be stripped and replaced. The table will have the
   format:
     | Date | Direction | Contracts | Entry | Stop | Exit | Result |
   The subject trade is row 1. Five related trades follow in
   chronological order.

8. END. End the article with the verification table. Nothing
   after the table. No CTA, no marketing line, no URL.

LENGTH: 500-800 words.
"""




def _strip_all_tables(body: str) -> str:
    """Remove every markdown table from the body.

    The model writes its own table from the skeleton. It gets
    the math wrong. We discard it and insert the Python-computed
    table instead.
    """
    if not body:
        return body
    lines = body.splitlines()
    out: list[str] = []
    in_table = False
    for ln in lines:
        s = ln.strip()
        # A markdown table starts with a line beginning `|` and
        # has a header separator on the next line. We treat any
        # contiguous run of `| ... |` lines as one table.
        if s.startswith("|") and s.endswith("|"):
            in_table = True
            continue
        if in_table and not s:
            # Blank line after a table ends it.
            in_table = False
            out.append("")
            continue
        if not in_table:
            out.append(ln)
    # Collapse extra blank lines.
    joined = "\n".join(out)
    while "\n\n\n" in joined:
        joined = joined.replace("\n\n\n", "\n\n")
    return joined.strip()


def _insert_table_before_cta(body: str, table: str) -> str:
    """Insert the verification table into the article body.

    The table goes before the final CTA paragraph. The CTA is
    the paragraph that mentions the product URL or contains
    "email me". Everything before it is the body; insert the
    table at that point.
    """
    if not table or not body:
        return body
    paras = body.split("\n\n")
    # Find the CTA paragraph — the last paragraph that mentions
    # the URL or "email me".
    cta_idx = None
    for i in range(len(paras) - 1, -1, -1):
        p_text = paras[i]
        if ("signaliq.com" in p_text.lower()
                or "email me" in p_text.lower()
                or "see exactly how" in p_text.lower()
                or "compare our data" in p_text.lower()
                or "full technical writeup" in p_text.lower()):
            cta_idx = i
            break
    if cta_idx is None:
        # No CTA found. Insert at the end.
        paras.append(table)
    else:
        paras.insert(cta_idx, table)
    return "\n\n".join(paras).strip()

async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    slug = subblock["slug"]

    tier_index = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM trading_posts
        WHERE subblock_id = :sid AND deleted_at IS NULL
    """), {"sid": subblock["id"]})).scalar() or 0

    from ..registers import pick_tier, target_words_for
    tier = pick_tier(tier_index)

    covered = await covered_dimensions(session, org_id)
    if not covered:
        log.warning("risk_management.no_process_covered", slug=slug)
        return {"status": "no_process", "slug": slug}

    coord = await pick_coordinate(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        subblock_slug=slug,
        covered_dimensions=covered,
    )

    # S.9.29 — pool-based picker. Count unused trades. If fewer
    # than 6 are available, generate 20 more on demand. The pool
    # is self-healing; the subblock never returns no_trades_available
    # while the LLM can still produce trades.
    from packages.trading.article_trades import (
        count_unused_trades, pick_trades, mark_trades_used,
    )

    unused = await count_unused_trades(session, org_id)
    if unused < 6:
        # The strategy-driven generator (risk_management_trades.py)
        # supplies trades per-symbol. If a single symbol has fewer
        # than 6 unused trades, the pipeline below retries with a
        # different symbol. There is NO synthetic fallback — the
        # old path called a broken LLM that mixed NQ futures with
        # a CL process document, producing invalid trades.
        log.info("risk_management.pool_low", unused=unused)
        # Fall through; pick_stock_and_timeframe will try other symbols.

    # ── Subject-first selection with per-symbol coherence ──
    #
    # The article must be about ONE instrument. The subject is a
    # trade for that instrument; the diagnostics are 5 more
    # trades of the SAME instrument. If the subject's symbol has
    # fewer than 6 unused trades, regenerate trades for that
    # symbol. If regeneration fails, try the next candidate.
    from packages.trading.article_trades import (
        build_skeleton, read_process, load_performance_record,
        _equity_from_summary, _rule_violations, pick_trades as _pick_trades,
    )

    process = await read_process(session, org_id, instrument_type="stock")

    # Load the structured strategy from trader_instrument_process.
    # Every rule the trade generator applies comes from here.
    _strategy_row = (await session.execute(sql_text("""
        SELECT strategy_json
        FROM trader_instrument_process
        WHERE org_id = :oid AND instrument_type = 'stock'
          AND deleted_at IS NULL LIMIT 1
    """), {"oid": org_id})).first()
    strategy = dict(_strategy_row[0]) if _strategy_row and _strategy_row[0] else {}

    # Get a first batch of candidates (any symbol) so we can find
    # a coherent-violation trade with a symbol that has enough
    # trades to fill the article.
    # ── Symbol pick → ensure trades → subject selection ──
    # Ported from market_analysis. The flow:
    #   1. pick_stock_and_timeframe → symbol + timeframe.
    #   2. ensure_trades_for(10) → generate 10 trades from that
    #      symbol's price bars.
    #   3. Pick the subject (coherent violation first) and 5
    #      siblings from that symbol's trades.
    # Guarantees 6 rows when the symbol generates 10+ trades
    # (the normal case). Retries up to 3 symbols if a stock is
    # thin or its history is invalid.
    from packages.trading.article_trades import (
        build_skeleton, read_process, load_performance_record,
        _equity_from_summary, _rule_violations, pick_trades as _pick_trades,
    )
    # risk_management has its own trade generator and stock picker.
    # No imports from market_analysis_rotation_stock or trade_generator.
    from .risk_management_trades import (
        ensure_trades_for,
        pick_stock_and_timeframe,
    )

    process = await read_process(session, org_id, instrument_type="stock")

    # Load the structured strategy from trader_instrument_process.
    # Every rule the trade generator applies comes from here.
    _strategy_row = (await session.execute(sql_text("""
        SELECT strategy_json
        FROM trader_instrument_process
        WHERE org_id = :oid AND instrument_type = 'stock'
          AND deleted_at IS NULL LIMIT 1
    """), {"oid": org_id})).first()
    strategy = dict(_strategy_row[0]) if _strategy_row and _strategy_row[0] else {}

    _MAX_STOCK_RETRIES = 3
    subject = None
    pool: list[dict] = []

    for _attempt in range(_MAX_STOCK_RETRIES):
        stock_pick = await pick_stock_and_timeframe(
            session,
            org_id=org_id,
            subblock_id=subblock["id"],
        )
        if stock_pick is None:
            log.warning("risk_management.no_stock_pick",
                        org_id=org_id[:8])
            return {"status": "no_stock_pick", "slug": slug}
        # risk_management's picker returns (symbol, timeframe).
        # No finding_type / section — those were market_analysis
        # concepts that came in with the port and are dead here.
        _sym, _tf = stock_pick

        await session.commit()

        await ensure_trades_for(
            session, org_id=org_id,
            symbol=_sym, timeframe=_tf, count=10,
            strategy=strategy,
        )

        _sym_trades = await _pick_trades(
            session, org_id, n=10, symbol=_sym,
        )
        if len(_sym_trades) < 4:
            log.warning(
                "risk_management.retry_thin_symbol",
                symbol=_sym, timeframe=_tf,
                available=len(_sym_trades),
                attempt=_attempt + 1,
            )
            continue

        # Categorize trades by outcome for a mixed pool.
        _violators = [
            t for t in _sym_trades
            if _rule_violations(t, process, strategy=strategy)
        ]
        _winners = [
            t for t in _sym_trades
            if (t.get("result") or 0) > 0 and t not in _violators
        ]
        _losers = [
            t for t in _sym_trades
            if (t.get("result") or 0) < 0 and t not in _violators
        ]
        _scratches = [
            t for t in _sym_trades
            if (t.get("result") or 0) == 0 and t not in _violators
        ]

        # Subject = first violator (the article is about a violation).
        # If no violator exists, fall back to first trade.
        if _violators:
            subject = _violators[0]
        elif _sym_trades:
            subject = _sym_trades[0]
        else:
            subject = None

        # Related pool: mix of outcomes so the diagnostics aren't
        # all losses. Take up to 2 winners, 2 losers, 1 scratch,
        # then fill from anything remaining.
        _related: list = []
        for t in _winners[:2]:
            if t["id"] != subject["id"]:
                _related.append(t)
        for t in _losers[:2]:
            if t["id"] != subject["id"]:
                _related.append(t)
        for t in _scratches[:1]:
            if t["id"] != subject["id"]:
                _related.append(t)
        # Fill up to 5 if above didn't reach it
        for t in _sym_trades:
            if len(_related) >= 5:
                break
            if t["id"] != subject["id"] and t not in _related:
                _related.append(t)

        pool = [subject] + _related[:5]
        break

    if subject is None or len(pool) < 4:
        log.warning("risk_management.no_coherent_symbol",
                    org_id=org_id[:8])
        return {"status": "no_trades_available", "slug": slug}

    related = [t for t in pool if t["id"] != subject["id"]]
    subject_id = subject["id"]
    trade_ids = [t["id"] for t in pool]

    # S.9.29b — build the skeleton the prompt quotes from.
    equity = _equity_from_summary(process.get("trade_history_summary", ""))
    _dates = [t["traded_at"] for t in pool
              if t.get("traded_at") is not None]
    anchor = max(_dates) if _dates else None
    performance = await load_performance_record(
        session, org_id, anchor=anchor, days=90,
    )
    skeleton = build_skeleton(
        subject, process,
        account_equity=equity,
        related=related,
        performance=performance,
        strategy=strategy,
    )

    topic_word = _compute_topic_word_from_trade(subject)

    # Account name — user-set, shown in the performance sentence.
    # Defensive: if the column is missing or the row is absent
    # (unprovisioned org), fall back to a generic name rather
    # than crash the article.
    account_name = "my demo account"
    try:
        _account_row = (await session.execute(sql_text("""
            SELECT account_name FROM trader_process
            WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
        """), {"oid": org_id})).first()
        if _account_row and _account_row[0]:
            account_name = _account_row[0]
    except Exception as _ae:
        log.warning("risk_management.account_name_query_failed",
                    error=str(_ae)[:120])

    user_prompt = PROMPT_TEMPLATE.format(
        dimension_label=coord["dimension_label"],
        angle_label=coord["angle_label"],
        angle_framing=coord["angle_framing"],
        skeleton=skeleton,
        topic_word=topic_word,
        account_name=account_name,
    )

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
            "You write trading process pieces.",
            form=None, tone=None, slug=slug,
            register=register, tier=None,
        ),
        user=user_prompt,
        prompt_name="trading.risk_management",
        source_payload={
            "register": register,
            "tier": tier,
            "coordinate": coord["coord"],
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "angle_framing": coord["angle_framing"],
            "skeleton": skeleton,
            "trade_id": subject["id"],
        },
        target_words=target_words,
        form=None, tone=None, voice=None,
        skip_verify=True,
        use_cache=False,
    )

    # ── Post-generation validation ────────────────────────────────
    if getattr(post, "verification_ok", True):
        try:
            from packages.trading.article_validator import validate

            # The validator receives the full batch — the subject
            # plus the five related trades. Its checks look up
            # trades by date, so it needs every trade the article
            # can cite.
            _trades = [subject] + (related or [])

            # Account equity — from the process document.
            _equity: float | None = None
            try:
                _eq_row = (await session.execute(sql_text("""
                    SELECT trade_history_summary
                    FROM trader_process
                    WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
                """), {"oid": org_id})).first()
                if _eq_row and _eq_row[0]:
                    import re as _re
                    _m = _re.search(
                        r"account_equity\s*:\s*([\d,.]+)",
                        _eq_row[0],
                    )
                    if _m:
                        _equity = float(_m.group(1).replace(",", ""))
            except Exception as _eqe:
                log.warning(
                    "risk_management.equity_parse_failed",
                    error=str(_eqe)[:120],
                )

            # The analysis block for this one trade.
            from packages.trading.trade_rule_analysis import (
                get_rule_analysis,
            )
            analysis_block = await get_rule_analysis(
                session, org_id, limit=5,
            )

            from packages.trading.article_trades import (
                load_performance_record,
            )
            # Use the same window the skeleton used: trailing 90
            # days ending on the subject trade's date. Otherwise
            # the validator computes the record over all history
            # and rejects the skeleton's windowed averages as
            # fabricated.
            _anchor = subject.get("traded_at") if subject else None
            _perf = await load_performance_record(
                session, org_id, anchor=_anchor, days=90,
            )
            # Extract the RULE VIOLATIONS block from the skeleton
            # so the validator can check the reframe against the
            # rule the article is actually about.
            import re as _re
            # New skeleton format uses FRAME / PRIMARY / SECONDARY.
            # Old format used RULE VIOLATIONS. Match either, up to
            # the PROCESS RULES block.
            _vm = _re.search(
                r"(?:FRAME:|RULE VIOLATIONS).*?(?=PROCESS RULES)",
                skeleton, _re.DOTALL,
            )
            _violations_text = _vm.group(0) if _vm else ""

            # ── Strip the model's table, insert Python's table ──
            from packages.trading.article_trades import build_trade_table
            _computed_table = build_trade_table(_trades[:6])
            if _computed_table:
                post.body = _strip_all_tables(post.body)
                post.body = _insert_table_before_cta(
                    post.body, _computed_table,
                )

            reasons = validate(
                post.body, analysis_block, _trades, _equity,
                performance=_perf,
                violations_text=_violations_text,
                skeleton_text=skeleton,
            )
            if reasons:
                log.warning(
                    "article.validator_rejected",
                    slug=slug,
                    reasons=reasons,
                )
                post.verification_ok = False
                post.unsupported_claims = list(
                    post.unsupported_claims or []
                ) + [f"__validator__: {r}" for r in reasons]
        except Exception as _ve:
            import traceback as _tb
            log.error(
                "article.validator_error",
                slug=slug,
                error=str(_ve)[:200],
                tb=_tb.format_exc()[-1500:],
            )
            post.verification_ok = False
            post.unsupported_claims = list(
                post.unsupported_claims or []
            ) + [f"__validator_error__: {str(_ve)[:200]}"]

    # ── Render the subject trade's candlestick ──
    # One chart, for the subject trade's timeframe. The chart shows
    # OHLC candles with entry, stop, target, and exit overlaid.
    # Inserted as a markdown image right after the verification
    # table (or at the top if no table).
    try:
        from packages.trading.charts.article_charts import (
            render_trade_candlestick,
        )
        _candle_url = await render_trade_candlestick(
            session,
            org_id=org_id,
            symbol=subject.get("instrument") or subject.get("symbol") or "",
            timeframe=subject.get("timeframe") or "1d",
            entry=float(subject["entry"]) if subject.get("entry") is not None else None,
            stop=float(subject["stop"]) if subject.get("stop") is not None else None,
            target=float(subject["target"]) if subject.get("target") is not None else None,
            exit_price=float(subject["exit"]) if subject.get("exit") is not None else None,
            entry_time=subject.get("entry_time"),
            exit_time=subject.get("exit_time"),
        )
        if _candle_url:
            _candle_md = (
                f"\n\n![{subject.get('instrument') or 'trade'} "
                f"{subject.get('timeframe') or '1d'} candles]"
                f"({_candle_url})\n\n"
            )
            # Insert after the verification table if present,
            # otherwise append to the body before the CTA.
            if "| Date | Direction | Contracts |" in post.body:
                # Split at the table's end (blank line after last row).
                import re as _re
                _table_end = _re.search(
                    r"\n\s*\n", post.body[post.body.find("| Date | Direction | Contracts |"):]
                )
                if _table_end:
                    _insert_at = post.body.find("| Date | Direction | Contracts |") + _table_end.end()
                    post.body = (
                        post.body[:_insert_at]
                        + _candle_md
                        + post.body[_insert_at:]
                    )
                else:
                    post.body = post.body + _candle_md
            else:
                # No table. Insert after the first paragraph.
                _first_blank = post.body.find("\n\n")
                if _first_blank > 0:
                    post.body = (
                        post.body[:_first_blank]
                        + _candle_md
                        + post.body[_first_blank:]
                    )
                else:
                    post.body = post.body + _candle_md
            log.info("risk_management.candle_inserted",
                     url=_candle_url,
                     symbol=subject.get("instrument"),
                     timeframe=subject.get("timeframe"))
    except Exception as _ce:
        log.warning("risk_management.candle_failed",
                    error=str(_ce)[:200])

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "register": register,
            "tier": tier,
            "coordinate": coord["coord"],
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "angle_framing": coord["angle_framing"],
        },
        source_urls=[],
        symbols=[],
    )
    await session.commit()

    # Advance the queue — every trade in the batch is used.
    try:
        await mark_trades_used(session, org_id, trade_ids, post_id)
    except Exception as _me:
        log.warning(
            "risk_management.mark_trades_used_failed",
            error=str(_me)[:120],
        )

    log.info(
        "risk_management.persisted",
        post_id=post_id[:8],
        coord=coord["coord"],
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok",
        "slug": slug,
        "post_id": post_id,
        "coordinate": coord["coord"],
        "word_count": post.word_count,
    }


def _compute_topic_word_from_trade(trade: dict[str, Any]) -> str:
    """One noun that names the mechanism the article is about."""
    setup = (trade.get("setup") or "").lower()
    if "clearance" in setup or "breakout" in setup:
        return "clearance"
    if "stop" in setup:
        return "stop"
    if "atr" in setup:
        return "ATR"
    return "sizing"
