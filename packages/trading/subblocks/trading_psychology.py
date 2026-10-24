"""trading_psychology generator: a behavioural lesson for Trading Psychology."""

from __future__ import annotations

from sqlalchemy import text as sql_text

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

from .base import (
    TradingPost,
    call_llm_json_verified,
    persist_post,
)
from ..editorial import compose_system_prompt, get_org_voice
from ..forms import pick_form_and_tone
from ..registers._selector import pick_register
from ..registry import (
    SUBBLOCK_ANGLES,
    covered_dimensions,
    pick_coordinate,
)
from ..psychology_log import (
    read_all_for_coord,
    read_next_unused,
    mark_used,
)

log = get_logger(__name__)


MECHANISM_CLAIM = (
    "Discipline breaks down when recent outcomes diverge from the plan, "
    "and the response is to increase size or frequency to recover."
)




PROCESS_FIELDS_BY_DIMENSION = {
    "D8":  ("daily_scan", "chart_overlay", "position_sizing",
            "entry_logic", "holding_period"),
    "D13": ("emotion_removal", "exit_logic", "position_sizing",
            "holding_period", "trade_history_summary"),
}


PROMPT_TEMPLATE_LOG = """You are writing a trading-psychology essay about the discipline to not act.

LENGTH: Write 770–1200 words. MINIMUM 770 — an article under 770
words is rejected.

Reach 770 by developing these beats, not by padding:
  - MECHANISM: 150–200 words. The claim, then the four bias
    sentences, each naming what the paper measured.
  - DIAGNOSTICS: five bullets, 80–110 words each. Each states what
    you did, the rule it broke, and what it cost.
  - FIXES: 120–160 words of prose on the changes you made.
  - WHAT THIS MEANS: 100–140 words, including the reconciliation
    figures.
Do not pad the hook or the reframe. Length comes from MECHANISM,
DIAGNOSTICS, FIXES and WHAT-THIS-MEANS.

The reader is a trader. The article is about their state, not the market.
The source below is the trader's own process and their recent behaviour.
Read it. Write about the gap.

**Trader process (their own words):**

{process_block}

**Batch of six trades (1 CONTENT + 5 REFERENCE):**

{batch_block}

**Reconciliation across the full log (computed — quote exactly):**

{reconciliation_block}

{charts_block}

**Research block (the only sources you may cite):**

{research_block_text}

**The article must do this:**

1. HOOK — open on one thing from the trader's own process or behaviour.
   Not a summary. The moment it caught your attention. First person.
   The reader watches you read it.

2. PROBLEM — one sentence. The pull the trader feels — the one that
   makes them want to act, or to break the rule they wrote.

3. MECHANISM — the one claim the whole article rests on. Write
   FOUR to FIVE sentences. The shape is fixed:

     Sentence 1: restate the claim in your own words. NO paper
       name. NO citation. Just the claim.
     Sentences 2–5: cite the four biases. ONE bias per sentence.
       Each sentence names the paper AND what the paper
       SPECIFICALLY measured — not the general claim.

   The four biases you MUST cite, one per sentence:
     - Loss aversion — Kahneman and Tversky 1979
     - Mental accounting — Thaler 1980
     - The disposition effect — Odean 1998
     - Overtrading after losses — Barber and Odean 2000

   CORRECT shape:
     "The urge to re-enter after a loss is a documented reflex.
      Kahneman and Tversky 1979 showed that losses loom larger
      than equivalent gains, driving disproportionate action.
      Thaler 1980 showed that traders treat each loss as its
      own mental account. Odean 1998 showed that the disposition
      effect is strongest after a losing streak. Barber and
      Odean 2000 showed that investors trade more frequently
      after losses, trying to recover."
   WRONG shape:
     "Discipline breaks down when recent outcomes diverge from
      the plan, as Barber and Odean 2000 demonstrate."
     (One paper. Wrong claim. Not four biases.)

   The claim you must argue is:

       {mechanism_claim}

   FIVE sentences. Claim first, then the four biases. Each bias
   in its own sentence with what the paper measured. This is the
   longest beat in the article. Do NOT shorten it.

CONTENT TRADE vs REFERENCE TRADES:
The BATCH above contains six trades. ONE is marked CONTENT.
FIVE are marked REFERENCE.

- The HOOK describes the CONTENT trade. The hook's FIRST sentence
  MUST state FIVE things from the CONTENT trade, in this order:
    1. the date,
    2. the instrument's ticker in parentheses,
    3. the contracts count as a number word ("three contracts",
       "the four-contract MNQ short", "one contract"),
    4. the result (with sign),
    5. the hold time.
  Every value comes from the batch, exactly as written. Do NOT
  paraphrase the contracts count. Do NOT omit it.
  After the first sentence, write the CONTENT trade's id in
  parentheses: "(row id: <first 8 chars of the CONTENT id>)".
  Example shape: "On 2026-05-06 I shorted the euro future (6E)
  with one contract, held 25 minutes for a -$1,225 stop out
  (row id: 1cc5bcd0)."

- The DIAGNOSTICS has exactly FIVE bullets. Bullet 1 describes
  the CONTENT trade. Bullets 2–5 each describe ONE of the four
  earliest REFERENCE trades — not all five refs. Cite each
  reference trade by its date and instrument. Do NOT cite a
  reference trade's id.

- The MECHANISM cites the four papers. Not the trades.

- All COUNTS (win rate, consecutive losses, hold-time averages)
  come from all six rows. Do not invent a count.

- THE RECONCILIATION. The block above splits the full log into
  compliant and rule-violating trades. Your FIXES or WHAT-THIS-
  MEANS paragraph MUST state, as figures:
    (a) compliant: n, net dollars, average per trade;
    (b) violation: n, net dollars, average per trade;
    (c) the per-trade gap;
    (d) the violation rate as a percentage;
    (e) whether the violations were net positive or net negative,
        stated as computed. If violations were net positive, say
        the finding is the per-trade gap, not a loss.
  Quote the figures exactly. Do not recompute them from the six
  rows. Do not describe a violation as "costly" if its net is
  positive — say the gap instead.

  ALSO STATE:
    (f) how many violations were PROFITABLE and their net — a
        violation that wins is the most dangerous kind, because it
        reinforces the behaviour. Say it explicitly.
    (g) the sample period (sample_period in the block).
    (h) THE CHART TAG. The diagnostics bullet that describes the
        cited trade must END with the literal token [CHART:daily]
        on its own line. In that same bullet, state the entry, the
        stop, the exit, and what the price did between them — the
        chart shows exactly that. Write the token exactly as
        [CHART:daily], brackets and colon included. An article
        without it fails validation.

  MECHANISM -> FIX MAPPING. Each fix must name the mechanism it
  addresses, not just the rule it enforces:
    - holding losers (disposition, Odean 1998) -> locked stop
    - size creep after wins (house money) -> sizing cap
    - frequency after losses (Barber & Odean 2000) -> cooldown
    - skipped review -> rule-tagged journal
  Do not list four fixes without saying which impulse each one
  targets.

  CAUSAL LANGUAGE. Say the gap "is consistent with" rule adherence,
  not that it "demonstrates" or "proves" it. Instrument mix, size,
  and regime can confound the comparison. Do not claim causation
  from an unpaired sample.

- Do NOT invent a trade that is not one of the six rows above.

4. DIAGNOSTICS — ONE bulleted list. Exactly FIVE items. Each item
   starts with "- " and is ONE sentence. Each item describes a
   distinct lived failure: what you did, what rule it broke, what
   it cost. Five bullets, five failures.
   DO NOT start every bullet with "I". Vary the openings:
     - "The first time I ignored the sizing cap, the trade…"
     - "A short on the currency future was cut early, before…"
     - "One morning, after a loss, I re-entered before…"
     - "The stop was intact. I exited anyway."
     - "A winning position on the gold contract grew by two
        contracts before I noticed."
   After the fifth bullet, end the diagnostics. Then a blank
   line, then the FIXES beat. Do NOT add a URL, an audit link, or
   any sentence after the fifth bullet.
   This is the ONLY list in the piece. EXACTLY FIVE BULLETS.
   Bullet 1 describes the CONTENT trade (the followed_rule win).
   Bullets 2–5 each describe ONE different reference trade — the
   four earliest by date. Five bullets, five trades.
   The rule is context, if it appears at all.

5. FIXES — ONE paragraph. NOT a list. NOT "I used to X. Now I Y."
   written five times. Describe the changes in prose. The reader
   should feel the change in the writing. Vary the openings. Some
   shapes:
     - "The sizing cap is now the first thing I check."
     - "The cooldown after a loss is non-negotiable."
     - "The stop exits on its own. I do not intervene."
     - "Every win closes before I consider scaling."
   Four to five changes, in one paragraph, in the article's voice.
   Each fix is behavioural. Not a market rule.

6. WHAT THIS MEANS — one paragraph, three to five sentences.
   Interpret the pattern for the reader. Not a summary of the
   fixes. Not a restatement of the mechanism. A generalisation
   the reader can apply to their own trading. Start with the
   phrase "Here's what this means for a systematic trader
   generally."
   Then give three to five sentences of interpretation. Each
   sentence names a general principle you learned from the
   batch — not a rule, a principle. Example shape:
     "First, the tags matter more than the trades. The same
      four trades appeared under multiple labels; the pattern
      is in the overlap, not in any single loss.
      Second, the cooldown is the highest-leverage rule I have.
      It targets the recovery impulse directly. Every other
      rule addresses a symptom.
      Third, the fix is temporal, not technical. I cannot
      automate away the impulse, but I can insert a mandatory
      delay between the impulse and the action."
   Do NOT number the sentences as "First, Second, Third" unless
   it helps the reader. Do NOT repeat the diagnostics or the
   fixes. This is the analytical paragraph.

7. REFRAME — one sentence. Standalone. Impersonal. It must correct
   a mistaken equivalence: the reader believes X is Y; the article
   shows X is not Y, it is Z.
   CRITICAL: the reframe must be a distinction whose shape has
   NOT been used before. Forbidden openers:
     - "Discipline is not…" / "Discipline isn't…"
     - "The cooldown isn't…"
     - "The rule isn't…"
     - "The stop isn't…"
     - "X is not the absence of Y; it is Z"
     - "X isn't a [noun]. It's a [noun]."
   All of those shapes have appeared in previous articles. The
   reader will recognize them. Choose a shape that has not been
   used. Examples of new shapes:
     - "The rule doesn't end the impulse. It ends the click."
     - "A stop isn't the trade. It's the price of the next one."
     - "Discipline is what's left when the profit is gone."
     - "The gap between the impulse and the click is the whole
        game."
   NOT a restatement of the mechanism. NOT a platitude. Before
   the checklist.

8. CHECKLIST — five questions woven into prose, starting with
   'Can I' or 'Do I' or 'Have I'. Each question is about the trader's
   own state, not about the market.

9. CTA — one sentence. YOU write it. There is no verbatim string.

   The CTA must offer the reader a DISCIPLINE artifact — a specific
   thing they could receive, named in the article's own words. The
   artifact must fit THIS article's theme: if the article diagnosed
   post-loss re-entry, the artifact is a cooldown protocol; if it
   diagnosed size creep, the artifact is a sizing sheet; if it
   diagnosed a broken rule, the artifact is a rule-audit worksheet.
   Read your own article and name the artifact it earned.

   There is NO CTA. Do NOT offer an artifact, do NOT say
   "email me", and do NOT include a URL. End the article on the
   reframe.

   The artifact may be a worksheet, a checklist, a log, a sheet, a
   protocol, or a journal. Anything specific the reader can hold.
   Do NOT offer a generic thing (a guide, tips, more content).

   The article ends on the reframe. No CTA, no artifact offer,
   no URL, no "email me".

Rules:

- Every factual sentence about the trader must trace to the source
  above. Do not invent a trade, a price, a win rate, a rule, or a
  blotter. Quote the trader's own process back to them.
- The CONTENT trade (from the BATCH block, marked CONTENT) is the
  only trade you may describe WITH NUMBERS. Its date, instrument,
  contracts, result, hold time, and exit reason are the numbers
  you may cite exactly as written.
- In the FIRST sentence of the article, write the CONTENT trade's
  ticker in parentheses immediately after the prose name. Example:
  "the e-mini S&P (ES)", "the gold contract (GC)", "the Treasury
  futures (ZB)", "the micro Nasdaq (MNQ)", "the crude oil (CL)".
  The ticker must appear as a standalone token — ES, GC, ZB, MNQ,
  CL, or 6E. This is not a name-drop; it tells the reader exactly
  which contract the article is about. Every reader must see the
  ticker in the first sentence.
- Reference trades may be named by date and instrument in the
  diagnostics. Their numbers also come from the BATCH block, not
  from memory.
- The cited trade's instrument — from the LATEST TRADE block —
  MUST appear at least once, as a standalone token, in the body.
  Write the ticker exactly as it appears in the source: MNQ, ZB,
  ES, CL, GC, or 6E. This is not a name-drop; it is the subject
  of the article. Every reader must know which contract you are
  describing.
- EVERY OTHER instrument must NOT be name-dropped. If the source
  mentions NQ, ES, CL, GC, SPY, or any ticker other than the cited
  trade's, describe it in prose — "the index future", "the
  front-month contract", "the Treasury contract". This rule
  applies to every instrument EXCEPT the cited one.
- Every generalisation about behaviour must trace to the research
  block above. Name the source. The claim and its citation are the
  SAME sentence — not the next sentence, not a parenthetical on a
  following line. If you name the disposition effect, the same
  sentence names Odean 1998. If you name loss aversion, the same
  sentence names Kahneman and Tversky 1979. If you name mental
  accounting, the same sentence names Thaler 1980. One sentence,
  one claim, one source.
- Cite sources in the form: Author YEAR, or Author and Author YEAR.
  Do NOT write "Author & Author, YEAR" and do NOT put a comma
  between the author name and the year. Correct: "Kahneman and
  Tversky 1979", "Odean 1998", "Barber and Odean 2000".
- The article MUST be 600–900 words. Aim for 750. The publish
  floor is 600. If your draft is under 600, it is wrong — fix it
  before you return.
- The article's LENGTH must NOT depend on the cited trade. Whether
  the cited trade was a large win or a small loss, a long hold or
  a short one, the article is still 600–900 words. Length comes
  from MECHANISM, DIAGNOSTICS, and FIXES — from YOUR analysis of
  the trade, not from the trade's own size.
- Before returning, DO THIS:
    1. Count the words in your draft.
    2. If the count is under 770, DO NOT RETURN YET. Expand the
       MECHANISM and the FIXES only — add more depth to the
       causal chain, more detail to the changes you made. Do
       NOT add more diagnostics bullets — the count is fixed at
       FIVE. Do NOT add more citations than the four biases. Do
       NOT pad the HOOK or the CTA. Then count again.
    3. If the count is still under 770, repeat step 2.
    4. Only return when the count is 770 or more.
- Do NOT pad the HOOK or the REFRAME. Length comes from MECHANISM,
  DIAGNOSTICS, FIXES and WHAT-THIS-MEANS only.
- A draft under 770 words is a failed draft. Do not return it.
- The cited trade is the ONLY trade you may describe with
  numbers. Do NOT invent a result, a date, a contract count, or
  an instrument that is not in the LATEST TRADE block above.
  Every number you write about a trade must appear in the
  LATEST TRADE block, unchanged.
- The article MUST name the cited trade's instrument, contracts,
  result, and hold time, EACH at least once, inside the body.
  Quote them exactly as they appear in the LATEST TRADE block.
- THE CHART. A price chart of the cited trade's instrument is
  appended after the article. Refer to it once, in the diagnostics
  or the fixes: name the entry price, the stop, and the exit as
  they appear on the chart, and what the price did between them.
  Do not describe a chart you were not given; use only the entry,
  stop, exit and result from the LATEST TRADE block.

Voice: First person throughout. The reader is watching you read the
trader's process. Never address the reader as 'you'.

Return a TITLE line, then BODY:, then the body.
"""



async def fetch_trader_state(
    session: AsyncSession,
    *,
    org_id: str,
    coord: str,
) -> dict[str, Any]:
    """Read the trader's state from psychology_trade_log.

    Two reads:

    - read_all_for_coord — every live row for this coordinate, used
      and unused. The readings are computed from these.
    - read_next_unused  — the next unconsumed row. This is the trade
      the article cites. It is marked used after the article persists.

    The readings and the cited trade come from the SAME table, so the
    article's numbers and its cited trade tell one story.
    """
    all_rows = await read_all_for_coord(
        session, org_id=org_id, coord=coord,
    )

    cited = await read_next_unused(
        session, org_id=org_id, coord=coord,
    )

    if not cited:
        return {
            "process_block": "(no psychology log entries available)",
            "behaviour_block": "(no psychology log entries available)",
            "readings": {},
            "cooling_off_rule": None,
            "latest_trade": None,
            "cited_row_id": None,
        }

    # ── Readings from the full live pool ────────────────────────────
    results = [
        float(r["result"]) for r in all_rows
        if r.get("result") is not None
    ]
    wins = [x for x in results if x > 0]
    losses = [x for x in results if x < 0]

    def _mins(s: Any) -> float | None:
        if not s:
            return None
        s = str(s).strip().lower()
        try:
            if s.endswith("m"):
                return float(s[:-1])
            if s.endswith("h"):
                return float(s[:-1]) * 60
            return float(s)
        except ValueError:
            return None

    hw = [_mins(r.get("hold_time")) for r in all_rows
          if r.get("result") is not None and float(r["result"]) > 0]
    hl = [_mins(r.get("hold_time")) for r in all_rows
          if r.get("result") is not None and float(r["result"]) < 0]
    hw = [x for x in hw if x is not None]
    hl = [x for x in hl if x is not None]

    # Consecutive losses from the tail (newest first — sort by date).
    sorted_rows = sorted(
        all_rows,
        key=lambda r: str(r.get("traded_at") or ""),
        reverse=True,
    )
    consecutive_losses = 0
    for r in sorted_rows:
        if r.get("result") is None:
            continue
        if float(r["result"]) < 0:
            consecutive_losses += 1
        else:
            break

    reading_keys: dict[str, int] = {}
    for r in all_rows:
        k = str(r.get("reading_key") or "unknown")
        reading_keys[k] = reading_keys.get(k, 0) + 1

    # Compliance split across the full log — the reviewer's #1 gap:
    # incidents listed without a compliant-vs-violation comparison.
    from ..psychology_skeleton import _classify as _psy_classify

    _comp = [r for r in all_rows
             if _psy_classify(r.get("reading_key")) == "compliant"]
    _viol = [r for r in all_rows
             if _psy_classify(r.get("reading_key")) == "violation"]
    _viol_w = [r for r in _viol if float(r.get("result") or 0) > 0]
    _viol_l = [r for r in _viol if float(r.get("result") or 0) < 0]

    def _net(rows): return sum(float(r["result"] or 0) for r in rows)
    def _avg(rows): return (_net(rows) / len(rows)) if rows else 0.0

    readings = {
        "recent_win_rate": round(len(wins) / len(results), 3) if results else None,
        "consecutive_losses": consecutive_losses,
        "avg_hold_winners_min": round(sum(hw) / len(hw), 1) if hw else None,
        "avg_hold_losers_min": round(sum(hl) / len(hl), 1) if hl else None,
        "last_exit_reason": (
            sorted_rows[0].get("exit_reason") if sorted_rows else None
        ),
        "reading_key_counts": reading_keys,
        "account_trade_count": len(all_rows),
        "compliant_n": len(_comp),
        "compliant_net": round(_net(_comp), 2),
        "compliant_avg": round(_avg(_comp), 2),
        "violation_n": len(_viol),
        "violation_net": round(_net(_viol), 2),
        "violation_avg": round(_avg(_viol), 2),
        "per_trade_gap": round(_avg(_comp) - _avg(_viol), 2),
        "violation_rate_pct": (
            round(len(_viol) / (len(_comp) + len(_viol)) * 100.0, 1)
            if (_comp or _viol) else None
        ),
        "violation_wins_n": len(_viol_w),
        "violation_wins_net": round(_net(_viol_w), 2),
        "violation_losses_n": len(_viol_l),
        "violation_losses_net": round(_net(_viol_l), 2),
        "sample_start": str(all_rows[0].get("traded_at") or "")[:10]
        if all_rows else None,
        "sample_end": str(sorted_rows[0].get("traded_at") or "")[:10]
        if sorted_rows else None,
    }

    # ── Behaviour block: one line per row, pipe-separated ──────────
    # ── Structured batch block ─────────────────────────────────────
    # The cited row is the CONTENT trade. The rest are REFERENCE rows.
    # The model writes the article around the content trade, using the
    # references for the readings and the pattern counts.
    def _row_block(r: dict, label: str, *, content: bool) -> str:
        _rid = str(r.get("id") or "")
        _short_id = _rid[:8] if _rid else "?"
        _res = (
            f"{float(r['result']):+.2f}"
            if r.get("result") is not None else "open"
        )
        _entry = (
            f"{float(r['entry']):.2f}"
            if r.get("entry") is not None else "?"
        )
        _exit = (
            f"{float(r['exit']):.2f}"
            if r.get("exit") is not None else "?"
        )
        _lines = [f"{label}"]
        if content:
            _lines.append(f"  id:            {_short_id}")
        _lines += [
            f"  traded_at:     {str(r.get('traded_at') or '')[:10]}",
            f"  instrument:    {r.get('instrument') or '?'}",
            f"  direction:     {r.get('direction') or '?'}",
            f"  contracts:     {r.get('contracts') or '?'}",
            f"  entry:         {_entry}",
            f"  exit:          {_exit}",
            f"  result:        {_res}",
            f"  hold_time:     {r.get('hold_time') or '?'}",
            f"  exit_reason:   {r.get('exit_reason') or '?'}",
            f"  reading_key:   {r.get('reading_key') or '?'}",
        ]
        if content:
            _lines.append(
                f'  narrative:     "{(r.get("narrative") or "")}"'
            )
        return "\n".join(_lines)

    _cited_id = str(cited.get("id") or "")
    batch_parts: list[str] = []
    batch_parts.append(
        _row_block(cited, "*** CONTENT TRADE — cite this one ***",
                   content=True)
    )
    _ref_n = 0
    for r in sorted_rows:
        if str(r.get("id") or "") == _cited_id:
            continue
        _ref_n += 1
        if _ref_n > 5:
            break
        batch_parts.append("")
        batch_parts.append(
            _row_block(r, f"REF {_ref_n}", content=False)
        )
    behaviour_block = "\n".join(batch_parts)

    # ── Process block: the reading keys this batch carries ──────────
    process_lines: list[str] = [
        f"- reading key: {k} ({v} trade{'s' if v != 1 else ''})"
        for k, v in sorted(reading_keys.items(), key=lambda kv: -kv[1])
    ]
    process_block = (
        "\n".join(process_lines)
        if process_lines else "(no psychology log entries)"
    )

    # ── The cited trade ────────────────────────────────────────────
    latest = {
        "traded_at": str(cited.get("traded_at") or "")[:10],
        "instrument": (
            str(cited.get("instrument") or "").strip().strip("'\"")
            or None
        ),
        "direction": cited.get("direction"),
        "contracts": cited.get("contracts"),
        "entry": (
            float(cited["entry"]) if cited.get("entry") is not None else None
        ),
        "exit": (
            float(cited["exit"]) if cited.get("exit") is not None else None
        ),
        "stop": (
            float(cited["stop"]) if cited.get("stop") is not None else None
        ),
        "target": (
            float(cited["target"]) if cited.get("target") is not None else None
        ),
        "entry_time": cited.get("entry_time"),
        "exit_time": cited.get("exit_time"),
        "result": (
            float(cited["result"]) if cited.get("result") is not None else None
        ),
        "hold_time": cited.get("hold_time"),
        "exit_reason": cited.get("exit_reason"),
        "reading_key": cited.get("reading_key"),
        "narrative": cited.get("narrative"),
    }

    return {
        "process_block": process_block,
        "behaviour_block": behaviour_block,
        "readings": readings,
        "cooling_off_rule": None,
        "latest_trade": latest,
        "cited_row_id": str(cited.get("id")),
    }


# ─────────────────────────────────────────────────────────────────────
# Content verifier — runs after generation, before persist.
#
# A single LLM call that reads the article and the source rows and
# returns {"ok": bool, "problems": [...]}. The verifier checks five
# things at present:
#
#   1. The cited trade's numbers match the source row.
#   2. The reading keys from the batch appear in the diagnostics.
#   3. Every generalisation names a source from the research block.
#   4. Voice is first person — no "the trader", no "you".
#   5. The CTA names a discipline artifact and ends with the URL.
#   6. The cited trade's instrument appears in the body as a token.
#   7. The article is at least 600 words.
#
# If ok=False, the article drafts. The problems are appended to
# unsupported_claims so the reviewer sees them. No regen.
#
# The check list is a constant so new checks can be appended
# without touching the verifier body.
# ─────────────────────────────────────────────────────────────────────

_VERIFIER_CHECKS = (
    "CONTENT vs SOURCE: every number the article cites about the "
    "trade (date, instrument, contracts, result, hold time, exit "
    "reason) must match the LATEST TRADE block exactly. The "
    "diagnostics must reference at least N of the reading keys from "
    "the process block, where N = min(3, number of distinct reading "
    "keys available in the batch). If the batch has only 2 distinct "
    "keys, the article must reference both. If it has 5, the "
    "article must reference at least 3. Do NOT demand more keys than "
    "the batch actually contains. Every generalisation about "
    "behaviour must either name a real paper (author and year) or "
    "leave the named effect uncited. Reject invented authors or "
    "years.",

    "VOICE: the article must be first person throughout. Reject any "
    "occurrence of \"the trader\", \"the desk\", or second-person "
    "address (\"you\", \"your\") except inside a quoted source.",

    "ENDING: the article must END on the reframe — one standalone "
    "DECLARATIVE sentence that shifts the reader's thinking. There is "
    "NO CTA, NO discipline-artifact offer, NO URL, and NO checklist "
    "for this register. A checklist is any run of questions in the "
    "reader's voice — 'Do I…?', 'Can I…?', 'Have I…?', 'Did I…?' — "
    "whether at the end or in the body. If the article contains such "
    "a run, or ends with an offer or a URL or a question, it fails. "
    "The final sentence must not end in a question mark.",

    "INSTRUMENT: the cited trade's instrument must appear in the "
    "article at least once as a standalone token.",

    "RECONCILIATION: the article must state, as figures, the "
    "compliant count, the compliant net dollars, the violation count, "
    "the violation net dollars, the per-trade gap, and the violation "
    "rate. These are supplied in the RECONCILIATION block. A prose "
    "gesture ('a narrower gap', 'violations cost more') without the "
    "figures fails.",

    "LENGTH: the article must be at least 770 words. Use the "
    "word count passed in the prompt; do not recount.",
)


_VERIFIER_PROMPT = """You are verifying a trading-psychology article \
before it is published. You receive:

- The article body.
- The cited trade (LATEST TRADE).
- The reading keys from the process block.
- The research block (the only sources the article may cite).

Your job is to check the article against the rules below and return
JSON only.

Article body:
---
{body}
---

LATEST TRADE:
- Date:        {traded_at}
- Instrument:  {instrument}
- Direction:   {direction}
- Contracts:   {contracts}
- Result:      {result}
- Hold:        {hold_time}
- Exit:        {exit_reason}
- Reading key: {reading_key}
- Word count:  {word_count}  (trust this number, do not recount)

Reading keys in the batch:
{reading_keys}

Research block (the only acceptable citations):
{research_block_text}

Rules to check:
{checks}

Return JSON, no prose, exactly this shape:
{{
  "ok": true or false,
  "problems": [
    "one-line description of each problem, or empty if ok"
  ]
}}
"""


async def verify_article_content(
    session: AsyncSession,
    *,
    org_id: str,
    post: Any,
    latest_trade: dict[str, Any] | None,
    readings: dict[str, Any],
    research_block_text: str,
) -> tuple[bool, list[str]]:
    """Run the content verifier. Return (ok, problems).

    Fail-open: if the verifier call fails, return (True, []) so the
    article publishes. The reviewer's other checks still ran.
    """
    from packages.ai.router import complete
    import json as _json

    if not latest_trade:
        return True, []

    reading_keys = ", ".join(
        f"{k} ({v})" for k, v in (readings.get("reading_key_counts") or {}).items()
    ) or "(none)"

    lt = latest_trade
    prompt = _VERIFIER_PROMPT.format(
        body=post.body,
        traded_at=lt.get("traded_at") or "?",
        instrument=lt.get("instrument") or "?",
        direction=lt.get("direction") or "?",
        contracts=lt.get("contracts") or "?",
        result=f"{(lt.get('result') or 0):+.2f}",
        hold_time=lt.get("hold_time") or "?",
        exit_reason=lt.get("exit_reason") or "?",
        reading_key=lt.get("reading_key") or "?",
        reading_keys=reading_keys,
        research_block_text=research_block_text,
        checks="\n".join(f"- {c}" for c in _VERIFIER_CHECKS),
        word_count=post.word_count,
    )

    try:
        raw = await complete(
            role="reasoning",
            system="You verify trading-psychology articles. Return JSON only.",
            user=prompt,
            org_id=org_id,
            purpose="trading.psychology.verify",
            temperature=0.0,
            json_mode=True,
            use_cache=False,
            max_tokens=1500,
        )
    except Exception as exc:
        log.warning(
            "trading_psychology.verify_call_failed",
            error=str(exc)[:200],
        )
        return True, []

    try:
        parsed = _json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = _json.loads(raw[start:end + 1])
            except Exception:
                log.warning("trading_psychology.verify_unparseable")
                return True, []
        else:
            log.warning("trading_psychology.verify_unparseable")
            return True, []

    ok = bool(parsed.get("ok"))
    problems = [str(p)[:300] for p in (parsed.get("problems") or [])][:20]

    log.info(
        "trading_psychology.content_verified",
        ok=ok,
        problems=len(problems),
    )
    for _i, _prob in enumerate(problems, 1):
        log.warning(
            "trading_psychology.content_problem",
            n=_i,
            of=len(problems),
            text=_prob,
        )
    return ok, problems


async def generate(
    session: AsyncSession,
    *,
    org_id: str,
    subblock: dict[str, Any],
) -> dict[str, Any] | None:

    # S.7.8b-4 — editorial voice + form/tone rotation
    form, tone = await pick_form_and_tone(
        session, org_id=org_id, subblock_id=subblock["id"],
        subblock_slug=subblock["slug"],
    )

    from ..registers import pick_tier, target_words_for
    tier_index = (await session.execute(sql_text("""
        SELECT COUNT(*) FROM trading_posts
        WHERE subblock_id = :sid AND deleted_at IS NULL
    """), {"sid": subblock["id"]})).scalar() or 0
    tier = pick_tier(tier_index)

    # S.8.94 — pick the next content coordinate and a CTA.
    covered = await covered_dimensions(session, org_id)
    if not covered:
        log.warning("subblock.no_process_covered", slug=subblock["slug"])
        return {"status": "no_process", "slug": subblock["slug"]}
    coord = await pick_coordinate(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        subblock_slug=subblock["slug"],
        covered_dimensions=covered,
        allowed_angles=SUBBLOCK_ANGLES.get(subblock["slug"]),
    )
    # The CTA is about insider filings, not the coordinate.

    register = await pick_register(
        session,
        org_id=org_id,
        subblock_id=subblock["id"],
        subblock_slug=subblock["slug"],
        tier=tier,
    )
    target_words = target_words_for(subblock["slug"], register, tier)
    voice = await get_org_voice(session, org_id)

    # -- Source: psychology_trade_log, filtered by coordinate -------
    state = await fetch_trader_state(
        session, org_id=org_id, coord=coord["coord"],
    )
    process_block = state["process_block"]
    behaviour_block = state["behaviour_block"]
    readings = state["readings"]
    cooling_off_rule = state["cooling_off_rule"]
    latest_trade = state["latest_trade"]
    cited_row_id = state["cited_row_id"]

    research_block_text = (
        "(Allowed citations — cite ONLY one of these four, or leave "
        "the effect uncited:\n"
        "  KAHNEMAN & TVERSKY 1979 — loss aversion. Losses loom "
        "larger than equivalent gains.\n"
        "  THALER 1980 — mental accounting. Traders segregate "
        "outcomes into separate mental accounts.\n"
        "  ODEAN 1998 — the disposition effect. Investors hold "
        "losers too long and realise winners too early; strongest "
        "after a losing streak.\n"
        "  BARBER & ODEAN 2000 — overtrading. Active trading "
        "reduces net returns; the impulse to act is the impulse the "
        "paper measures.\n"
        "Do NOT cite any other paper, author, or year. If none of "
        "the four fits the claim, name the effect without a "
        "citation.)"
    )

    if latest_trade:
        _lt = latest_trade
        latest_trade_block = (
            f"- Date:        {_lt['traded_at']}\n"
            f"- Instrument:  {_lt['instrument']}\n"
            f"- Direction:   {_lt['direction']}\n"
            f"- Contracts:   {_lt['contracts']}\n"
            f"- Result:      {(_lt['result'] or 0):+.2f}\n"
            f"- Hold:        {_lt['hold_time']}\n"
            f"- Exit:        {_lt['exit_reason']}\n"
            f"- Reading key: {_lt['reading_key']}\n"
            f"\n"
            f"The trader's own log entry for this trade:\n"
            f"\"{_lt['narrative']}\""
        )
    else:
        latest_trade_block = "(no trades recorded)"



    _r = readings or {}
    reconciliation_block = (
        f"  compliant:  n={_r.get('compliant_n')}, "
        f"net ${_r.get('compliant_net'):+,.2f}, "
        f"avg ${_r.get('compliant_avg'):+,.2f}/trade\n"
        f"  violation:  n={_r.get('violation_n')}, "
        f"net ${_r.get('violation_net'):+,.2f}, "
        f"avg ${_r.get('violation_avg'):+,.2f}/trade\n"
        f"  per_trade_gap: ${_r.get('per_trade_gap'):+,.2f}\n"
        f"  violation_rate: {_r.get('violation_rate_pct')}%\n"
        f"  violation_wins: n={_r.get('violation_wins_n')}, "
        f"net ${(_r.get('violation_wins_net') or 0):+,.2f} — "
        f"profitable violations, state these\n"
        f"  sample_period: {_r.get('sample_start')} to "
        f"{_r.get('sample_end')}\n"
        f"  violations_net: "
        f"{'POSITIVE — the finding is the per-trade gap, not a loss' if (_r.get('violation_net') or 0) >= 0 else 'NEGATIVE'}"
        if _r else "  (no reconciliation available)"
    )
    try:
        from packages.trading.psychology_charts import (
            build_psychology_charts_block as _bpc,
        )
        _lt0 = latest_trade or {}
        charts_block = await _bpc(
            session, org_id=org_id,
            symbol=(_lt0.get("instrument") or "").strip().strip("'\""),
            entry=_lt0.get("entry"), stop=_lt0.get("stop"),
            exit_price=_lt0.get("exit"),
            traded_at=_lt0.get("traded_at"),
        )
    except Exception as _ce:
        log.warning("trading_psychology.charts_block_failed",
                    error=str(_ce)[:160])
        charts_block = ""

    user_prompt = PROMPT_TEMPLATE_LOG.format(
        process_block=process_block,
        batch_block=behaviour_block,
        reconciliation_block=reconciliation_block,
        charts_block=charts_block,
        research_block_text=research_block_text,
        mechanism_claim=MECHANISM_CLAIM,
    )

    post = await call_llm_json_verified(
        session,
        org_id=org_id,
        system=compose_system_prompt(
            voice,
            subblock.get("style_guide"),
            "You write clear trading education.",
            form=form,
            tone=tone,
            slug=subblock["slug"],
            register=register,
            tier=tier,
        ),
        user=user_prompt,
        prompt_name="trading.trading_psychology",
        source_payload={
            "topic": coord["coord"],
            "register": register,
            "tier": tier,
            "coord": coord["coord"],
            "block": coord["coord"].split("-")[0],
            "form": form,
            "dimension_label": coord["dimension_label"],
            "angle_label": coord["angle_label"],
            "angle_framing": coord["angle_framing"],
            "trader_state": readings,
            "latest_trade": latest_trade,
            "mechanism_claim": MECHANISM_CLAIM,
            "cooling_off_rule": cooling_off_rule
        },
        target_words=target_words,
        form=form,
        tone=tone,
        voice=voice,
        skip_verify=True,   # S.8.42 — education subblock, no source data
        use_cache=False,    # every filing batch is different
    )

    # -- Content verifier: read the article against the source rows.
    #    Fail-open: if the call errors, publish. Problems append to
    #    unsupported_claims and the article drafts.
    _ok, _problems = await verify_article_content(
        session,
        org_id=org_id,
        post=post,
        latest_trade=latest_trade,
        readings=readings,
        research_block_text=research_block_text,
    )
    if not _ok:
        post.verification_ok = False
        _existing = list(getattr(post, "unsupported_claims", []) or [])
        post.unsupported_claims = _existing + [
            f"__content__: {p}" for p in _problems
        ]

    # Candlestick chart for the cited trade's instrument. Appended
    # after the reframe — there is no table and no CTA in this
    # register, so the chart is the final element.
    #
    # The content verifier above makes an LLM call; if that call
    # fails at the network layer the session can be left closed, and
    # a chart query on it raises InterfaceError('connection is
    # closed'). Roll back first so the chart has a usable
    # connection, or skip it cleanly.
    # Candlestick chart in its OWN session.
    #
    # The content verifier above makes an LLM call on `session`. A
    # network failure there, or the rollback that follows it, can
    # leave that session without the RLS context — a market_history
    # query then returns 0 rows silently (candle_too_few), which is
    # how every psychology chart failed. A fresh session has a clean
    # connection and a fresh set_config, so it cannot inherit that
    # state.
    try:
        from packages.trading.charts.article_charts import (
            render_trade_candlestick,
        )
        from packages.db.session import get_session_factory as _gsf
        from packages.db.tenant import set_current_org as _set_org
        _lt = latest_trade or {}
        _sym = (_lt.get("instrument") or "").strip().strip("'\"")
        async with _gsf()() as _chart_s:
            await _set_org(_chart_s, org_id)
            _candle_url = await render_trade_candlestick(
                _chart_s,
                org_id=org_id,
                symbol=_sym,
                timeframe="1d",
                entry=_lt.get("entry"),
                stop=_lt.get("stop"),
                target=_lt.get("target"),
                exit_price=_lt.get("exit"),
                entry_time=_lt.get("entry_time"),
                exit_time=_lt.get("exit_time"),
            )
        if _candle_url:
            import re as _re
            _img = f"\n\n![{_sym or 'trade'} 1d candles]({_candle_url})\n\n"
            _new, _n = _re.subn(r"\[CHART:\s*daily\s*\]", _img,
                               post.body, flags=_re.IGNORECASE)
            if _n:
                post.body = _new
            else:
                # The model omitted the tag. Append so the chart is
                # never lost, and log it — the prompt requirement
                # failed and the article should be inspected.
                post.body = post.body.rstrip() + _img
                log.warning("trading_psychology.chart_tag_missing",
                            symbol=_sym)
            log.info("trading_psychology.candle_inserted",
                     symbol=_sym, url=_candle_url, replaced=_n)
    except Exception as _ce:
        log.warning("trading_psychology.candle_failed",
                    error=str(_ce)[:200])

    # Deterministic reconciliation check. Fail-closed: a missing
    # figure drafts the article.
    from packages.trading.psychology_validator import (
        validate_psychology as _val_psy,
    )
    _psy_reasons = _val_psy(post.body, readings, latest_trade)
    if _psy_reasons:
        log.warning("trading_psychology.validator_rejected",
                    reasons=_psy_reasons)
        post.verification_ok = False
        post.unsupported_claims = list(
            getattr(post, "unsupported_claims", None) or []
        ) + _psy_reasons

    post_id = await persist_post(
        session,
        org_id=org_id,
        subblock=subblock,
        post=post,
        source_payload={"topic": coord["coord"], "generator": "llm_curated",
                        "register": register, "tier": tier,
                        "coord": coord["coord"],
                        "block": coord["coord"].split("-")[0],
                        "form": form,
                        "dimension_label": coord["dimension_label"],
                        "angle_label": coord["angle_label"],
                        "trader_state": readings,
                        "latest_trade": latest_trade,
                        "mechanism_claim": MECHANISM_CLAIM},
        source_urls=[],
        symbols=[],
    )
    await session.commit()

    # Mark the cited psychology-log row as consumed. Never re-read.
    if cited_row_id:
        try:
            await mark_used(
                session, row_id=cited_row_id, post_id=post_id,
            )
        except Exception as exc:
            log.warning(
                "trading_psychology.mark_used_failed",
                row_id=cited_row_id[:8],
                post_id=post_id[:8],
                error=str(exc)[:200],
            )

    log.info(
        "trading_psychology.persisted",
        post_id=post_id[:8],
        topic=coord["coord"],
        words=post.word_count,
        status="published" if post.verification_ok else "draft",
    )
    return {
        "status": "ok",
        "slug": subblock["slug"],
        "post_id": post_id,
        "topic": coord["coord"],
        "word_count": post.word_count,
        "coord": coord["coord"],
        "block": coord["coord"].split("-")[0],
        "form": form,
        "content_ok": _ok,
        "content_problems": _problems,
    }
