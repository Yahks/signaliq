"""Trader process API — one document per org, drafts as full documents."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from apps.api.schemas.trader_process import (
    SECTIONS,
    DraftListItem,
    DraftResponse,
    ProcessDocument,
    ProcessResponse,
)
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/trader-process", tags=["trader-process"])

_COLS = ", ".join(SECTIONS)


async def _scoped(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.get("", response_model=ProcessResponse)
async def get_process(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    # Debug: log the RLS setting and the row count.
    import logging
    _log = logging.getLogger(__name__)
    setting = (await session.execute(sql_text(
        "SELECT current_setting('app.current_org_id', true)"
    ))).scalar()
    count = (await session.execute(sql_text(
        "SELECT COUNT(*) FROM trader_process WHERE org_id = :oid"
    ), {"oid": org_id})).scalar()
    _log.info("DEBUG get_process org_id=%s setting=%s count=%s",
              org_id, setting, count)
    row = (await session.execute(sql_text(f"""
        SELECT {_COLS}, updated_at
        FROM trader_process WHERE org_id = :oid AND deleted_at IS NULL LIMIT 1
    """), {"oid": org_id})).first()
    if not row:
        return ProcessResponse()
    d = dict(zip(SECTIONS, row[:-1]))
    return ProcessResponse(**d, updated_at=row[-1])


@router.put("", response_model=ProcessResponse)
async def save_process(
    payload: ProcessDocument,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    cols = ", ".join(f"{s} = EXCLUDED.{s}" for s in SECTIONS)
    vals = ", ".join(f":{s}" for s in SECTIONS)
    # 1. Write the live row (upsert, one per org).
    await session.execute(sql_text(f"""
        INSERT INTO trader_process (org_id, {_COLS})
        VALUES (:oid, {vals})
        ON CONFLICT (org_id) DO UPDATE SET {cols}, updated_at = now(), deleted_at = NULL
    """), {"oid": org_id, **payload.model_dump()})

    # 2. Append the same content to the history table.
    #    Every Save keeps a version. The article generator reads
    #    the current row plus the last N history rows, so it can
    #    quote the trader's evolution, not just the snapshot.
    await session.execute(sql_text(f"""
        INSERT INTO trader_process_history (org_id, {_COLS})
        VALUES (:oid, {vals})
    """), {"oid": org_id, **payload.model_dump()})

    await session.commit()
    return await get_process(session=session, org_id=org_id)


@router.post("/generate", response_model=ProcessResponse)
async def generate_process(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    """Draft a process for this org on demand.

    Persists one draft row (so Process drafts shows it), and returns
    the drafted document. Does NOT write trader_process — the caller
    decides (typically: load into the composer, Save to commit).
    """
    from packages.trading.process_drafter import draft_for_org

    created = await draft_for_org(session, org_id, force=True)

    # Find the pending draft — the newly created one if created==1,
    # otherwise an existing pending one that blocked creation.
    row = (await session.execute(sql_text("""
        SELECT id::text FROM trader_process_drafts
        WHERE org_id = :oid AND reviewed_at IS NULL
        ORDER BY created_at DESC LIMIT 1
    """), {"oid": org_id})).first()

    if not row:
        raise HTTPException(
            status_code=503,
            detail="Drafting is not available for this organisation. "
                   "Check credentials and that the trading block is enabled.",
        )

    draft_id = row[0]
    return await get_draft(draft_id=draft_id, session=session, org_id=org_id)


@router.get("/history")
async def list_history(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> list[dict]:
    """Every accepted version of the trader's process."""
    rows = (await session.execute(sql_text("""
        SELECT id::text, created_at,
               COALESCE(NULLIF(LEFT(daily_scan, 200), ''), '') AS preview
        FROM trader_process_history
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


@router.get("/history/{doc_id}", response_model=ProcessResponse)
async def get_history(
    doc_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    """One accepted version by id."""
    row = (await session.execute(sql_text(f"""
        SELECT {_COLS}, created_at
        FROM trader_process_history
        WHERE id = :did AND org_id = :oid AND deleted_at IS NULL
        LIMIT 1
    """), {"did": doc_id, "oid": org_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Version not found")
    d = dict(zip(SECTIONS, row[:-1]))
    return ProcessResponse(**d, updated_at=row[-1])


@router.delete("/history/{doc_id}")
async def delete_history(
    doc_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> dict:
    """Soft-delete one accepted version."""
    await session.execute(sql_text("""
        UPDATE trader_process_history
        SET deleted_at = now()
        WHERE id = :did AND org_id = :oid AND deleted_at IS NULL
    """), {"did": doc_id, "oid": org_id})
    await session.commit()
    return {"removed": True}


@router.delete("")
async def delete_process(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> dict:
    """Soft-delete the trader's process. Moves it to trash."""
    await session.execute(sql_text("""
        UPDATE trader_process
        SET deleted_at = now()
        WHERE org_id = :oid AND deleted_at IS NULL
    """), {"oid": org_id})
    await session.commit()
    return {"removed": True}


@router.get("/list")
async def list_processes(
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> list[dict]:
    """List saved process documents for the org (alive rows only)."""
    rows = (await session.execute(sql_text("""
        SELECT id::text, updated_at,
               COALESCE(NULLIF(LEFT(daily_scan, 200), ''), '') AS preview
        FROM trader_process
        WHERE org_id = :oid AND deleted_at IS NULL
        ORDER BY updated_at DESC
    """), {"oid": org_id})).all()
    return [
        {
            "id": r[0],
            "updated_at": r[1].isoformat() if r[1] else None,
            "preview": r[2],
        }
        for r in rows
    ]


@router.get("/by-id/{doc_id}", response_model=ProcessResponse)
async def get_process_by_id(
    doc_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    """Fetch one saved document by id (alive rows only)."""
    row = (await session.execute(sql_text(f"""
        SELECT {_COLS}, updated_at
        FROM trader_process
        WHERE id = :did AND org_id = :oid AND deleted_at IS NULL
        LIMIT 1
    """), {"did": doc_id, "oid": org_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    d = dict(zip(SECTIONS, row[:len(SECTIONS)]))
    if "updated_at" in row._mapping:
        d["updated_at"] = row._mapping["updated_at"]
    return d


@router.get("/drafts", response_model=list[DraftListItem])
async def list_drafts(
    status: str = "pending",
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    """List drafts. status=pending (default) or status=accepted."""
    where = (
        "reviewed_at IS NULL" if status == "pending"
        else "reviewed_at IS NOT NULL"
    )
    rows = (await session.execute(sql_text(f"""
        SELECT id::text, created_at, reviewed_at, daily_scan
        FROM trader_process_drafts
        WHERE org_id = :oid AND {where}
        ORDER BY created_at DESC
        LIMIT 200
    """), {"oid": org_id})).all()
    return [
        DraftListItem(
            id=r[0], created_at=r[1], reviewed_at=r[2],
            preview=(r[3] or "")[:200],
        )
        for r in rows
    ]


@router.get("/drafts/{draft_id}", response_model=DraftResponse)
async def get_draft(
    draft_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    row = (await session.execute(sql_text(f"""
        SELECT id::text, {_COLS}, created_at, reviewed_at
        FROM trader_process_drafts
        WHERE id = :d AND org_id = :oid LIMIT 1
    """), {"d": draft_id, "oid": org_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")
    d = dict(zip(SECTIONS, row[1:-2]))
    return DraftResponse(**d, id=row[0], created_at=row[-2], reviewed_at=row[-1])


@router.post("/drafts/{draft_id}/accept", response_model=ProcessResponse)
async def accept_draft(
    draft_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> Any:
    row = (await session.execute(sql_text(f"""
        SELECT {_COLS}, reviewed_at FROM trader_process_drafts
        WHERE id = :d AND org_id = :oid LIMIT 1
    """), {"d": draft_id, "oid": org_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Draft not found")

    # S.9.11 — no-op if this draft was already accepted. Prevents
    # duplicate history rows when the trader clicks "Save as my
    # process" more than once on the same draft.
    if row[-1] is not None:
        log.info("trader_process.accept_idempotent",
                 draft_id=draft_id[:8])
        return await get_process(session=session, org_id=org_id)

    d = dict(zip(SECTIONS, row[:-1]))

    # 1. Write the live row (upsert, one per org).
    cols = ", ".join(f"{s} = EXCLUDED.{s}" for s in SECTIONS)
    vals = ", ".join(f":{s}" for s in SECTIONS)
    await session.execute(sql_text(f"""
        INSERT INTO trader_process (org_id, {_COLS})
        VALUES (:oid, {vals})
        ON CONFLICT (org_id) DO UPDATE SET {cols},
            updated_at = now(), deleted_at = NULL
    """), {"oid": org_id, **d})

    # 2. Append to the history table. Every accept is a version.
    await session.execute(sql_text(f"""
        INSERT INTO trader_process_history (org_id, {_COLS})
        VALUES (:oid, {vals})
    """), {"oid": org_id, **d})

    # 3. Mark the draft reviewed.
    await session.execute(sql_text("""
        UPDATE trader_process_drafts SET reviewed_at = now()
        WHERE id = :d AND org_id = :oid
    """), {"d": draft_id, "oid": org_id})

    await session.commit()
    return await get_draft(draft_id=draft_id, session=session, org_id=org_id)


@router.delete("/drafts/{draft_id}")
async def delete_draft(
    draft_id: str,
    session: AsyncSession = Depends(_scoped),
    org_id: str = Depends(current_org_id),
) -> dict[str, bool]:
    r = await session.execute(sql_text("""
        DELETE FROM trader_process_drafts
        WHERE id = :d AND org_id = :oid
    """), {"d": draft_id, "oid": org_id})
    await session.commit()
    return {"removed": (r.rowcount or 0) > 0}
