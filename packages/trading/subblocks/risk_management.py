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

from ..ctas import pick_cta
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

The skeleton is the only source of facts. Everything you write
must come from it.

Write in the practitioner-risk register. First person for what
you did. Impersonal for the mechanism and the reframe. Never
"you." Speak to a peer — a systematic trader who knows the
terms.

Write in PARAGRAPHS. Each paragraph has 2-5 sentences on a
single idea. Do not write one sentence per line. The article
reads as continuous prose.

Dates in prose: "Wednesday, June 3, 2026" or "June 3, 2026."
Never "2026-06-03."

No headings. No bold labels. No beat names printed as titles.
No tables — except the trade verification table. No formula
notation — write every formula in words.

THE ARTICLE HAS ONE SUBJECT TRADE. The skeleton contains a
block titled "THE ARTICLE'S TRADE". Every beat except the
diagnostics and the verification table uses ONLY that trade's
facts.

The skeleton also contains a block titled "THE RELATED
TRADES". Use those trades in the diagnostics list and the
verification table only.

THE ELEVEN BEATS, in order:

1. HOOK — one paragraph. The trade from THE ARTICLE'S TRADE
   block. First person, past tense. Name every one of these:
     - date AND time of day, copied from the skeleton
       (e.g. "Friday, January 2, 2026 at 9:30 AM")
     - instrument
     - contracts
     - entry price
     - ATR at entry — write it as "the 5-day ATR was 32.0" or
       "ATR 32.0". ATR is a POINT value, not a dollar value.
       Never write "$32" or "ATR $32".
     - stop price
     - exit price
     - result in dollars and percent of equity

   After the dollar loss, state the percent loss as
   "a loss of X% of my $200,000 equity."

   THE STOP AND THE EXIT. Read the skeleton's stop and exit
   fields.
   - If the exit equals the stop (or is within 0.25 points),
     write "the stop was hit" and nothing more.
   - If the trade is a LOSS and the exit is on the wrong side of
     the stop (below the stop for a long, above the stop for a
     short), the market slipped past. Say so explicitly: "The
     price slipped through the stop and I was filled at <exit>,
     <gap> points past it."
   - Otherwise — a WIN with the exit above the stop (long) or
     below the stop (short) — just state the exit price. Do not
     call it a slip. A winner is not a slip.
   Do NOT attribute a stop slip to the ATR filter or any rule.
   Filters reject ENTRIES, not exits.

PERCENTS. The article may write only two kinds of percent:
   - the clearance percent from the setup string ("0.5% clearance")
   - the percent-of-equity loss for the subject trade
     ("a loss of 0.9% of my $200,000 equity")
   Do NOT write "X% of the entry price", "a X% move", "a X% stop
   distance", or any other percent. No derived percents. No
   percentages of the stop distance. No percentages of the ATR.

2. THE PROBLEM — one sentence. What failed about THE ARTICLE'S
   TRADE.

3. THE MECHANISM — one or two paragraphs. Impersonal. Why the
   failure happened. Teach the reader something they did not
   have a name for.

   Discuss ONLY THE RULE NAMED IN THE RULE VIOLATIONS BLOCK.
   Read that block.
   - If only the ATR filter is violated, the mechanism
     explains why an ATR above the threshold fails the trade.
     Do not discuss clearance, sizing, or stop placement.
   - If only clearance is violated, the mechanism explains
     why marginal clearance fails the trade. Do not discuss
     the ATR filter.
   - If the setup is a pull-back to a moving average, the
     clearance rule does NOT apply at all — a pull-back has
     no breakout to clear a high.
   The mechanism is about ONE rule. One paragraph is enough.
   Do not summarize the whole process.

4. THE DIAGNOSTICS — present the COMPLETE results of the related
   trades. One bullet per related trade. Format:

     - <date and time> | <N> contracts | entry <E> | stop <S> | exit <X> | <result string>

   The <date and time> is copied from that trade's block in the
   skeleton (e.g. "Monday, January 5, 2026 at 9:30 AM").

   The <result string> is copied VERBATIM from the related trade
   block in the skeleton. It is already formatted as "WIN of
   $X,XXX", "LOSS of $X,XXX", or "SCRATCH ($0)". Do not recompute
   it, do not prefix the word "loss", do not add or remove a
   minus sign. If the skeleton says "WIN of $3,000", the
   diagnostic item ends with "WIN of $3,000".

   THE STOP-EXIT GAP. For each related trade, compare its stop
   to its exit.
   - If the exit equals the stop (within 0.25 points), write
     the item as-is.
   - If the trade is a LOSS and the exit is on the wrong side of
     the stop (below for long, above for short), append
     " (slipped X pts)" where X is the gap.
   - Otherwise — a WIN, or an exit on the same side as the
     entry — write the item as-is. A winner is not a slip.
   Do not attribute a slip to a filter or rule.

   Example:
     - September 29, 2026 | 3 contracts | entry 20,345 | stop 20,323.7 | exit 20,430 | WIN of $5,100
     - October 2, 2026 | 4 contracts | entry 21,230 | stop 21,211.325 | exit 21,180 | LOSS of $4,000

   Every value comes from THE RELATED TRADES block. Do NOT
   write "I took a 3-contract long on..."

5. THE FIXES — four to five changes ABOUT THE ARTICLE'S TRADE.
   Continuous prose. Use only rules from the PROCESS RULES
   block. Each fix references THE ARTICLE'S TRADE. The article's
   trade date must appear in at least three of the fixes.
   Rotate openers. No two consecutive fixes start the same way.

   Do NOT write counterfactual claims. Do not write "would
   have capped", "would have limited", "would have prevented",
   "would have avoided", "would have kept me out", or any
   phrase saying what a rule would have done to the loss.
   State what you do now. The trade happened; the fix changes
   the future. A stop is a level, not a guarantee — do not
   claim it would have capped or limited anything.

6. THE REFRAME — one or two sentences. Standalone. Impersonal.
   It corrects a MISTAKEN EQUIVALENCE. "X is not what you
   think. X is what it actually is."

   THE REFRAME MUST BE ABOUT THE RULE NAMED IN THE RULE
   VIOLATIONS BLOCK. Read that block.
   - If the violation is the ATR filter, the reframe is about
     the ATR filter.
   - If the violation is clearance, the reframe is about
     clearance.
   - If the violation is sizing or stop, the reframe is about
     that rule.
   Do NOT write a clearance reframe if the violation is the
   ATR filter. The reader sees the mismatch immediately.

   Example for an ATR-filter violation:
     "The ATR filter isn't a speed limit on entries. It's a
      ceiling on the size of the loss the trade can take."

   Example for a clearance violation:
     "Clearance isn't a buffer against noise. It's the minimum
      distance the price must prove the breakout."

   Weak: "Clearance is the minimum distance above the 10-day
   high." (a definition, not a distinction)

7. THE CHECKLIST — five questions woven into one or two
   paragraphs. Every sentence ends with "?". Not a bulleted
   list.

8. TRADE VERIFICATION — one markdown table. All six trades from
   the batch. Columns:

     | Date | Contracts | Entry | Stop | Exit | Result |
     |------|-----------|-------|------|------|--------|

   ROW FORMAT:
   - Date: copied from the skeleton EXACTLY, including the
     weekday. If the skeleton says "Tuesday, January 6, 2026",
     write "Tuesday, January 6, 2026" — not "Thursday" and
     not "Jan 6, 2026". The weekday must match the date. Do
     NOT include the time in this table.
   - Contracts, Entry, Stop, Exit: numbers copied from the
     skeleton.
   - Result: signed dollar amount — "+$2,640" for a win,
     "-$1,800" for a loss. Do NOT write "WIN of" or "LOSS of"
     in this table.

   The subject trade is the FIRST row. The five related trades
   follow in chronological order.

   Example row:
     | Tuesday, February 24, 2026 | 3 | 21200.0 | 21176.0 | 21170.0 | -$1,800 |

9. PERFORMANCE CONTEXT — one sentence. Output this text
   verbatim, substituting {account_name} as written:

     "These trades are drawn from {account_name}. The full
      trade record, equity curve, and monthly performance are
      at https://signaliq.com/trading/performance"

   Do NOT generate a performance record. Do not invent trade
   counts, winner counts, loser counts, averages, largest wins,
   or largest losses. The record lives on the performance page.
   The article links to it.

   Do NOT change the wording. Do NOT write "in this article"
   — that phrase is forbidden by the register.

10. HOW THE PROCESS LOOKS OVER TIME — one or two paragraphs.
    What the trades reveal about the process. Do NOT refer to
    "the batch", "the six-trade set", "this article", "this
    piece", or "the sample". Write as if the reader is looking
    at a real trading record: use "the last six trades" or
    "these trades" or name a specific trade by date.

    Do NOT cite dollar averages, totals, or any performance
    statistic. The performance page carries those. Refer to
    specific trades by date and their individual results
    ($1,800 loss, $2,640 win) — those are the article's own
    trades. Do NOT summarize winners, losers, or averages.

    Open with a specific trade date, not with "Looking at". 
    Right: "The February 24 trade was the only one where the
            ATR filter was ignored."
    Wrong: "Looking at the last six trades, the wins all..."

11. THE CTA — one paragraph. Output this text verbatim:

      {cta_text}

MARKERS. Wrap three beats in HTML comments:

  <!-- REFRAME -->
  ... the reframe ...
  <!-- /REFRAME -->

  <!-- DIAGNOSTICS -->
  - item
  - item
  <!-- /DIAGNOSTICS -->

  <!-- CHECKLIST -->
  ... the checklist ...
  <!-- /CHECKLIST -->

LENGTH: 500-800 words.
"""


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
    cta_text = pick_cta(coord["dimension_keywords"], post_index=tier_index)

    # S.9.29 — pool-based picker. Count unused trades. If fewer
    # than 6 are available, generate 20 more on demand. The pool
    # is self-healing; the subblock never returns no_trades_available
    # while the LLM can still produce trades.
    from packages.trading.article_trades import (
        count_unused_trades, pick_trades, mark_trades_used,
    )

    unused = await count_unused_trades(session, org_id)
    if unused < 6:
        log.info("risk_management.pool_low", unused=unused)
        try:
            from packages.trading.synthetic_trades import (
                generate_synthetic_trades,
            )
            await generate_synthetic_trades(
                session, org_id=org_id, count=20, force=True,
            )
        except Exception as exc:
            log.warning("risk_management.batch_gen_failed",
                        error=str(exc)[:200])
            return {"status": "no_trades_available", "slug": slug}

    pool = await pick_trades(session, org_id, n=6)
    if not pool or len(pool) < 4:
        log.info("risk_management.queue_empty", org_id=org_id[:8])
        return {"status": "no_trades_available", "slug": slug}

    # Subject is the first LOSING trade (post-mortem register).
    losers = [t for t in pool if (t.get("result") or 0) < 0]
    if not losers:
        return {"status": "no_trades_available", "slug": slug}
    # Prefer a loser that VIOLATES a rule — the register is a
    # post-mortem, and a violation gives the article a concrete
    # problem to diagnose.
    from packages.trading.article_trades import (
        build_skeleton, read_process, load_performance_record,
        _equity_from_summary, _rule_violations,
    )
    process = await read_process(session, org_id)
    violating = [
        t for t in losers if _rule_violations(t, process)
    ]
    subject = violating[0] if violating else losers[0]
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
        cta_text=cta_text,
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
            "cta_text": cta_text,
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
            _vm = _re.search(
                r"RULE VIOLATIONS.*?(?=PROCESS RULES)",
                skeleton, _re.DOTALL,
            )
            _violations_text = _vm.group(0) if _vm else ""
            reasons = validate(
                post.body, analysis_block, _trades, _equity,
                performance=_perf,
                violations_text=_violations_text,
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
            "cta_text": cta_text,
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
