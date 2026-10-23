"""Daily snapshot of the trader's market_conditions readings.

The trader's readings are entered, not computed, so the table has
no time series — six rows over one week. Regime persistence and
conditional expectancy both need one: "correlation above 0.50 for
N days" requires N days of rows.

This job copies the org's most recent trader-entered row forward
once per day, with created_at=now() and snapshot_of set to the
source row. It never edits or deletes the source; it only adds a
dated copy. If a snapshot already exists for today, it does nothing.

Called from the scheduler loop; safe to call repeatedly.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


async def snapshot_conditions(
    session: AsyncSession, org_id: str,
) -> dict[str, str]:
    """Insert one snapshot per org per day. Returns a status dict."""
    today = date.today()
    # already snapped today?
    exists = (await session.execute(sql_text("""
        SELECT 1 FROM market_conditions
        WHERE org_id = :oid AND deleted_at IS NULL
          AND snapshot_of IS NOT NULL
          AND created_at::date = :today
        LIMIT 1
    """), {"oid": org_id, "today": today})).first()
    if exists:
        return {"status": "already_snapshot_today"}

    # the most recent trader-entered row (snapshot_of IS NULL)
    src = (await session.execute(sql_text("""
        SELECT id, what_i_track, baselines, regimes, thresholds,
               adjustments, mechanisms, realized_vol_current,
               spread_current, range_current, correlation_current,
               breadth_current, metric_source, rule_version
        FROM market_conditions
        WHERE org_id = :oid AND deleted_at IS NULL
          AND snapshot_of IS NULL
        ORDER BY created_at DESC
        LIMIT 1
    """), {"oid": org_id})).mappings().first()
    if not src:
        return {"status": "no_source_row"}
    if not (src["what_i_track"] or "").strip():
        return {"status": "source_row_empty"}

    await session.execute(sql_text("""
        INSERT INTO market_conditions (
            org_id, what_i_track, baselines, regimes, thresholds,
            adjustments, mechanisms, realized_vol_current,
            spread_current, range_current, correlation_current,
            breadth_current, metric_source, rule_version,
            snapshot_of, created_at
        ) VALUES (
            :oid, :track, :base, :reg, :thr, :adj, :mech,
            :vol, :spread, :range, :corr, :breadth,
            :msrc, :rv, :snap_of, now()
        )
    """), {
        "oid": org_id,
        "track": src["what_i_track"], "base": src["baselines"],
        "reg": src["regimes"], "thr": src["thresholds"],
        "adj": src["adjustments"], "mech": src["mechanisms"],
        "vol": src["realized_vol_current"], "spread": src["spread_current"],
        "range": src["range_current"], "corr": src["correlation_current"],
        "breadth": src["breadth_current"], "msrc": src["metric_source"],
        "rv": src["rule_version"], "snap_of": src["id"],
    })
    await session.commit()
    _src_id = str(src["id"])
    log.info("conditions.snapshot", org_id=str(org_id)[:8],
             source=_src_id[:8])
    return {"status": "snapshot_inserted", "source_id": _src_id}
