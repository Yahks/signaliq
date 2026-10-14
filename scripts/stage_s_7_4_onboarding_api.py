"""S.7.4 — onboarding API.

Three endpoints:
  GET  /api/onboarding/state
  POST /api/onboarding/acknowledge
  POST /api/onboarding/select-blocks

Plus: TokenResponse gains onboarding_required so the frontend can
route new users to the guide screen without an extra round-trip.

Also refactors settings.py's KNOWN_BLOCKS (a hardcoded duplicate of
the blocks registry) to query blocks instead — one source of truth.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ONBOARDING_ROUTER = ROOT / "apps" / "api" / "routers" / "onboarding.py"
ONBOARDING_SCHEMA = ROOT / "apps" / "api" / "schemas" / "onboarding.py"
AUTH_SCHEMA = ROOT / "apps" / "api" / "schemas" / "auth.py"
AUTH_ROUTER = ROOT / "apps" / "api" / "routers" / "auth.py"
MAIN = ROOT / "apps" / "api" / "main.py"
SETTINGS_ROUTER = ROOT / "apps" / "api" / "routers" / "settings.py"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def apply_once(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src:
        print(f"  ↷ {label}: already applied")
        return
    if old not in src:
        fail(f"{label}: anchor not found in {path.name}\n"
             f"--- expected (first 400) ---\n{old[:400]}")
    path.write_text(src.replace(old, new, 1))
    try:
        ast.parse(path.read_text())
    except SyntaxError as e:
        fail(f"after '{label}': {e}")
    print(f"  ✓ {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. The onboarding schema
# ─────────────────────────────────────────────────────────────────────

ONBOARDING_SCHEMA_PY = '''"""Onboarding API schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field


class BlockOption(BaseModel):
    """A block the picker can show. Name/description come from the
    blocks registry so the frontend never hardcodes them."""
    slug: str
    name: str
    description: str | None = None


class OnboardingStateResponse(BaseModel):
    guide_seen: bool
    completed: bool
    selected_blocks: list[str]
    available_blocks: list[BlockOption]


class AcknowledgeResponse(BaseModel):
    ok: bool
    completed: bool


class SelectBlocksRequest(BaseModel):
    blocks: list[str] = Field(..., min_length=1, max_length=2)


class SelectBlocksResponse(BaseModel):
    ok: bool
    selected_blocks: list[str]
'''


def step1_schema() -> None:
    print("\n[1/6] apps/api/schemas/onboarding.py")
    if ONBOARDING_SCHEMA.exists():
        print("  ↷ already exists")
        return
    ONBOARDING_SCHEMA.write_text(ONBOARDING_SCHEMA_PY)
    try:
        ast.parse(ONBOARDING_SCHEMA.read_text())
    except SyntaxError as e:
        fail(f"onboarding.py schema syntax: {e}")
    print("  ✓ created")


# ─────────────────────────────────────────────────────────────────────
# 2. The onboarding router
# ─────────────────────────────────────────────────────────────────────

ONBOARDING_ROUTER_PY = '''"""Onboarding endpoints: state, acknowledge, select-blocks.

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
'''


def step2_router() -> None:
    print("\n[2/6] apps/api/routers/onboarding.py")
    if ONBOARDING_ROUTER.exists():
        print("  ↷ already exists")
        return
    ONBOARDING_ROUTER.write_text(ONBOARDING_ROUTER_PY)
    try:
        ast.parse(ONBOARDING_ROUTER.read_text())
    except SyntaxError as e:
        fail(f"onboarding.py router syntax: {e}")
    print("  ✓ created")


# ─────────────────────────────────────────────────────────────────────
# 3. TokenResponse — onboarding_required
# ─────────────────────────────────────────────────────────────────────

def step3_token_response() -> None:
    print("\n[3/6] schemas/auth.py — TokenResponse")
    src = AUTH_SCHEMA.read_text()
    if "onboarding_required" in src:
        print("  ↷ already applied")
        return
    # Find the TokenResponse class and add the field.
    import re
    m = re.search(
        r"class TokenResponse\(BaseModel\):(.*?)(?=\nclass |\Z)",
        src, re.DOTALL,
    )
    if not m:
        fail("TokenResponse class not found in schemas/auth.py")
    body = m.group(1)
    # Insert the field after the last line of the class.
    # Trim trailing blank line from the match, keep it.
    new_body = body.rstrip("\n") + (
        "\n    # S.7.4 — true when the org has not completed onboarding.\n"
        "    onboarding_required: bool = False\n"
    )
    src = src[:m.start()] + f"class TokenResponse(BaseModel):{new_body}" + src[m.end():]
    AUTH_SCHEMA.write_text(src)
    try:
        ast.parse(src)
    except SyntaxError as e:
        fail(f"auth schema syntax after TokenResponse change: {e}")
    print("  ✓ added onboarding_required field")


# ─────────────────────────────────────────────────────────────────────
# 4. auth.py — populate the field on register + login
# ─────────────────────────────────────────────────────────────────────

def step4_auth_populate() -> None:
    print("\n[4/6] auth.py — populate onboarding_required")

    # register: the org was just created with NULL onboarding_completed_at,
    # so it's always required.
    apply_once(
        AUTH_ROUTER,
        '''    settings = get_settings()
    access = create_access_token(user_id=user_id, org_id=org_id, role="owner")
    refresh = create_refresh_token(user_id=user_id)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_ttl_min * 60,
    )


@router.post("/login", response_model=TokenResponse)''',
        '''    settings = get_settings()
    access = create_access_token(user_id=user_id, org_id=org_id, role="owner")
    refresh = create_refresh_token(user_id=user_id)
    # S.7.4 — a fresh org has onboarding_completed_at = NULL, so this
    # is always True on register. The frontend uses it to route to the
    # guide screen immediately.
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_ttl_min * 60,
        onboarding_required=True,
    )


@router.post("/login", response_model=TokenResponse)''',
        "register: onboarding_required=True",
    )

    # login: read the org's onboarding_completed_at.
    apply_once(
        AUTH_ROUTER,
        '''    factory = get_owner_session_factory()
    async with factory() as session:
        user = await _load_user_by_identifier(session, body.identifier)
        if not user or not verify_password(body.password, user.password_hash):
            raise AuthError("Invalid email or password")

        membership = await _load_primary_membership(session, user.id)
        org_id = membership.org_id if membership else None
        role = membership.role if membership else None
        user_id = user.id

    settings = get_settings()
    access = create_access_token(user_id=user_id, org_id=org_id, role=role)
    refresh = create_refresh_token(user_id=user_id)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_ttl_min * 60,
    )''',
        '''    factory = get_owner_session_factory()
    async with factory() as session:
        user = await _load_user_by_identifier(session, body.identifier)
        if not user or not verify_password(body.password, user.password_hash):
            raise AuthError("Invalid email or password")

        membership = await _load_primary_membership(session, user.id)
        org_id = membership.org_id if membership else None
        role = membership.role if membership else None
        user_id = user.id

        # S.7.4 — read onboarding state so the login response can tell
        # the frontend where to route.
        onboarding_required = True
        if org_id:
            row = (await session.execute(select(Org.onboarding_completed_at).where(Org.id == org_id))).first()
            onboarding_required = row is None or row[0] is None

    settings = get_settings()
    access = create_access_token(user_id=user_id, org_id=org_id, role=role)
    refresh = create_refresh_token(user_id=user_id)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_ttl_min * 60,
        onboarding_required=onboarding_required,
    )''',
        "login: onboarding_required from org state",
    )

    # refresh: same field — the token response shape is shared.
    apply_once(
        AUTH_ROUTER,
        '''    factory = get_owner_session_factory()
    async with factory() as session:
        membership = await _load_primary_membership(session, user_id)
        org_id = membership.org_id if membership else None
        role = membership.role if membership else None

    settings = get_settings()
    access = create_access_token(user_id=user_id, org_id=org_id, role=role)
    new_refresh = create_refresh_token(user_id=user_id)
    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        expires_in=settings.jwt_access_ttl_min * 60,
    )''',
        '''    factory = get_owner_session_factory()
    async with factory() as session:
        membership = await _load_primary_membership(session, user_id)
        org_id = membership.org_id if membership else None
        role = membership.role if membership else None

        # S.7.4 — refresh preserves the onboarding flag.
        onboarding_required = True
        if org_id:
            row = (await session.execute(select(Org.onboarding_completed_at).where(Org.id == org_id))).first()
            onboarding_required = row is None or row[0] is None

    settings = get_settings()
    access = create_access_token(user_id=user_id, org_id=org_id, role=role)
    new_refresh = create_refresh_token(user_id=user_id)
    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        expires_in=settings.jwt_access_ttl_min * 60,
        onboarding_required=onboarding_required,
    )''',
        "refresh: onboarding_required preserved",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. main.py — mount the router
# ─────────────────────────────────────────────────────────────────────

def step5_mount() -> None:
    print("\n[5/6] main.py — mount the onboarding router")
    apply_once(
        MAIN,
        '''from apps.api.routers import notifications as notifications_router
from apps.api.routers import trading as trading_router''',
        '''from apps.api.routers import notifications as notifications_router
from apps.api.routers import onboarding as onboarding_router
from apps.api.routers import trading as trading_router''',
        "import onboarding_router",
    )
    apply_once(
        MAIN,
        '''app.include_router(notifications_router.router)
app.include_router(trading_router.router)''',
        '''app.include_router(notifications_router.router)
app.include_router(onboarding_router.router)
app.include_router(trading_router.router)''',
        "include onboarding_router",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. settings.py — replace KNOWN_BLOCKS with a blocks query
# ─────────────────────────────────────────────────────────────────────

def step6_settings_refactor() -> None:
    print("\n[6/6] settings.py — KNOWN_BLOCKS from blocks registry")

    # Replace the list with a note; the actual query happens inside
    # list_block_settings. We change the function to query blocks.
    apply_once(
        SETTINGS_ROUTER,
        '''# Static registry of blocks we know about. Extend when new blocks land.
KNOWN_BLOCKS = [
    {"slug": "trading", "name": "Trading"},
    {"slug": "news",    "name": "News"},
]''',
        '''# S.7.4 — blocks are read from the blocks registry, not hardcoded.
# The picker (S.7.5) shows blocks the same way, so there is one source
# of truth: the blocks table.''',
        "remove KNOWN_BLOCKS constant",
    )

    apply_once(
        SETTINGS_ROUTER,
        '''    rows = (await session.execute(sql_text("""
        SELECT block_slug, auto_enabled, updated_at
        FROM org_block_settings
    """))).all()
    by_slug = {r[0]: r for r in rows}

    items = []
    for b in KNOWN_BLOCKS:
        r = by_slug.get(b["slug"])
        items.append(BlockSettingsItem(
            slug=b["slug"],
            name=b["name"],
            auto_enabled=bool(r[1]) if r else True,
            updated_at=r[2] if r else None,
        ))
    return BlockSettingsListResponse(items=items)''',
        '''    # S.7.4 — read the block registry instead of a hardcoded list.
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
    return BlockSettingsListResponse(items=items)''',
        "list_block_settings queries blocks",
    )

    apply_once(
        SETTINGS_ROUTER,
        '''    from fastapi import HTTPException
    known = {b["slug"]: b["name"] for b in KNOWN_BLOCKS}
    if slug not in known:
        raise HTTPException(status_code=404, detail=f"Unknown block: {slug}")''',
        '''    from fastapi import HTTPException
    # S.7.4 — validate against the registry, not a hardcoded list.
    known_row = (await session.execute(sql_text(
        "SELECT name FROM blocks WHERE slug = :s AND is_active = true"
    ), {"s": slug})).first()
    if known_row is None:
        raise HTTPException(status_code=404, detail=f"Unknown block: {slug}")''',
        "update_block_settings queries blocks",
    )


def main() -> None:
    print("=" * 70)
    print("S.7.4 — onboarding API")
    print("=" * 70)
    step1_schema()
    step2_router()
    step3_token_response()
    step4_auth_populate()
    step5_mount()
    step6_settings_refactor()
    print()
    print("=" * 70)
    print("Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
