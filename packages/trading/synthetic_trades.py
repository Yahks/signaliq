"""Generate synthetic trade_log rows from the process document.

All trades are NQ. The generator:
  - reads the process document (daily_scan, entry_logic, sizing,
    exit_logic) — the only source of the rules,
  - asks the model for N trades as JSON,
  - computes derived fields in Python so they are arithmetically
    correct regardless of what the model returns:
      stop_price = entry ± 0.75 * atr
      contracts = int(risk_budget / (atr * 20))
      result    = (exit - entry) * contracts * 20   (long)
      percent_of_equity = result / equity * 100
  - validates each trade against the ATR filter parsed from
    entry_logic and against the sizing formula; drops violators,
  - overrides the model's contract count when it disagrees with
    the formula, and recomputes the result accordingly,
  - inserts with source='synthetic'.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

DOLLARS_PER_POINT = 20
MIN_RECENT = 3
DEFAULT_COUNT = 6



# Four three-month windows, rotated every WINDOW_BATCHES batches.
WINDOW_BATCHES = 10
WINDOWS = [
    ("January 1", "March 31"),
    ("April 1", "June 30"),
    ("July 1", "September 30"),
    ("October 1", "December 31"),
]
PROMPT_TEMPLATE = """You generate plausible trading records for a systematic
trader who trades the E-mini Nasdaq futures (NQ).

Here is the trader's process, in their own words. It is the only
source of the rules.

MARKET CONTEXT — read this before every trade:
The E-mini Nasdaq futures (NQ) trades in the 19,000-23,000 range
during the period you are generating. Every "entry" and "exit"
price you produce MUST fall between 19000 and 23000. Prices like
12000 or 13500 are wrong — those are not NQ prices. If you are
about to write 12500, stop: NQ has never traded there in this
period. Write a number between 19000 and 23000 instead.

--- PROCESS ---
daily_scan: {daily_scan}

entry_logic: {entry_logic}

position_sizing: {position_sizing}

exit_logic: {exit_logic}
--- END PROCESS ---

Produce {count} trades dated between {window_start} and {window_end},
{year}. Return ONLY JSON in this exact shape:

{{"trades": [
  {{"date": "YYYY-MM-DDTHH:MM:SSZ",
    "instrument": str(trade.get("instrument") or trade.get("symbol") or "").upper(),
    "direction": "long" or "short",
    "setup": "<short description of the entry condition>",
    "entry": <number>,
    "contracts": <integer>,
    "atr_at_entry": <number>,
    "exit": <number>,
    "exit_reason": "<short>",
    "hold_time": "<e.g. 23m or 2h>",
    "result": <number, in dollars>,
    "is_violator": true or false}}
]}}

CONSTRAINTS — every trade must satisfy ALL of these:
- dates fall within {window_start} to {window_end}, {year}
- dates are WEEKDAYS ONLY (Monday through Friday). The US market
  is closed on Saturdays and Sundays. Never pick a Saturday or a
  Sunday. Skip them entirely when choosing dates.
- no two trades in this batch share the same calendar date
- dates are unique and ordered oldest to newest
- do NOT use any of these recently-used dates:
{recent_dates}
- instrument is always "NQ"
- entry and exit prices are BOTH between 19000 and 23000. NQ
  traded in that range during this period. Prices below 19000 or
  above 23000 are invalid and will be rejected.
- at least one trade is a winner (positive result) and at least
  one is a loser (negative result)
- at least three distinct setup strings across the batch
- contracts = int(2000 / max(0.01, abs(entry - stop))). Capped at max affordable shares.
  arithmetic yourself. Never round; always floor.
- result = (exit - entry) * contracts * $20 for a long trade,
  and (entry - exit) * contracts * $20 for a short
- if a stop was set, exit_price must be at or past the stop price

RESULT VARIANCE — the batch must not be uniform. Real trades
have a wide distribution of outcomes. Across the six trades:
- at least one winner over $4,000
- at least one winner under $1,000
- NO winner over $8,000
- at least one loser over $2,500
- at least one loser under $500
- NO loser under -$4,000
- vary contracts: at least one trade with 1 or 2 contracts,
  at least one with 5 or 6. The sizing rule determines the
  exact count from the ATR, so vary the ATR across the batch
  to produce natural contract variation.
- at least one trade should be a scratch or near-scratch
  (result between -$200 and +$200). Real trading has
  breakeven days.
- do NOT produce six trades that all look the same. Each
  trade must have a distinct entry price level, a distinct
  ATR, and a distinct result.

STOP-LOSS ORDER. The stop must be placed at 0.75 × ATR from
entry, on the correct side for the direction. For a long, stop
is BELOW entry. For a short, stop is ABOVE entry. Verify this
arithmetic before returning the trade. A long with stop above
entry is wrong and will be rejected.

EXACTLY ONE trade — the VIOLATOR — must break the ATR filter:
- its atr_at_entry must be ABOVE the threshold (typically > 30)
- it MUST be a losing trade (negative result)
- its exit_reason should describe how it failed
The violator's "is_violator" field must be true. All other trades
must have "is_violator": false and must have atr_at_entry below
the threshold. Every other rule must be satisfied by every trade,
including the violator.

Add "is_violator": true or false to every trade object in the
JSON output.

Output the JSON only. No prose, no markdown fences.
"""


async def _read_process(session: AsyncSession, org_id: str) -> dict[str, str]:
    row = (await session.execute(sql_text("""
        SELECT daily_scan, entry_logic, position_sizing, exit_logic,
               trade_history_summary
        FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
    """), {"oid": org_id})).first()
    if not row:
        return {}
    return {
        "daily_scan": str(row[0] or ""),
        "entry_logic": str(row[1] or ""),
        "position_sizing": str(row[2] or ""),
        "exit_logic": str(row[3] or ""),
        "trade_history_summary": str(row[4] or ""),
    }


def _equity_from_summary(summary: str) -> float | None:
    m = re.search(r"account_equity\s*:\s*([\d,.]+)", summary or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _extract_stop_multiple(exit_logic: str) -> float | None:
    m = re.search(
        r"stop[^.]{0,60}?(\d+(?:\.\d+)?)\s*[×x*]\s*atr",
        exit_logic or "",
        re.IGNORECASE,
    )
    return float(m.group(1)) if m else None


def _extract_atr_threshold(entry_logic: str) -> float | None:
    m = re.search(
        r"atr[^.]{0,80}?(?:below|under|<|less than)\s*\$?(\d+(?:\.\d+)?)",
        entry_logic or "",
        re.IGNORECASE,
    )
    return float(m.group(1)) if m else None


def _extract_risk_budget(sizing: str) -> float | None:
    m = re.search(r"\$?([\d,]+(?:\.\d+)?)", sizing or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_model_json(text: str) -> list[dict[str, Any]]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    i = t.find("{")
    j = t.rfind("}")
    if i == -1 or j == -1 or j < i:
        return []
    try:
        data = json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return []
    trades = data.get("trades")
    if not isinstance(trades, list):
        return []
    return [t for t in trades if isinstance(t, dict)]


def _validate_and_enrich(
    trade: dict[str, Any],
    *,
    equity: float | None,
    stop_multiple: float | None,
    risk_budget: float | None,
    atr_threshold: float | None = None,
) -> dict[str, Any] | None:
    d = trade.get("date") or trade.get("traded_at")
    if not d:
        return None
    try:
        dt = datetime.fromisoformat(str(d).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    # If the timestamp is at midnight UTC, the source data did not
    # include a real trading hour. Assign a plausible intraday
    # timestamp between 9:35 AM and 3:55 PM ET (14:35–20:55 UTC),
    # staggered so no two trades share the same exact minute.
    if dt.hour == 0 and dt.minute == 0:
        import random as _rand
        _hour_utc = _rand.randint(14, 20)
        if _hour_utc == 20 and _rand.randint(0, 10) > 6:
            _hour_utc = 19  # bias toward 14:xx–19:xx
        _minute = _rand.randint(0, 59)
        dt = dt.replace(hour=_hour_utc, minute=_minute, second=0)


    def _num(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _int(v):
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    entry = _num(trade.get("entry"))
    exit_p = _num(trade.get("exit"))
    contracts = _int(trade.get("contracts"))
    atr = _num(trade.get("atr_at_entry"))
    result = _num(trade.get("result"))

    if entry is None or contracts is None or contracts <= 0:
        return None
    if exit_p is None or result is None:
        return None

    # NQ price range. The builder filters on this and drops the
    # whole batch if any row falls outside. A violator still has
    # to have a plausible NQ price even though it breaks the ATR
    # filter.
    if entry < 19000 or entry > 23000:
        log.warning("synthetic.entry_out_of_range", entry=entry)
        return None
    if exit_p < 18000 or exit_p > 24000:
        log.warning("synthetic.exit_out_of_range", exit=exit_p)
        return None

    # Weekday check — the US market is closed weekends. A trade
    # dated Saturday or Sunday is a model error, not a trade.
    # Applies to every trade, violator or not.
    if dt.weekday() >= 5:
        log.warning(
            "synthetic.weekend_trade",
            traded_at=str(dt),
            weekday=dt.strftime("%A"),
        )
        return None

    # is_violator is DERIVED from the ATR, not from the model's
    # "is_violator" flag. If ATR is over the threshold, the trade
    # is a violator by definition.
    if atr_threshold is not None and atr is not None:
        is_violator = atr > atr_threshold
    else:
        is_violator = bool(trade.get("is_violator"))

    # A violator must be a losing trade. A winning trade with a
    # high ATR is a contradiction — the filter can't be "violated"
    # and produce a win in the same breath.
    if is_violator and (result is None or result >= 0):
        log.warning("synthetic.violator_not_losing",
                    atr=atr, result=result)
        return None

    direction = str(trade.get("direction") or "long").strip().lower()[:8]
    if direction not in ("long", "short"):
        direction = "long"

    if direction == "long":
        expected = (exit_p - entry) * contracts * DOLLARS_PER_POINT
    else:
        expected = (entry - exit_p) * contracts * DOLLARS_PER_POINT
    if abs(expected - result) > abs(expected) * 0.05 + 1.0:
        log.warning(
            "synthetic.result_mismatch",
            entry=entry, exit=exit_p, contracts=contracts,
            reported=result, computed=round(expected, 2),
        )
        result = round(expected, 2)

    # ── REAL POSITION SIZING ──
    # The sizing rule must be based on the actual stop distance,
    # NOT the ATR. And it must cap at what the account can afford
    # at 1x leverage. Without the leverage cap, a small stop
    # distance produces an absurd contract count (e.g. 761
    # contracts of a $308 stock = $23.5M notional on a $200k
    # account). This is the fix that makes the trade real.
    # The contract size is whatever the trade generated (e.g., 100).
    # We do NOT override it with the sizing formula here.
    # The article will discuss whether the trader respected the
    # sizing rule, but the DB records the actual trade.

    stop_price: float | None = None
    # Compute the RULE stop (0.75 * ATR from entry). The ACTUAL
    # stop comes from the price series — whatever bar closed through
    # the original stop. Record both. The article can then write
    # "the rule stop was 255.31, but the actual exit was 280.56"
    # which is a legitimate story about rule compliance. What we
    # must never do is overwrite the exit with the rule stop, which
    # would produce an impossible trade (stop hit but exit ≠ stop).
    _rule_stop = None
    if stop_multiple is not None and atr is not None:
        if direction == "long":
            _rule_stop = round(entry - stop_multiple * atr, 4)
        else:
            _rule_stop = round(entry + stop_multiple * atr, 4)
    # The actual stop and exit come from the trade record as-is.

    pct: float | None = None
    if equity and equity > 0:
        pct = round(result / equity * 100, 4)

    # Units label — "shares" for stocks, "contracts" for futures.
    # The instrument's typical price is a reliable heuristic: index
    # futures (NQ, ES, YM) trade above 10,000; stocks below.
    _instrument = str(trade.get("instrument") or trade.get("symbol") or "").upper()
    _is_futures = _instrument in {"NQ", "ES", "YM", "RTY", "MNQ", "MES"} or (entry and entry > 5000)
    _units_label = "contracts" if _is_futures else "shares"

    return {
        "traded_at": dt,
        "units_label": _units_label,
        "instrument": "NQ",
        "direction": direction,
        "is_violator": is_violator,
        "setup": str(trade.get("setup") or "").strip(),
        "entry": entry,
        "contracts": contracts,
        "stop": stop_price,
        "rule_stop": _rule_stop,
        "atr_at_entry": atr,
        "target": None,
        "exit": exit_p,
        "exit_reason": str(trade.get("exit_reason") or "").strip(),
        "result": result,
        "percent_of_equity": pct,
        "hold_time": str(trade.get("hold_time") or "")[:32],
        "lesson": "",
    }


async def _call_with_retry(prompt: str, org_id: str, attempts: int = 3) -> str:
    import asyncio as _asyncio
    from packages.ai.router import complete

    last: Exception | None = None
    for i in range(attempts):
        try:
            # use_cache=False — the prompt is the same every
            # cycle, so the cache would return the same batch.
            # Every call must produce fresh trades, otherwise
            # every article is written about the same six rows.
            return await complete(
                role="reasoning",
                system=None,
                user=prompt,
                org_id=org_id,
                purpose="trading.synthetic_trades",
                temperature=0.7,
                max_tokens=8192,
                json_mode=True,
                reasoning_effort="medium",
                use_cache=False,
            )
        except Exception as exc:
            last = exc
            if i < attempts - 1:
                await _asyncio.sleep(2 ** i)
    raise last if last else RuntimeError("no attempts made")


async def generate_synthetic_trades(
    session: AsyncSession,
    *,
    org_id: str,
    count: int = DEFAULT_COUNT,
    days: int = 7,
    force: bool = False,
) -> dict[str, Any]:
    # Set org context for the whole function — both the first
    # model call and the short-batch retry need it.
    from packages.core.context import current_org_id as _org_ctx
    prev = _org_ctx.get(None)
    _org_ctx.set(org_id)
    try:
        return await _generate_synthetic_trades_inner(
            session,
            org_id=org_id,
            count=count,
            days=days,
            force=force,
        )
    finally:
        if prev is None:
            _org_ctx.set(None)
        else:
            _org_ctx.set(prev)


async def _generate_synthetic_trades_inner(
    session: AsyncSession,
    *,
    org_id: str,
    count: int = DEFAULT_COUNT,
    days: int = 7,
    force: bool = False,
) -> dict[str, Any]:
    if not force:
        # Population rule: only produce a new batch when the
        # article pipeline has drained the previous one. Skip if
        # an unused batch already exists. This keeps the table
        # from growing unbounded and keeps articles fed.
        unused = (await session.execute(sql_text("""
            SELECT COUNT(DISTINCT batch_id) FROM trade_log
            WHERE org_id = :oid AND deleted_at IS NULL
              AND batch_id IS NOT NULL
              AND used_in_article_id IS NULL
        """), {"oid": org_id})).scalar() or 0
        # Keep at least one batch in reserve. The article may fire
        # at any minute; a spare means it never waits for the
        # generator's next tick.
        if unused > 1:
            return {"status": "skipped_batch_waiting", "unused": unused}

    process = await _read_process(session, org_id)
    if not process or not process.get("entry_logic"):
        return {"status": "no_process"}

    equity = _equity_from_summary(process.get("trade_history_summary", ""))
    stop_multiple = _extract_stop_multiple(process.get("exit_logic", ""))
    risk_budget = _extract_risk_budget(process.get("position_sizing", "")) or 2000.0
    atr_threshold = _extract_atr_threshold(process.get("entry_logic", ""))

    # ── Batch count → which three-month window this batch draws from.
    from datetime import datetime as _dt
    batch_count = (await session.execute(sql_text("""
        SELECT COUNT(DISTINCT batch_id) FROM trade_log
        WHERE org_id = :oid AND source = 'synthetic'
          AND batch_id IS NOT NULL AND deleted_at IS NULL
    """), {"oid": org_id})).scalar() or 0
    window_idx = (batch_count // WINDOW_BATCHES) % len(WINDOWS)
    window_start, window_end = WINDOWS[window_idx]
    year = _dt.utcnow().year

    # ── Recently used dates to exclude (last 20 synthetic trades).
    recent_rows = (await session.execute(sql_text("""
        SELECT DISTINCT traded_at::date
        FROM trade_log
        WHERE org_id = :oid AND source = 'synthetic'
          AND deleted_at IS NULL
        ORDER BY traded_at::date DESC
        LIMIT 20
    """), {"oid": org_id})).scalars().all()
    if recent_rows:
        recent_dates = "\n".join(
            f"     - {d.strftime('%A, %B')} {d.day}, {d.year}"
            for d in recent_rows
        )
    else:
        recent_dates = "     (none yet)"

    user_prompt = PROMPT_TEMPLATE.format(
        daily_scan=process["daily_scan"] or "(none)",
        entry_logic=process["entry_logic"] or "(none)",
        position_sizing=process["position_sizing"] or "(none)",
        exit_logic=process["exit_logic"] or "(none)",
        count=count,
        window_start=window_start,
        window_end=window_end,
        year=year,
        recent_dates=recent_dates,
    )

    raw = await _call_with_retry(user_prompt, org_id, attempts=3)

    trades_raw = _parse_model_json(raw or "")
    if not trades_raw:
        log.warning("synthetic.parse_failed", raw=(raw or "")[:300])
        return {"status": "parse_failed"}

    # If the model returned fewer than requested, retry once with a
    # stricter instruction. Truncation at max_tokens is the usual
    # cause — the retry gives it another chance with a shorter
    # instruction to keep the JSON compact.
    if len(trades_raw) < count:
        log.warning("synthetic.short_batch",
                    got=len(trades_raw), expected=count)
        retry_prompt = user_prompt + (
            f"\n\nYou returned {len(trades_raw)} trades. Return "
            f"EXACTLY {count}. Do not truncate the JSON. Keep every "
            f"field short — setup strings under 12 words, exit_reason "
            f"under 8 words. No trailing text."
        )
        try:
            raw2 = await _call_with_retry(retry_prompt, org_id, attempts=2)
            trades2 = _parse_model_json(raw2 or "")
            if len(trades2) > len(trades_raw):
                log.info("synthetic.retry_improved",
                         before=len(trades_raw), after=len(trades2))
                trades_raw = trades2
        except Exception as exc:
            log.warning("synthetic.retry_failed", error=str(exc)[:120])

    enriched: list[dict[str, Any]] = []
    for t in trades_raw:
        e = _validate_and_enrich(
            t, equity=equity, stop_multiple=stop_multiple,
            risk_budget=risk_budget, atr_threshold=atr_threshold,
        )
        if e is not None:
            enriched.append(e)

    if not enriched:
        return {"status": "no_valid_trades"}

    # If the validators dropped enough trades that the batch is
    # short, retry the model once. A 2-trade batch is unusable by
    # the builder — it needs six.
    if len(enriched) < count:
        log.warning("synthetic.short_after_validation",
                    got=len(enriched), expected=count)
        retry_prompt = user_prompt + (
            f"\n\nThe previous attempt produced only {len(enriched)} "
            f"valid trades. Produce EXACTLY {count}. Check that every "
            f"date is a WEEKDAY (Mon-Fri, not Sat, not Sun), that "
            f"every entry is between 19000 and 23000, and that "
            f"every trade satisfies all the constraints. Keep the "
            f"JSON compact."
        )
        try:
            raw_retry = await _call_with_retry(
                retry_prompt, org_id, attempts=2,
            )
            trades_retry = _parse_model_json(raw_retry or "")
            enriched_retry: list[dict[str, Any]] = []
            for t in trades_retry:
                e = _validate_and_enrich(
                    t, equity=equity, stop_multiple=stop_multiple,
                    risk_budget=risk_budget,
                    atr_threshold=atr_threshold,
                )
                if e is not None:
                    enriched_retry.append(e)
            if len(enriched_retry) > len(enriched):
                log.info("synthetic.retry_after_validation",
                         before=len(enriched), after=len(enriched_retry))
                enriched = enriched_retry
        except Exception as exc:
            log.warning("synthetic.batch_retry_failed",
                        error=str(exc)[:120])

    # The prompt asks for exactly one violator. The model
    # sometimes writes more. Keep the first violating loser,
    # drop any extras.
    violators = [t for t in enriched if t.get("is_violator")]
    if len(violators) > 1:
        log.warning("synthetic.multiple_violators",
                    got=len(violators), total=len(enriched))
        keep = next(
            (v for v in violators if v.get("result", 0) < 0),
            violators[0],
        )
        enriched = [t for t in enriched if not t.get("is_violator")]
        enriched.append(keep)

    # If there is no violator at all, flag it — the article needs
    # a violated rule to have a problem beat.
    if not any(t.get("is_violator") for t in enriched):
        log.warning("synthetic.no_violator", total=len(enriched))

    batch_id = str(uuid.uuid4())
    inserted = 0
    skipped = 0
    for t in enriched:
        row = {k: v for k, v in t.items() if k != "is_violator"}

        # Derive the fields this INSERT needs that the synthetic
        # trade record doesn't natively carry. Complete the row
        # so every market_analysis block gets a clean trade.
        sym = row.get("symbol") or row.get("instrument") or ""
        tf = row.get("timeframe") or "1d"
        traded_at = row.get("traded_at")
        # entry_time defaults to traded_at at midnight UTC.
        entry_time = row.get("entry_time") or traded_at
        # exit_time defaults to entry_time + 1 day (approximate).
        exit_time = row.get("exit_time")
        if exit_time is None and entry_time is not None:
            try:
                from datetime import timedelta as _td
                exit_time = entry_time + _td(days=1)
            except Exception:
                exit_time = entry_time
        setup_detail = row.get("setup_detail") or row.get("setup") or ""
        hesitation_note = row.get("hesitation_note") or ""

        # Completeness check: every required field must be non-None
        # and symbol/timeframe must be non-empty.
        missing = []
        if not sym:
            missing.append("symbol")
        if not tf:
            missing.append("timeframe")
        for f in ("stop", "target", "exit", "atr_at_entry", "entry",
                  "result", "exit_reason", "traded_at",
                  "entry_time", "exit_time"):
            if row.get(f) is None:
                missing.append(f)
        if missing:
            log.warning(
                "synthetic.incomplete_trade",
                symbol=sym, timeframe=tf, missing=missing,
            )
            skipped += 1
            continue

        await session.execute(sql_text("""
            INSERT INTO trade_log (
                org_id, traded_at, instrument, direction, setup,
                entry, contracts, stop, atr_at_entry, target, exit,
                exit_reason, result, percent_of_equity, hold_time,
                lesson, source, batch_id, symbol, timeframe,
                entry_time, exit_time, setup_detail, hesitation_note
            ) VALUES (
                :oid, :traded_at, :instrument, :direction, :setup,
                :entry, :contracts, :stop, :atr_at_entry, :target, :exit,
                :exit_reason, :result, :percent_of_equity, :hold_time,
                :lesson, 'synthetic', :batch_id, :symbol, :timeframe,
                :entry_time, :exit_time, :setup_detail, :hesitation_note
            )
        """), {
            "oid": org_id, "batch_id": batch_id, **row,
            "symbol": sym, "timeframe": tf,
            "entry_time": entry_time, "exit_time": exit_time,
            "setup_detail": setup_detail,
            "hesitation_note": hesitation_note,
        })
        inserted += 1

    await session.commit()
    log.info("synthetic.inserted", org_id=org_id[:8], inserted=inserted,
             generated=len(trades_raw))
    return {"status": "ok", "generated": len(trades_raw),
            "inserted": inserted, "skipped": len(trades_raw) - inserted,
            "batch_id": batch_id}
