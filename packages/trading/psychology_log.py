"""Generate and read psychology-log entries.

The `psychology_trade_log` table holds model-written narratives of
trades a trader took — real-shaped numbers, invented dates and
instruments, first-person narration. The trading_psychology
subblock reads one unused row per article and marks it used.

The generator runs lazily: when the unused pool for an
(org, coord) drops to REFILL_THRESHOLD rows or fewer, BATCH_SIZE
new rows are generated in one batch.

The numbers the model writes are constrained:
  - Instruments: ES, CL, GC, 6E, ZB, MNQ (no NQ — trade_log owns it)
  - Dates: 2026 weekdays, excluding US market holidays
  - P&L shape: matches the trader's real trade_log distribution

The generator reads trade_log for the *shape* (win rate, hold-time
distribution, exit-reason frequency) — it never quotes a real
trade. The numbers in psychology_trade_log are the model's.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.ai.router import complete
from packages.core.logging import get_logger

log = get_logger(__name__)


INSTRUMENTS = ("ES", "CL", "GC", "6E", "ZB", "MNQ")

HOLIDAYS_2026 = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
}

BATCH_SIZE = 6
REFILL_THRESHOLD = 2


# ─────────────────────────────────────────────────────────────────────
# Date picking
# ─────────────────────────────────────────────────────────────────────

def _pick_dates(n: int) -> list[str]:
    """Pick n weekday ISO dates in 2026, no holidays, spread apart.

    No two dates in the same calendar week. Spread across the year.
    """
    candidates: list[date] = []
    d = date(2026, 1, 2)  # first weekday of 2026
    end = date(2026, 12, 31)
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS_2026:
            candidates.append(d)
        d += timedelta(days=1)

    # Group by ISO week so we can pick at most one per week.
    by_week: dict[int, list[date]] = {}
    for c in candidates:
        by_week.setdefault(c.isocalendar()[1], []).append(c)

    weeks = sorted(by_week.keys())
    rng = random.Random()  # non-seeded; each batch gets new dates
    rng.shuffle(weeks)
    picked = [rng.choice(by_week[w]) for w in weeks[:n]]
    picked.sort()
    return [p.isoformat() for p in picked]


# ─────────────────────────────────────────────────────────────────────
# Read the trader's real behaviour shape
# ─────────────────────────────────────────────────────────────────────

async def _read_behaviour_shape(
    session: AsyncSession, *, org_id: str,
) -> dict[str, Any]:
    """Summarise the trader's real trade_log into a shape prompt.

    Does NOT quote a specific trade. Returns distributions only.
    """
    rows = (await session.execute(sql_text("""
        SELECT result, hold_time, exit_reason
        FROM trade_log
        WHERE org_id = :oid
          AND deleted_at IS NULL
          AND traded_at <= now()
        ORDER BY traded_at DESC
        LIMIT 60
    """), {"oid": org_id})).mappings().all()

    if not rows:
        return {
            "recent_win_rate": 0.5,
            "avg_hold_winners_min": 45.0,
            "avg_hold_losers_min": 30.0,
            "exit_reasons": ["target hit", "stop loss", "time exit"],
            "typical_result_win": 500.0,
            "typical_result_loss": -400.0,
        }

    results = [float(r["result"]) for r in rows if r["result"] is not None]
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

    hw = [_mins(r["hold_time"]) for r in rows
          if r["result"] is not None and float(r["result"]) > 0]
    hl = [_mins(r["hold_time"]) for r in rows
          if r["result"] is not None and float(r["result"]) < 0]
    hw = [x for x in hw if x is not None]
    hl = [x for x in hl if x is not None]

    exit_reasons: dict[str, int] = {}
    for r in rows:
        _ok, _why = _row_consistent(r)
        if not _ok:
            log.warning("psychology_log.row_inconsistent",
                        coord=coord, reason=_why,
                        instrument=r.get("instrument"),
                        entry=r.get("entry"), stop=r.get("stop"),
                        exit=r.get("exit"), result=r.get("result"))
            continue
        er = (r["exit_reason"] or "?").strip()
        exit_reasons[er] = exit_reasons.get(er, 0) + 1

    top_exits = sorted(exit_reasons.items(), key=lambda kv: -kv[1])[:5]

    return {
        "recent_win_rate": round(len(wins) / max(len(results), 1), 2),
        "avg_hold_winners_min": round(sum(hw) / len(hw), 0) if hw else 45.0,
        "avg_hold_losers_min": round(sum(hl) / len(hl), 0) if hl else 30.0,
        "exit_reasons": [k for k, _ in top_exits] or ["target hit", "stop loss"],
        "typical_result_win": round(sum(wins) / len(wins), 0) if wins else 500.0,
        "typical_result_loss": round(sum(losses) / len(losses), 0) if losses else -400.0,
    }


# ─────────────────────────────────────────────────────────────────────
# Generate a batch
# ─────────────────────────────────────────────────────────────────────

_GEN_PROMPT = """You are writing entries for a trader's psychology log.

You have two jobs:

1. Generate {n} trades that fit the trader's real behaviour. The
   shape below is their actual P&L distribution. Your {n} trades
   must match the shape: similar win rate, similar hold-time
   spread, similar exit reasons. But the instruments and dates
   are yours to pick, from the lists below.

2. For each trade, write a first-person narrative of 60-100 words
   about what the trader felt, what rule they broke or kept, and
   what they noticed about their own state. This narrative is what
   the article will quote, so it must read as lived experience,
   not as a report.

Behaviour shape (from the trader's real log):

- win rate: {wr}
- average hold on winners: {hw} min
- average hold on losers: {hl} min
- exit reasons in use: {exits}
- typical winning result: {win}
- typical losing result: {loss}

Constraints:

- Instruments: one of {instruments} per trade. Do NOT use NQ.
- Dates: exactly these {n} weekdays. Use each once.
{dates}

- Direction: long or short.
- Contracts: integer 1-6.
- Entry/stop/target/exit: plausible for the instrument. Result
  must equal (exit - entry) * contracts * point_value for the
  direction, rounded to dollars. Point values: ES $50, CL $1000,
  GC $100, 6E $1250, ZB $1000, MNQ $2.
- hold_time: a string like "45m" or "2h 10m". Match the shape.
- exit_reason: one of the exit reasons above.
- reading_key: a short slug naming the behaviour this trade
  illustrates — e.g. "post_loss_reentry", "held_winner_too_long",
  "cut_early_on_reversal", "size_creep_after_win",
  "followed_rule", "broke_rule".

Return JSON, no prose, exactly this shape:

{{
  "trades": [
    {{
      "traded_at": "YYYY-MM-DDT14:30:00Z",
      "instrument": "ES",
      "direction": "long",
      "contracts": 2,
      "entry": 5840.00,
      "stop": 5830.00,
      "target": 5860.00,
      "exit": 5852.00,
      "exit_reason": "target hit",
      "result": 240.00,
      "hold_time": "45m",
      "reading_key": "followed_rule",
      "narrative": "..."
    }}
  ]
}}
"""


async def _call_model_for_batch(
    session: AsyncSession,
    *,
    org_id: str,
    coord: str,
    n: int,
) -> list[dict[str, Any]]:
    """Ask the model for n trades. Parse and validate."""
    shape = await _read_behaviour_shape(session, org_id=org_id)
    dates = _pick_dates(n)

    prompt = _GEN_PROMPT.format(
        n=n,
        wr=shape["recent_win_rate"],
        hw=int(shape["avg_hold_winners_min"]),
        hl=int(shape["avg_hold_losers_min"]),
        exits=", ".join(shape["exit_reasons"]),
        win=int(shape["typical_result_win"]),
        loss=int(shape["typical_result_loss"]),
        instruments=", ".join(INSTRUMENTS),
        dates="\n".join(f"  - {d}" for d in dates),
    )

    raw = await complete(
        role="reasoning",
        system="You write trading-psychology log entries. Return JSON only.",
        user=prompt,
        org_id=org_id,
        purpose="trading.psychology_log.generate",
        temperature=0.7,
        json_mode=True,
        use_cache=False,
        max_tokens=4000,
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract the first {...} block.
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end + 1])
        else:
            raise

    trades = parsed.get("trades") or []
    if len(trades) != n:
        log.warning(
            "psychology_log.short_batch",
            requested=n, got=len(trades), coord=coord,
        )

    # Normalise dates: parse traded_at into a real datetime.
    for t in trades:
        d = str(t.get("traded_at", ""))[:10]
        if d not in dates:
            # Snap to the first allowed date that hasn't been used.
            t["traded_at"] = f"{dates[0]}T14:30:00Z"
        raw = str(t["traded_at"]).replace(" ", "T").strip()
        if "T" not in raw:
            raw = f"{raw}T14:30:00Z"
        if not raw.endswith("Z") and "+" not in raw[10:]:
            raw = raw + "Z"
        # asyncpg wants a datetime object, not a string.
        try:
            t["traded_at"] = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            # Fall back to the first allowed date at 14:30 UTC.
            t["traded_at"] = datetime.fromisoformat(
                f"{dates[0]}T14:30:00+00:00"
            )

    return trades


def _row_consistent(r: dict) -> tuple[bool, str]:
    """Reject a generated trade whose prices cannot all be true.

    Row 60a6d5c2 was a short at 80.00 with a stop at 80.50, an exit
    at 79.51, exit_reason 'stop hit', and result -$487. All four
    cannot hold: 79.51 is below the 80.00 entry, so the trade
    profited; and 'stop hit' at 79.51 is impossible with the stop
    at 80.50. Articles then described that row faithfully and every
    gate passed, because they matched the row.

    Checks:
      - stop on the correct side of entry for the direction
      - 'stop hit' means exit == stop
      - result matches (entry-exit)*n for a short, (exit-entry)*n
        for a long, within a tolerance
      - result's sign matches the price move
    """
    try:
        d = (r.get("direction") or "").strip().lower()
        e = float(r.get("entry"))
        s = float(r.get("stop"))
        x = float(r.get("exit"))
        n = int(r.get("contracts") or 0)
        res = float(r.get("result"))
    except (TypeError, ValueError):
        return False, "missing or non-numeric price field"

    if n <= 0:
        return False, "non-positive contracts"
    if d == "short" and s <= e:
        return False, f"short stop {s} not above entry {e}"
    if d == "long" and s >= e:
        return False, f"long stop {s} not below entry {e}"

    reason = (r.get("exit_reason") or "").strip().lower()
    if reason == "stop hit" and abs(x - s) > 1e-6:
        return False, f"'stop hit' but exit {x} != stop {s}"

    # Futures P&L is per-point x contracts x multiplier. Without
    # the multiplier every futures result looks wrong by 50-1250x.
    _MULT = {"ES": 50, "MES": 5, "CL": 1000, "MCL": 100,
             "GC": 100, "ZB": 1000, "6E": 125000, "MNQ": 2,
             "NQ": 20}
    mult = _MULT.get((r.get("instrument") or "").strip().upper(), 1)
    pnl_per_unit = (e - x) if d == "short" else (x - e)
    computed = pnl_per_unit * n * mult
    if abs(computed - res) > max(1.0, abs(res) * 0.01):
        return False, (
            f"result {res} does not match the prices "
            f"(computed {computed:.2f})"
        )
    if computed * res < 0:
        return False, "result sign contradicts the price move"
    return True, ""


async def generate_psychology_batch(
    session: AsyncSession,
    *,
    org_id: str,
    coord: str,
) -> str:
    """Generate a batch of BATCH_SIZE rows. Returns the batch_id."""
    trades = await _call_model_for_batch(
        session, org_id=org_id, coord=coord, n=BATCH_SIZE,
    )
    batch_id = str(uuid.uuid4())

    inserted = 0
    for t in trades:
        try:
            await session.execute(sql_text("""
                INSERT INTO psychology_trade_log (
                    org_id, coord, batch_id, reading_key, narrative,
                    traded_at, instrument, direction, contracts,
                    entry, stop, target, exit,
                    exit_reason, result, hold_time
                ) VALUES (
                    :org_id, :coord, :batch_id, :reading_key, :narrative,
                    :traded_at, :instrument, :direction, :contracts,
                    :entry, :stop, :target, :exit,
                    :exit_reason, :result, :hold_time
                )
            """), {
                "org_id": org_id,
                "coord": coord,
                "batch_id": batch_id,
                "reading_key": str(t.get("reading_key") or "unknown")[:64],
                "narrative": str(t.get("narrative") or "")[:2000],
                "traded_at": t["traded_at"],  # datetime (tz-aware)
                "instrument": str(t.get("instrument") or "ES")[:8],
                "direction": str(t.get("direction") or "long")[:8],
                "contracts": int(t.get("contracts") or 1),
                "entry": float(t["entry"]) if t.get("entry") is not None else None,
                "stop": float(t["stop"]) if t.get("stop") is not None else None,
                "target": float(t["target"]) if t.get("target") is not None else None,
                "exit": float(t["exit"]) if t.get("exit") is not None else None,
                "exit_reason": str(t.get("exit_reason") or "?")[:128],
                "result": float(t.get("result") or 0),
                "hold_time": str(t.get("hold_time") or "?")[:32],
            })
            inserted += 1
        except Exception as exc:
            log.warning(
                "psychology_log.insert_failed",
                coord=coord, error=str(exc)[:200],
            )

    await session.commit()
    log.info(
        "psychology_log.batch_generated",
        coord=coord, batch_id=batch_id[:8], inserted=inserted,
    )
    return batch_id


# ─────────────────────────────────────────────────────────────────────
# Read paths
# ─────────────────────────────────────────────────────────────────────

async def count_unused(
    session: AsyncSession, *, org_id: str, coord: str,
) -> int:
    return (await session.execute(sql_text("""
        SELECT count(*) FROM psychology_trade_log
        WHERE org_id = :oid
          AND coord = :coord
          AND deleted_at IS NULL
          AND used_in_article_id IS NULL
    """), {"oid": org_id, "coord": coord})).scalar() or 0


async def read_next_unused(
    session: AsyncSession, *, org_id: str, coord: str,
) -> dict[str, Any] | None:
    """Read the next unused row. Generates a batch if the pool is low.

    Returns None only when the pool is empty AND generation failed.
    """
    unused = await count_unused(session, org_id=org_id, coord=coord)
    if unused <= REFILL_THRESHOLD:
        log.info(
            "psychology_log.refilling",
            coord=coord, unused=unused,
        )
        await generate_psychology_batch(session, org_id=org_id, coord=coord)

    row = (await session.execute(sql_text("""
        SELECT id, narrative, reading_key,
               traded_at, instrument, direction, contracts,
               entry, stop, target, exit,
               exit_reason, result, hold_time
        FROM psychology_trade_log
        WHERE org_id = :oid
          AND coord = :coord
          AND deleted_at IS NULL
          AND used_in_article_id IS NULL
        ORDER BY traded_at ASC
        LIMIT 1
    """), {"oid": org_id, "coord": coord})).mappings().first()

    return dict(row) if row else None


async def read_all_for_coord(
    session: AsyncSession, *, org_id: str, coord: str,
) -> list[dict[str, Any]]:
    """Read every live row for a coordinate (used + unused) for readings."""
    rows = (await session.execute(sql_text("""
        SELECT id, narrative, reading_key, traded_at, instrument,
               direction, contracts, result, hold_time, exit_reason,
               used_in_article_id
        FROM psychology_trade_log
        WHERE org_id = :oid
          AND coord = :coord
          AND deleted_at IS NULL
        ORDER BY traded_at DESC
        LIMIT 60
    """), {"oid": org_id, "coord": coord})).mappings().all()
    return [dict(r) for r in rows]


async def mark_used(
    session: AsyncSession, *, row_id: str, post_id: str,
) -> None:
    """Mark a row as consumed by an article. Never re-read after this."""
    await session.execute(sql_text("""
        UPDATE psychology_trade_log
        SET used_in_article_id = :pid,
            used_at = now()
        WHERE id = :rid
          AND used_in_article_id IS NULL
    """), {"pid": post_id, "rid": row_id})
    await session.commit()
