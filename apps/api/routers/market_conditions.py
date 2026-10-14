"""Market conditions — trader-filled state + model-drafted proposals.

Endpoints:
  GET  /api/market-conditions                       latest live row
  PUT  /api/market-conditions                       append a new row
  POST /api/market-conditions/generate              draft on demand
  GET  /api/market-conditions/drafts                list pending drafts
  GET  /api/market-conditions/drafts/{id}           one draft
  POST /api/market-conditions/drafts/{id}/accept    accept → live row
  DELETE /api/market-conditions/drafts/{id}         remove draft
"""
from __future__ import annotations
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, get_session
from apps.api.schemas.market_conditions import (
    MarketConditions,
    MarketConditionsResponse,
)
from packages.core.logging import get_logger
from packages.db.tenant import set_current_org

log = get_logger(__name__)
router = APIRouter(prefix="/api/market-conditions", tags=["market-conditions"])

COLS = (
    "what_i_track", "baselines", "regimes",
    "thresholds", "adjustments", "mechanisms",
)
_COLS = ", ".join(COLS)


async def _scoped(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


async def _get(session: AsyncSession, org_id: str) -> MarketConditionsResponse:
    row = (await session.execute(sql_text("""
        SELECT what_i_track, baselines, regimes,
               thresholds, adjustments, mechanisms, created_at
        FROM market_conditions
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 1
    """), {"oid": org_id})).first()
    if not row:
        return MarketConditionsResponse()
    d = dict(zip(COLS, row[:6]))
    return MarketConditionsResponse(**d, updated_at=row[6])


@router.get("", response_model=MarketConditionsResponse)
async def get_conditions(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    return await _get(session, org_id)


@router.put("", response_model=MarketConditionsResponse)
async def save_conditions(
    payload: MarketConditions,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    # S.9.12 — idempotent. If the latest live row already has the
    # exact same content, no-op. Prevents duplicate history rows
    # from a double-click on Save.
    existing = (await session.execute(sql_text(f"""
        SELECT {_COLS}
        FROM market_conditions
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 1
    """), {"oid": org_id})).first()

    if existing:
        same = all(
            (existing[i] or "").strip() == (payload.model_dump()[c] or "").strip()
            for i, c in enumerate(COLS)
        )
        if same:
            log.info("market_conditions.no_op_save", org_id=org_id[:8])
            return await _get(session, org_id)

    cols = ", ".join(COLS)
    vals = ", ".join(f":{c}" for c in COLS)
    await session.execute(sql_text(f"""
        INSERT INTO market_conditions (org_id, {cols})
        VALUES (:oid, {vals})
    """), {"oid": org_id, **payload.model_dump()})

    await session.commit()
    return await _get(session, org_id)


@router.post("/generate", response_model=MarketConditionsResponse)
async def generate_conditions(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    """Draft a market-conditions block on demand.

    Persists one draft row (so Process drafts shows it), returns
    the drafted content. Does NOT write market_conditions — the
    caller decides (typically: load into the composer, Save to
    commit).
    """
    from packages.trading.market_conditions_drafter import draft_for_org

    created = await draft_for_org(session, org_id, force=True)

    row = (await session.execute(sql_text("""
        SELECT id::text FROM market_conditions_drafts
        WHERE org_id = :oid AND reviewed_at IS NULL
        ORDER BY created_at DESC LIMIT 1
    """), {"oid": org_id})).first()

    if not row:
        raise HTTPException(
            status_code=503,
            detail="Drafting is not available for this organisation.",
        )
    draft_id = row[0]
    return await _get_draft(session, org_id, draft_id)


async def _get_draft(
    session: AsyncSession, org_id: str, draft_id: str,
) -> MarketConditionsResponse:
    row = (await session.execute(sql_text(f"""
        SELECT {_COLS}, created_at, reviewed_at
        FROM market_conditions_drafts
        WHERE id = :d AND org_id = :oid LIMIT 1
    """), {"d": draft_id, "oid": org_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")
    d = dict(zip(COLS, row[:6]))
    return MarketConditionsResponse(**d, updated_at=row[6])


@router.get("/drafts")
async def list_drafts(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> list[dict]:
    rows = (await session.execute(sql_text("""
        SELECT id::text, created_at, reviewed_at,
               COALESCE(NULLIF(LEFT(what_i_track, 200), ''), '') AS preview
        FROM market_conditions_drafts
        WHERE org_id = :oid
        ORDER BY created_at DESC LIMIT 100
    """), {"oid": org_id})).all()
    return [
        {
            "id": r[0],
            "created_at": r[1].isoformat() if r[1] else None,
            "reviewed_at": r[2].isoformat() if r[2] else None,
            "preview": r[3],
        }
        for r in rows
    ]


@router.get("/drafts/{draft_id}", response_model=MarketConditionsResponse)
async def get_draft(
    draft_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    return await _get_draft(session, org_id, draft_id)


@router.post("/drafts/{draft_id}/accept",
             response_model=MarketConditionsResponse)
async def accept_draft(
    draft_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    row = (await session.execute(sql_text(f"""
        SELECT {_COLS}, reviewed_at FROM market_conditions_drafts
        WHERE id = :d AND org_id = :oid LIMIT 1
    """), {"d": draft_id, "oid": org_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")
    if row[-1] is not None:
        # Already accepted — idempotent.
        return await _get(session, org_id)

    d = dict(zip(COLS, row[:-1]))
    vals = ", ".join(f":{c}" for c in COLS)
    await session.execute(sql_text(f"""
        INSERT INTO market_conditions (org_id, {_COLS})
        VALUES (:oid, {vals})
    """), {"oid": org_id, **d})

    await session.execute(sql_text("""
        UPDATE market_conditions_drafts SET reviewed_at = now()
        WHERE id = :d AND org_id = :oid
    """), {"d": draft_id, "oid": org_id})
    await session.commit()
    return await _get(session, org_id)


@router.delete("/drafts/{draft_id}")
async def delete_draft(
    draft_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> dict:
    await session.execute(sql_text("""
        DELETE FROM market_conditions_drafts
        WHERE id = :d AND org_id = :oid
    """), {"d": draft_id, "oid": org_id})
    await session.commit()
    return {"removed": True}

# ── Appended: list / by-id / delete for the saved list page ─────


@router.get("/list")
async def list_conditions(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> list[dict]:
    """Every live saved market-conditions row, newest first."""
    rows = (await session.execute(sql_text("""
        SELECT id::text, created_at,
               COALESCE(NULLIF(LEFT(what_i_track, 200), ''), '') AS preview
        FROM market_conditions
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT 200
    """), {"oid": org_id})).all()
    return [
        {
            "id": r[0],
            "updated_at": r[1].isoformat() if r[1] else None,
            "preview": r[2],
        }
        for r in rows
    ]


@router.get("/by-id/{doc_id}", response_model=MarketConditionsResponse)
async def get_condition_by_id(
    doc_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    row = (await session.execute(sql_text(f"""
        SELECT {_COLS}, created_at
        FROM market_conditions
        WHERE id = :did AND org_id = :oid AND deleted_at IS NULL
        LIMIT 1
    """), {"did": doc_id, "oid": org_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    d = dict(zip(COLS, row[:6]))
    return MarketConditionsResponse(**d, updated_at=row[6])


@router.delete("/by-id/{doc_id}")
async def delete_condition_by_id(
    doc_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> dict:
    await session.execute(sql_text("""
        UPDATE market_conditions SET deleted_at = now()
        WHERE id = :did AND org_id = :oid AND deleted_at IS NULL
    """), {"did": doc_id, "oid": org_id})
    await session.commit()
    return {"removed": True}

