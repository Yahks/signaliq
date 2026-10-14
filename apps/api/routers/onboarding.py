"""Onboarding endpoints: state, acknowledge, select-blocks.

State machine:
  onboarding_guide_seen_at IS NULL AND onboarding_completed_at IS NULL
    → brand-new org. Show the guide, then the picker.
  guide_seen_at IS NOT NULL AND completed_at IS NULL
    → guide acknowledged. Show the picker.
  completed_at IS NOT NULL
    → fully onboarded. Dashboard.

These endpoints all run BEFORE the picker has written any
org_block_settings rows, so they must handle an empty set correctly.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from apps.api.schemas.onboarding import (
    AcknowledgeResponse,
    BlockOption,
    OnboardingStateResponse,
    SelectBlocksRequest,
    SelectBlocksResponse,
)
from packages.core.blocks import (
    MAX_BLOCKS_PER_ORG,
    MIN_BLOCKS_AT_SIGNUP,
    ONBOARDABLE_BLOCKS,
)
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


async def _scoped(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


async def _load_available_blocks(session: AsyncSession) -> list[BlockOption]:
    """Available blocks = ONBOARDABLE_BLOCKS, with name/description from
    the blocks table. Any onboardable slug without a blocks row is a
    data integrity error — fail loudly rather than silently omit."""
    rows = (await session.execute(sql_text("""
        SELECT slug, name, description
        FROM blocks
        WHERE slug = ANY(:slugs)
    """), {"slugs": list(ONBOARDABLE_BLOCKS)})).all()
    by_slug = {r[0]: (r[1], r[2]) for r in rows}

    missing = [s for s in ONBOARDABLE_BLOCKS if s not in by_slug]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Onboardable blocks missing from registry: {missing}",
        )

    return [
        BlockOption(
            slug=s,
            name=by_slug[s][0],
            description=by_slug[s][1],
        )
        for s in ONBOARDABLE_BLOCKS
    ]


@router.get("/state", response_model=OnboardingStateResponse)
async def get_state(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped),
) -> OnboardingStateResponse:
    """Everything the frontend needs to decide which screen to show."""
    row = (await session.execute(sql_text("""
        SELECT onboarding_guide_seen_at, onboarding_completed_at
        FROM orgs WHERE id = :oid
    """), {"oid": org_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Org not found")

    guide_seen = row[0] is not None
    completed = row[1] is not None

    selected = [
        r[0] for r in (await session.execute(sql_text("""
            SELECT block_slug FROM org_block_settings
            WHERE org_id = :oid AND auto_enabled = true
            ORDER BY block_slug
        """), {"oid": org_id})).all()
    ]

    available = await _load_available_blocks(session)

    return OnboardingStateResponse(
        guide_seen=guide_seen,
        completed=completed,
        selected_blocks=selected,
        available_blocks=available,
    )


@router.post("/acknowledge", response_model=AcknowledgeResponse)
async def acknowledge(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped),
) -> AcknowledgeResponse:
    """Mark the guide as seen. Idempotent if not completed; 409 if the
    user has already finished onboarding."""
    row = (await session.execute(sql_text("""
        SELECT onboarding_completed_at FROM orgs WHERE id = :oid
    """), {"oid": org_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Org not found")
    if row[0] is not None:
        raise HTTPException(status_code=409, detail="Onboarding already completed")

    await session.execute(sql_text("""
        UPDATE orgs
        SET onboarding_guide_seen_at = COALESCE(onboarding_guide_seen_at, :now)
        WHERE id = :oid
    """), {"now": datetime.now(UTC), "oid": org_id})
    await session.commit()
    return AcknowledgeResponse(ok=True, completed=False)


@router.post("/select-blocks", response_model=SelectBlocksResponse)
async def select_blocks(
    body: SelectBlocksRequest,
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped),
) -> SelectBlocksResponse:
    """Write the chosen blocks. Only valid at signup — the picker is
    the ONLY caller. Changing blocks later uses a different endpoint."""
    # Idempotency guard
    row = (await session.execute(sql_text("""
        SELECT onboarding_completed_at FROM orgs WHERE id = :oid
    """), {"oid": org_id})).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Org not found")
    if row[0] is not None:
        raise HTTPException(status_code=409, detail="Onboarding already completed")

    chosen = list(body.blocks)

    # Dedupe preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for s in chosen:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    chosen = deduped

    if len(chosen) < MIN_BLOCKS_AT_SIGNUP:
        raise HTTPException(
            status_code=400,
            detail=f"Pick at least {MIN_BLOCKS_AT_SIGNUP} block.",
        )
    if len(chosen) > MAX_BLOCKS_PER_ORG:
        raise HTTPException(
            status_code=400,
            detail=f"Pick at most {MAX_BLOCKS_PER_ORG} blocks.",
        )
    bad = [s for s in chosen if s not in ONBOARDABLE_BLOCKS]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown block(s): {bad}. Available: {list(ONBOARDABLE_BLOCKS)}",
        )

    for slug in chosen:
        await session.execute(sql_text("""
            INSERT INTO org_block_settings (org_id, block_slug, auto_enabled)
            VALUES (:oid, :slug, true)
            ON CONFLICT (org_id, block_slug) DO UPDATE
            SET auto_enabled = true, updated_at = now()
        """), {"oid": org_id, "slug": slug})

    await session.execute(sql_text("""
        UPDATE orgs
        SET onboarding_completed_at = now(),
            onboarding_guide_seen_at = COALESCE(onboarding_guide_seen_at, now())
        WHERE id = :oid
    """), {"oid": org_id})
    await session.commit()

    return SelectBlocksResponse(ok=True, selected_blocks=chosen)
