"""fundamental_metrics: F3 Structural Explainer.

The subblock reads the trader's `fundamental_metrics` section (a
text column on trader_process), picks the next unused FM coordinate,
and writes a structural explainer for that coordinate using the
trader's section as the source material.

No-wrap: after all 8 FM coordinates are used for this process
version, the subblock returns {status: exhausted}.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from ..editorial import compose_system_prompt
from ..fundamental_metrics import get_fundamental_metrics_section
from ..registry import pick_fm_coordinate
from ..registers._selector import pick_register
from .base import call_llm_json_verified, persist_post

log = get_logger(__name__)


PROMPT_TEMPLATE = """Form: F3 — Structural Explainer.

Coordinate: {coordinate}

You are teaching how a fundamental condition behaves and how it
interacts with systematic strategies. This is evergreen — not
tied to today's moment.

Here is the trader's fundamental-metrics section, in their own
words. This is the only source of facts. Quote from it where it
strengthens the piece. Every number in the piece must come from
here.

--- BEGIN TRADER'S SECTION ---
{trader_section}
--- END TRADER'S SECTION ---

SIGNAL FACTS (from the trader's process, authoritative):
{signal_directions}

SIGNAL BACKTEST AND BASELINE (authoritative — read before the
backtest beat; cite these values exactly, do not invent or omit):

{facts_block}

State every fact above: the direction (bullish or bearish), the
rule, the frequency, the size rule, the exit rule, the
point-in-time, and the holding period.

THE TRADE. This article is about one specific trade, cited below.
State its date, direction, entry, stop, exit, contracts, and the
result in dollars and in R. Every one of those numbers is given;
quote them, do not compute them. If the block says "(no
signal-linked trade available yet)", say the signal has not yet
produced a trade — do not invent one.

{trade_block}

DO NOT OPEN LIKE THESE. The first sentences of the most recent
articles in this subblock are listed below. Your opening must be
different — different first clause, different subject, different
verb.

{openings_block}

Also state the EXIT-STRUCTURE BASELINE if present: on N random
entries under these rules, the target was hit X% of the time, the
stop Y%, and the time stop Z%, for an expectancy of W R per trade.

State the sample too: n, wins, losses, the average R on wins and
losses, and the net R. Compare the expectancy to the baseline and
say plainly whether the signal beats it.

PARAGRAPHS. Every paragraph must be at least three sentences.
Short bursts read as notes, not prose.

If SIGNAL_BACKTEST is absent, say that no backtest exists for this
signal — do not borrow a result from another signal.

Do not invent a hit rate. Every number you state here must come
from the baseline or signal_backtest blocks.

Also state the EXIT-STRUCTURE BASELINE if present, quoting its
numbers VERBATIM. Do NOT compute a breakeven yourself — the block
carries `breakeven_hit_rate_pct`, `avg_win_r`, `avg_loss_r`,
`target_hit_rate_pct` and `expectancy_r`. Quote them.

State: on N random entries, the target was hit X%; the breakeven at
this payoff is Y% (from the block); the baseline is Z points
above/below breakeven; expectancy is W R per trade. If the hit rate
is below breakeven, say the exit structure is structurally
negative and the signal cannot rescue it. State that the signal's
own hit rate is not yet measured. Do not invent a signal hit rate.
Do not compute a breakeven that differs from the block. Where the composite is
"veto", say this signal blocks entry when it fires; where it is
"warning", say it reduces size but does not block entry. Do not
imply a direction by describing price action; say the word. Do not
add a fact that is not listed above.

VOICE. First person for what the trader does. Impersonal for
how the condition behaves. Never diagnose the reader. No 'you
can check,' 'you should,' 'your portfolio.'

WORD BUDGET — 800 words MINIMUM. Hard floor: an article under 820
words is rejected. Reach it by developing these beats, not by
padding:
  - Mechanism: 150-200 words on how the condition behaves.
  - Diagnostics: 4-6 observations, 70-100 words each, each drawn
    from the trader's own process.
  - Fixes: 4-5 before/after changes, 70-100 words each.
  - Reframe: one sentence, but develop the paragraph around it.
Do not repeat a point to add length. If short, add depth to the
mechanism and the diagnostics, not summary sentences.

FIXES ARE BEFORE/AFTER. Every fix begins with 'I used to' or
'I now.' Never 'Reduce,' 'Cap,' 'Shift,' 'Monitor,' 'Adjust.'

BEAT SHAPE — seven beats, in order:

  1. Hook — first person. The trader's own view on the
     condition. 'I used to treat X as Y. Then I saw...'
  2. Problem — one sentence. What the trader missed.
  3. Mechanism — impersonal. State the effect of the rule ONLY as
     the SIGNAL FACTS above state it. If the facts say "a draw
     greater than 5 million barrels supports a bullish bias," that
     is the whole mechanism. Do NOT explain why a draw is bullish.
     Do NOT claim it tightens supply, contracts available crude,
     prompts contract rolls, hedges, buying pressure, or a
     self-reinforcing loop. Those are causal claims the source does
     not make. If the fact row gives no mechanism, write one
     sentence stating the rule and move on.
  4. Diagnostics — first person. 4-6 lived observations from
     the trader's own process. Each one states the reading, the
     trader's threshold, and what it means.
  5. Fixes — 4-5 before/after changes in prose.
  6. Reframe — impersonal.
  7. End — end on the reframe. No checklist, no CTA, no URL.

CHART. Where the candle chart belongs, write the tag
[CHART: daily] on its own line. It is replaced with a daily
candlestick chart of CL. Place it where a visual of price action
supports the mechanism, usually after beat 4.

Continuous prose. No headings, no bold labels, no bullets,
no numbered lists. Do not print beat names. Every paragraph is at
least THREE sentences — a two-sentence paragraph is under-developed
and will be rejected.
"""


async def _recent_openings(session, org_id: str, subblock_id: str,
                           limit: int = 5) -> list[str]:
    """The first sentence of the most recent articles for this
    subblock. The prompt tells the model not to begin like these."""
    rows = (await session.execute(sql_text("""
        SELECT body FROM trading_posts
        WHERE org_id = :o AND subblock_id = :s AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT :n
    """), {"o": org_id, "s": subblock_id, "n": limit})).mappings().all()
    out: list[str] = []
    for r in rows:
        body = (r["body"] or "").strip()
        if not body:
            continue
        first = body.split("\n\n", 1)[0].strip().split(". ")[0].strip()
        if first:
            out.append(first[:160])
    return out


async def _pick_unused_signal_trade(
    session, org_id: str, signal: str,
) -> dict | None:
    """A trade from this signal that has not yet been the subject
    of an article. Each article is about a different trade."""
    row = (await session.execute(sql_text("""
        SELECT id, symbol, instrument, direction, traded_at, setup,
               entry, stop, exit, exit_reason, contracts, result,
               atr_at_entry, signal
        FROM trade_log
        WHERE org_id = :o AND deleted_at IS NULL
          AND signal = :sig
          AND used_in_article_id IS NULL
        ORDER BY traded_at LIMIT 1
    """), {"o": org_id, "sig": signal})).mappings().first()
    return dict(row) if row else None


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:
    # 1. Read the trader's fundamental-metrics section.
    # The split columns are authoritative. fundamental_metrics is a
    # legacy paragraph that duplicates them and once carried the
    # level-based contango veto; reading it alongside the split
    # columns let the model write from the stale text.
    _cols = (await session.execute(sql_text("""
        SELECT fundamental_opec_cut, fundamental_api_draw,
               fundamental_spread
        FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 1
    """), {"oid": org_id})).mappings().first()
    if _cols:
        trader_section = "\n\n".join(
            s for s in (
                (_cols["fundamental_opec_cut"] or "").strip(),
                (_cols["fundamental_api_draw"] or "").strip(),
                (_cols["fundamental_spread"] or "").strip(),
            ) if s
        )
    else:
        trader_section = await get_fundamental_metrics_section(session, org_id)
    if not trader_section:
        log.warning("fundamental_metrics.no_section", org_id=org_id[:8])
        return {"status": "no_section", "slug": subblock["slug"]}

    # 2. Pick the next FM coordinate (no-wrap).
    coord = await pick_fm_coordinate(
        session, org_id=org_id, subblock_id=subblock["id"],
    )
    if coord is None:
        log.info("fundamental_metrics.exhausted", org_id=org_id[:8])
        return {"status": "exhausted", "slug": subblock["slug"]}

    # 3. Pick the register (market-regime via SUBBLOCK_TIERS).
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

    # Structured signal directions: the article must state the
    # direction it treats each signal as, and the validator compares
    # to this table.
    # Per-state directions. If this coordinate is a spread state
    # (FM7a-FM7d), only THAT state is loaded — the article is about
    # one path, with one direction. Loading every state would let the
    # article hold two contradictory theses, which is exactly the
    # defect that produced "widening backwardation erodes the bullish
    # bias".
    from ..registry import FM_COORDINATE_STATE
    from ..fundamental_state_drafter import load_state_facts
    _coord_state = FM_COORDINATE_STATE.get(coord["coord"])
    # Conceptual coordinates (FM_SPREAD, FM1-FM8) carry a one-
    # paragraph trader section and no backtest/baseline. The 650
    # floor is unreachable without padding for those, so they
    # target a shorter piece. Spread states keep the full floor.
    if _coord_state is None:
        target_words = min(target_words, 500)
    _facts = None
    if _coord_state:
        _sig, _state = _coord_state
        _facts = await load_state_facts(session, org_id, _sig, _state)
        _rows = (await session.execute(sql_text("""
            SELECT signal, state, direction, description
            FROM fundamental_signal_states
            WHERE org_id = :oid AND signal = :sig AND state = :st
              AND deleted_at IS NULL
        """), {"oid": org_id, "sig": _sig, "st": _state})).mappings().all()
    else:
        # A conceptual coordinate (FM1-FM6, FM8) is not about a
        # signal state. Load nothing: the article cannot name a state
        # it was never given, and requiring it to would flag every
        # sentence that happens to contain "draw" or "cut".
        _rows = []

    _states = [dict(r) for r in _rows]
    if _facts:
        _dir_lines = "\n".join([
            f"  signal/state: {_facts['signal']}/{_facts['state']}",
            f"  direction: {_facts['direction']}",
            f"  rule: {_facts['rule_definition']}",
            f"  frequency: {_facts['frequency']}",
            f"  size rule: {_facts['size_rule']}",
            f"  exit rule: {_facts['exit_rule']}",
            f"  point-in-time: {_facts['point_in_time']}",
            f"  holding period: {_facts['holding_period']}",
            f"  composite: {_facts['composite_rule']} — "
            + ("this signal blocks entry when it fires"
               if _facts['composite_rule'] == 'veto'
               else "this signal reduces size; it does not block entry"),
        ])
    elif _coord_state and _states:
        _st0 = _states[0]
        _dir_lines = (
            f"  - {_st0['signal']} / {_st0['state']} is "
            f"{_st0['direction']}: {_st0.get('description') or ''}"
        )
    else:
        _dir_lines = "\n".join(
            f"  - {r['signal']} / {r['state']} is {r['direction']}"
            for r in _states
        ) or "  (none on file)"

    # The prompt references SIGNAL_BACKTEST, EXIT-STRUCTURE
    # BASELINE, and most_recent by name. None of those were in
    # _dir_lines, so the model was told to cite a trade it was
    # never shown. Build the block here and pass it to both the
    # prompt and the verifier payload.
    _facts_block_parts: list[str] = []
    if _facts:
        _sb = _facts.get("signal_backtest")
        if _sb:
            _facts_block_parts.append(
                "SIGNAL_BACKTEST:\n"
                f"  n: {_sb.get('n')}\n"
                f"  wins: {_sb.get('wins')}\n"
                f"  losses: {_sb.get('losses')}\n"
                f"  avg_win_r: {_sb.get('avg_win_r')}\n"
                f"  avg_loss_r: {_sb.get('avg_loss_r')}\n"
                f"  expectancy_r: {_sb.get('expectancy_r')}\n"
                f"  net_r: {_sb.get('net_r')}"
            )
            # The trade the article is about is _trade (the picked
            # unused trade), not most_recent. The trade block below
            # carries it. facts_block carries the signal summary only.
        else:
            _facts_block_parts.append(
                "SIGNAL_BACKTEST: absent (no backtest for this signal)"
            )
        _base = _facts.get("baseline")
        if _base and _base.get("available"):
            _facts_block_parts.append(
                "EXIT-STRUCTURE BASELINE (present):\n"
                f"  n: {_base.get('n')}\n"
                f"  target_hit_rate_pct: {_base.get('target_hit_rate_pct')}\n"
                f"  expectancy_r: {_base.get('expectancy_r')}\n"
                f"  first_bar: {_base.get('first_bar')}\n"
                f"  last_bar: {_base.get('last_bar')}"
            )
        else:
            _facts_block_parts.append(
                "EXIT-STRUCTURE BASELINE: not available for this state"
            )
        _esb = _facts.get("exit_structure_baseline")
        if _esb:
            _facts_block_parts.append(
                "EXIT_STRUCTURE_BASELINE (raw):\n"
                + repr(_esb)
            )
    _facts_block = "\n\n".join(_facts_block_parts) if _facts_block_parts else ""

    # A specific trade, and the openings of recent articles. Both
    # are what keep two articles on the same coordinate distinct.
    _openings = await _recent_openings(session, org_id, subblock["id"])
    _trade = None
    if _coord_state:
        _sig, _st = _coord_state
        _trade = await _pick_unused_signal_trade(
            session, org_id, f"{_sig}/{_st}" if _st else _sig,
        )

    from ..signal_math import trade_math
    if _trade:
        _tm = trade_math(dict(_trade), 1_000_000.0, 0.015)
        _trade_block = "\n".join([
            f"  date: {_trade.get('traded_at')}",
            f"  symbol: {_trade.get('symbol')}",
            f"  direction: {_trade.get('direction')}",
            f"  entry: {_tm['entry']}",
            f"  stop: {_tm['stop']}",
            f"  exit: {_tm['exit']}",
            f"  exit_reason: {_trade.get('exit_reason') or 'n/a'}",
            f"  contracts: {_tm['contracts']}",
            f"  stop_distance_points: {_tm['stop_distance_points']}",
            f"  stop_atr_multiple: {_tm['stop_atr_multiple']}",
            f"  planned_risk_dollars: {_tm['planned_risk_dollars']}",
            f"  realized_pnl_dollars: {_tm['realized_pnl_dollars']}",
            f"  r_multiple: {_tm['r_multiple']}",
            f"  percent_of_equity: {_tm['percent_of_equity']}",
        ])
    else:
        _trade_block = "  (no signal-linked trade available yet)"

    _openings_block = "\n".join(f"  - {o}" for o in _openings) \
                      or "  (none yet)"

    # The validator takes state-qualified directions: "signal/state".
    _directions = {
        f"{r['signal']}/{r['state']}": r["direction"] for r in _states
    }
    _signal_words = {
        "spread/backwardation_widening": "backwardation widening",
        "spread/backwardation_narrowing": "backwardation narrowing",
        "spread/contango_widening": "contango widening",
        "spread/contango_narrowing": "contango narrowing",
        "api_draw/above_5mb": "draw",
        "opec_cut/above_0_5mbpd": "cut",
    }

    user_prompt = PROMPT_TEMPLATE.format(
        coordinate=coord["label"],
        trader_section=trader_section,
        signal_directions=_dir_lines,
        facts_block=_facts_block,
        trade_block=_trade_block,
        openings_block=_openings_block,
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
        prompt_name="trading.fundamental_metrics",
        source_payload={
            "form": "F3",
            "register": register,
            "tier": tier,
            "coordinate": coord["coord"],
            "coordinate_label": coord["label"],
            "trader_section": trader_section,
            "signal_directions": _dir_lines,
            "facts_block": _facts_block,
        },
        target_words=target_words,
        form=None,
        tone=None,
        voice=None,
        skip_verify=False,
        exclude_standard={"LENGTH", "CTA_RULE", "PRE_RETURN_CHECKS"},
    )

    # ── Validation ──
    from packages.trading.fundamental_metrics_validator import (
        validate_fundamental_metrics,
    )
    # The validator's citation check reads
    # facts["signal_backtest"]["most_recent"]. The article is about
    # _trade, so point that key at _trade for the check. Shallow
    # copy — the real _facts is untouched.
    _facts_for_check = dict(_facts) if _facts else {}
    if _trade:
        _sb = dict(_facts_for_check.get("signal_backtest") or {})
        _sb["most_recent"] = {
            "d": str(_trade.get("traded_at") or ""),
            "entry": _trade.get("entry"),
            "stop": _trade.get("stop"),
            "exit": _trade.get("exit"),
            "exit_reason": _trade.get("exit_reason"),
        }
        _facts_for_check["signal_backtest"] = _sb
    _reasons = validate_fundamental_metrics(
        post.body, trader_section,
        directions=_directions, signals=_signal_words,
        facts=_facts_for_check,
        is_conceptual=(_coord_state is None),
    )
    if _reasons:
        log.warning("fundamental_metrics.validator_rejected",
                    reasons=_reasons)
        post.verification_ok = False
        post.unsupported_claims = list(
            getattr(post, "unsupported_claims", None) or []
        ) + _reasons

    # ── Candle chart (CL daily) ──
    # The trader's process is CL throughout (entry_logic,
    # position_sizing, exit_logic), and every FM coordinate is a
    # crude-market concept, so the chart shows CL daily candles.
    # No trade markers: this is a conceptual essay, not a trade
    # note. Failure never blocks the article.
    try:
        from packages.trading.charts.article_charts import (
            render_trade_candlestick,
        )
        from packages.db.session import get_session_factory as _gsf
        from packages.db.tenant import set_current_org as _set_org

        _sym = "CL"
        # The chart shows the trade the article is about (the
        # picked unused trade), so the image and the prose
        # describe the same entry, stop, and exit.
        async with _gsf()() as _chart_s:
            await _set_org(_chart_s, org_id)
            _candle_url = await render_trade_candlestick(
                _chart_s,
                org_id=org_id,
                symbol=_sym,
                timeframe="1d",
                entry=(float(_trade["entry"]) if _trade else None),
                stop=(float(_trade["stop"]) if _trade else None),
                exit_price=(float(_trade["exit"]) if _trade else None),
                entry_time=(_trade["traded_at"] if _trade else None),
            )
        if _candle_url:
            import re as _re
            _img = f"\n\n![{_sym} 1d candles]({_candle_url})\n\n"
            _new, _n = _re.subn(
                r"\[CHART:\s*daily\s*\]", _img, post.body,
                flags=_re.IGNORECASE,
            )
            if _n:
                post.body = _new
            else:
                post.body = post.body.rstrip() + _img
                log.warning("fundamental_metrics.chart_tag_missing",
                            symbol=_sym)
            log.info("fundamental_metrics.candle_inserted",
                     symbol=_sym, url=_candle_url, replaced=_n)
    except Exception as _ce:
        log.warning("fundamental_metrics.candle_failed",
                    error=str(_ce)[:200])

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={
            "form": "F3",
            "register": register,
            "tier": tier,
            "coordinate": coord["coord"],
            "coordinate_label": coord["label"],
            "trader_section": trader_section,
            "signal_directions": _dir_lines,
        },
        source_urls=[],
        symbols=["CL"],
    )
    await session.commit()

    # Mark the trade used so the next article on this coordinate
    # picks a different one. Without this the unused-trade query
    # returns the same row forever and two articles cite one trade.
    if _trade:
        await session.execute(sql_text("""
            UPDATE trade_log SET used_in_article_id = :p WHERE id = :t
        """), {"p": post_id, "t": _trade["id"]})
        await session.commit()

    log.info(
        "fundamental_metrics.persisted",
        post_id=post_id[:8],
        coord=coord["coord"],
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "coordinate": coord["coord"],
        "word_count": post.word_count,
    }
