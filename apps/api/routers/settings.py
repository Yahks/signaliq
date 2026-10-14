"""Settings endpoints — per-block automation toggles."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id, require_role
from apps.api.schemas.settings import (
    BlockSettingsItem,
    BlockSettingsListResponse,
    EditorialUpdateRequest,
    UpdateBlockSettingsRequest,
)
from packages.core.logging import get_logger
from packages.db.session import get_session
from packages.db.tenant import set_current_org
from packages.trading.editorial import VOICES, VOICE_NAMES, DEFAULT_VOICE

log = get_logger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# S.7.4 — blocks are read from the blocks registry, not hardcoded.
# The picker (S.7.5) shows blocks the same way, so there is one source
# of truth: the blocks table.


async def _scoped(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.get("/blocks", response_model=BlockSettingsListResponse)
async def list_block_settings(
    session: AsyncSession = Depends(_scoped),
) -> BlockSettingsListResponse:
    """Current auto_enabled state for every known block."""
    # S.7.4 — read the block registry instead of a hardcoded list.
    block_rows = (await session.execute(sql_text("""
        SELECT slug, name FROM blocks
        WHERE is_active = true
        ORDER BY slug
    """))).all()

    rows = (await session.execute(sql_text("""
        SELECT block_slug, auto_enabled, updated_at
        FROM org_block_settings
    """))).all()
    by_slug = {r[0]: r for r in rows}

    items = []
    for slug, name in block_rows:
        r = by_slug.get(slug)
        items.append(BlockSettingsItem(
            slug=slug,
            name=name,
            auto_enabled=bool(r[1]) if r else False,
            updated_at=r[2] if r else None,
        ))
    return BlockSettingsListResponse(items=items)


@router.patch("/blocks/{slug}", response_model=BlockSettingsItem)
async def update_block_settings(
    slug: str,
    body: UpdateBlockSettingsRequest,
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped),
) -> BlockSettingsItem:
    """Flip auto-generation for one block. Owner/admin only."""
    from fastapi import HTTPException
    # S.7.4 — validate against the registry, not a hardcoded list.
    known_row = (await session.execute(sql_text(
        "SELECT name FROM blocks WHERE slug = :s AND is_active = true"
    ), {"s": slug})).first()
    if known_row is None:
        raise HTTPException(status_code=404, detail=f"Unknown block: {slug}")

    await session.execute(sql_text("""
        INSERT INTO org_block_settings (org_id, block_slug, auto_enabled)
        VALUES (:oid, :slug, :enabled)
        ON CONFLICT (org_id, block_slug) DO UPDATE
        SET auto_enabled = EXCLUDED.auto_enabled,
            updated_at = now()
    """), {"oid": org_id, "slug": slug, "enabled": body.auto_enabled})

    row = (await session.execute(sql_text("""
        SELECT block_slug, auto_enabled, updated_at
        FROM org_block_settings
        WHERE org_id = :oid AND block_slug = :slug
    """), {"oid": org_id, "slug": slug})).first()

    await session.commit()

    return BlockSettingsItem(
        slug=row[0], name=known[slug],
        auto_enabled=bool(row[1]), updated_at=row[2],
    )


# ─────────────────────────────────────────────────────────────────────
# S.7.8b-2 — editorial voice
# ─────────────────────────────────────────────────────────────────────


@router.get("/editorial")
async def get_editorial(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped),
) -> dict:
    """Return this org's editorial voice and the list of available voices.

    Each available voice carries its full directive prose so the UI can
    show the model's actual instructions on each card without duplicating
    the text on the frontend.
    """
    row = (await session.execute(sql_text("""
        SELECT settings->'editorial'->>'voice' AS voice
        FROM orgs WHERE id = :oid
    """), {"oid": org_id})).first()
    current = (row[0] if row and row[0] else DEFAULT_VOICE)

    return {
        "voice": current,
        "default": DEFAULT_VOICE,
        "available": [
            {"name": name, "directive": VOICES[name]}
            for name in VOICE_NAMES
        ],
    }


@router.put("/editorial")
async def set_editorial(
    body: EditorialUpdateRequest,
    org_id: str = Depends(current_org_id),
    _role: str = Depends(require_role("owner", "admin")),
    session: AsyncSession = Depends(_scoped),
) -> dict:
    """Set this org's editorial voice. Owner/admin only.

    Writes to orgs.settings->'editorial'->'voice'. jsonb_set creates
    the intermediate 'editorial' object if it does not exist.
    """
    if body.voice not in VOICES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown voice: {body.voice!r}. "
                   f"Available: {', '.join(VOICE_NAMES)}",
        )

    # Use `||` merge rather than jsonb_set so we don't trip SQLAlchemy's
    # text() parser on the `::text` cast (it reads `:v::text` as a bind
    # name and leaves the colon literal — see the S.7.8b-2 traceback).
    # CAST(:v AS text) with a space parses cleanly.
    import json as _json
    await session.execute(sql_text("""
        UPDATE orgs
        SET settings = coalesce(settings, '{}'::jsonb)
                       || CAST(:patch AS jsonb),
            updated_at = now()
        WHERE id = :oid
    """), {
        "oid": org_id,
        "patch": _json.dumps({"editorial": {"voice": body.voice}}),
    })
    await session.commit()

    log.info("editorial.set", org_id=org_id[:8], voice=body.voice)
    return {"voice": body.voice}
