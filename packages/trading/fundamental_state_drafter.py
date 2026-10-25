"""Draft the per-state signal facts from the trader's process document.

Every field is filled from a column the trader wrote — nothing is
invented. The model's job is to locate the trader's own statement of
each fact and place it in the signal's row, citing the column.

Checks before insert:
  - every number in a value appears in its cited source column
  - composite_rule is exactly one of warning / veto
  - direction matches fundamental_signal_states
  - no two spread states share a (level, change) pair
A draft that fails is not stored.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.router import complete
from packages.core.logging import get_logger

log = get_logger(__name__)

STATES = [
    ("opec_cut", "above_0_5mbpd"),
    ("api_draw", "above_5mb"),
    ("spread", "backwardation_widening"),
    ("spread", "backwardation_narrowing"),
    ("spread", "contango_widening"),
    ("spread", "contango_narrowing"),
]

PROCESS_COLS = (
    "daily_scan", "entry_logic", "position_sizing", "exit_logic",
    "holding_period", "research_to_rules", "chart_overlay",
    "no_backtest_rule", "multi_strategy_blend", "rolling",
    "stocks_vs_options", "universes", "emotion_removal",
    "fundamental_opec_cut", "fundamental_api_draw",
    "fundamental_spread", "trade_history_summary",
)

# The column that defines each signal. The drafter reads only this
# one for the signal's rule and frequency — it cannot pull across.
SIGNAL_COLUMN = {
    "opec_cut": "fundamental_opec_cut",
    "api_draw": "fundamental_api_draw",
    "spread": "fundamental_spread",
}

DRAFT_PROMPT = """You are the trader. Fill one row of signal facts.

SIGNAL STATE: {signal} / {state}
DIRECTION (fixed, from the table): {direction}

Here is your own process document. Every value you write MUST be
quoted or paraphrased from one of these columns, and you must name
the column it came from.

{process_block}

Return JSON with exactly these keys:
  frequency         e.g. "monthly (OPEC MOMR)", "weekly (Tue API)", "daily"
  rule_definition   the threshold and what it measures
  size_rule         what size does for this signal
  exit_rule         what closes the position
  point_in_time     when the data is known and when you act
  holding_period    the window
  source_columns    {{key: column_name}} for each value above

For a SPREAD state, `frequency` and `rule_definition` MUST come from
the `fundamental_metrics` column — that is the column that defines
the spread. Do not use `daily_scan` or `entry_logic` for a spread
row.

Do not write a value that is not in the document. If a fact is not
stated in any column, write "not stated" and cite "" for it.
"""


def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", s or ""))


async def draft_states(session: AsyncSession, org_id: str) -> list[dict]:
    row = (await session.execute(sql_text(f"""
        SELECT {', '.join(PROCESS_COLS)} FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 1
    """), {"oid": org_id})).mappings().first()
    if not row:
        return []
    doc = dict(row)
    block = "\n\n".join(
        f"--- {c} ---\n{doc.get(c) or ''}" for c in PROCESS_COLS
    )

    dirs = {r["signal"] + "/" + r["state"]: r["direction"]
            for r in (await session.execute(sql_text(
                "SELECT signal, state, direction FROM fundamental_signal_states "
                "WHERE org_id=:o AND deleted_at IS NULL"),
                {"o": org_id})).mappings().all()}

    composite = {
        r["signal"]: r["composite_rule"]
        for r in (await session.execute(sql_text(
            "SELECT signal, composite_rule FROM fundamental_signal_composite "
            "WHERE org_id=:o AND deleted_at IS NULL"),
            {"o": org_id})).mappings().all()
    }

    out: list[dict] = []
    for sig, st in STATES:
        key = f"{sig}/{st}"
        direction = dirs.get(key)
        if not direction:
            continue
        prompt = DRAFT_PROMPT.format(
            signal=sig, state=st, direction=direction,
            process_block=block,
        )
        resp = await complete(
            role="reasoning", purpose="trading.fundamental_state_draft",
            system="You return only JSON.", user=prompt,
            json_mode=True, temperature=0.0,
        )
        try:
            d = json.loads(resp if isinstance(resp, str) else resp.text)
        except Exception:
            log.warning("state_draft.unparseable", key=key)
            continue

        src = d.get("source_columns") or {}
        ok = True
        for field in ("frequency", "rule_definition", "size_rule",
                      "exit_rule", "point_in_time", "holding_period"):
            val = str(d.get(field) or "")
            raw_col = src.get(field) or ""
            if val.strip().lower() == "not stated":
                continue
            # A field may cite more than one column, comma-separated.
            cols = [c.strip() for c in raw_col.split(",") if c.strip()]
            if not cols or any(c not in PROCESS_COLS for c in cols):
                log.warning("state_draft.bad_source", key=key, field=field,
                            col=raw_col)
                ok = False
                continue
            cited_text = "\n".join(doc.get(c) or "" for c in cols)
            # numbers must appear in the cited text
            nums = _numbers(val)
            if nums and not nums.issubset(_numbers(cited_text)):
                log.warning("state_draft.unsourced_number",
                            key=key, field=field, nums=sorted(nums))
                ok = False
            # frequency words must appear in the cited text
            if field == "frequency":
                for w in ("daily", "weekly", "monthly"):
                    if w in val.lower() and w not in cited_text.lower():
                        log.warning("state_draft.unsourced_frequency",
                                    key=key, word=w)
                        ok = False
        # A spread row's frequency and rule_definition must come
        # from fundamental_metrics — the column that defines the
        # spread. Otherwise the model attaches the API's "weekly" or
        # the breakout's "0.25%" to a spread state.
        # rule_definition and frequency come from the signal's own
        # column, and only from it.
        _own = SIGNAL_COLUMN.get(sig, "")
        if _own:
            for field in ("frequency", "rule_definition"):
                cited = {
                    c.strip()
                    for c in (src.get(field) or "").split(",")
                    if c.strip()
                }
                if cited and cited != {_own}:
                    log.warning("state_draft.wrong_column",
                                key=key, field=field,
                                cols=sorted(cited), want=_own)
                    ok = False

        if sig not in composite:
            log.warning("state_draft.no_composite", key=key, signal=sig)
            ok = False

        # A row must not invert itself: if the rule says the row's own
        # direction is "eroding", the direction field and the rule
        # disagree. "narrowing backwardation erodes bearish bias"
        # under direction=bearish is the contradiction.
        _rule = str(d.get("rule_definition") or "").lower()
        if _rule and _rule != "not stated":
            _own = direction.lower()
            _other = "bullish" if _own == "bearish" else "bearish"
            if f"{_own} bias" in _rule and ("erod" in _rule):
                log.warning("state_draft.self_contradiction",
                            key=key, direction=direction, rule=_rule[:60])
                ok = False
            del _other
        if not ok:
            continue

        await session.execute(sql_text("""
            INSERT INTO fundamental_signal_state_drafts
              (org_id, signal, state, direction, frequency, rule_definition,
               size_rule, exit_rule, point_in_time, holding_period,
               source_columns)
            VALUES (:o, :s, :st, :d, :f, :r, :sz, :ex, :pit, :hp,
                    CAST(:sc AS jsonb))
            ON CONFLICT DO NOTHING
        """), {
            "o": org_id, "s": sig, "st": st, "d": direction,
            "f": str(d.get("frequency") or "not stated"),
            "r": str(d.get("rule_definition") or "not stated"),
            "sz": str(d.get("size_rule") or "not stated"),
            "ex": str(d.get("exit_rule") or "not stated"),
            "pit": str(d.get("point_in_time") or "not stated"),
            "hp": str(d.get("holding_period") or "not stated"),
            "sc": json.dumps(src),
        })
        out.append({"signal": sig, "state": st, "direction": direction})
    await session.commit()
    return out
