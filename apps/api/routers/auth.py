"""Authentication endpoints: register, login, refresh, me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.schemas.auth import (
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

from apps.api.deps import current_token_payload
from packages.core.config import get_settings
from packages.core.error import AuthError, ConflictError, NotFoundError
from packages.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from packages.db.models.membership import Membership
from packages.db.models.org import Org
from packages.db.models.user import User
from packages.db.session import get_owner_session_factory, get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _load_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def _load_user_by_identifier(session: AsyncSession, identifier: str) -> User | None:
    """Load a user by email OR username (case-insensitive on username)."""
    ident = identifier.strip()
    result = await session.execute(
        select(User).where(
            or_(
                func.lower(User.email) == ident.lower(),
                func.lower(User.username) == ident.lower(),
            )
        )
    )
    return result.scalar_one_or_none()


async def _load_primary_membership(session: AsyncSession, user_id: str) -> Membership | None:
    result = await session.execute(
        select(Membership).where(Membership.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none()


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest) -> TokenResponse:
    """Register a user + create their first org + owner membership.

    Uses the OWNER DB role (bypasses RLS) because registration happens
    before any tenant context exists. Do NOT expose this pattern to
    authenticated endpoints.

    Requires explicit acceptance of:
      - Terms of Service
      - Age of majority (18+ or local equivalent)
      - Risk disclaimer (statistical analysis, not betting advice)
    """
    # Enforce disclaimer acceptance BEFORE any DB writes
    missing: list[str] = []
    if not body.accepted_terms:
        missing.append("terms")
    if not body.accepted_age:
        missing.append("age")
    if not body.accepted_risk:
        missing.append("risk")
    if missing:
        from packages.core.error import ValidationError
        raise ValidationError(
            f"Missing required acceptances: {', '.join(missing)}. "
            "You must accept the Terms, confirm you are 18+, and acknowledge "
            "the risk disclaimer before registering."
        )

    factory = get_owner_session_factory()
    async with factory() as session:
        if await _load_user_by_email(session, body.email):
            raise ConflictError("Email already registered")

        # Validate username
        if body.username:
            uname = body.username.strip().lower()
            if not uname or len(uname) < 3 or len(uname) > 64:
                from packages.core.error import ValidationError
                raise ValidationError("Username must be 3-64 characters")
            # Check availability
            existing = await session.execute(
                select(User).where(func.lower(User.username) == uname)
            )
            if existing.scalar_one_or_none():
                raise ConflictError("Username already taken")
        else:
            # Derive from email prefix
            base = body.email.split("@", 1)[0].lower()[:64]
            uname = base
            suffix = 1
            while True:
                existing = await session.execute(
                    select(User).where(func.lower(User.username) == uname)
                )
                if not existing.scalar_one_or_none():
                    break
                suffix += 1
                uname = f"{base}_{suffix}"

        user = User(
            email=body.email,
            username=uname,
            country=(body.country or None),
            password_hash=hash_password(body.password),
            full_name=body.full_name,
        )
        session.add(user)
        await session.flush()

        # Auto-derive org slug + name (no user input required)
        import secrets as _secrets
        base_slug = "".join(
            c for c in (body.username or body.email.split("@")[0]).lower()
            if c.isalnum() or c in "-_"
        )[:50] or "user"
        org_slug = f"{base_slug}-{_secrets.token_hex(3)}"
        org_name = f"{(body.username or body.email.split('@')[0]).title()}'s workspace"

        org = Org(slug=org_slug, name=org_name)
        session.add(org)
        await session.flush()

        membership = Membership(org_id=org.id, user_id=user.id, role="owner")
        session.add(membership)
        await session.commit()

        user_id = user.id
        org_id = org.id

        # Provision the new org with the rows the trading pipeline
        # needs: trader_process (default rules, account name) and
        # block settings. Idempotent. Wrapped so a provisioning
        # failure doesn't block signup.
        try:
            from packages.db.provision import provision_org
            await provision_org(session, str(org_id), user_id=str(user_id))
        except Exception as exc:
            from packages.core.logging import get_logger
            get_logger(__name__).warning(
                "auth.provision_failed", error=str(exc)[:200]
            )

        # Grant signup tokens. Uses a fresh transaction so it commits
        # independently and doesn't roll back the user if the ledger fails.
        try:
            from packages.core.tokens import grant, SIGNUP_GRANT
            await grant(session, user_id, SIGNUP_GRANT, "signup_bonus",
                        org_id=org_id)
        except Exception as exc:
            from packages.core.logging import get_logger
            get_logger(__name__).warning(
                "auth.signup_grant_failed", error=str(exc)[:200]
            )

    settings = get_settings()
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


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    """Authenticate and issue tokens.

    Uses the OWNER session because login happens before tenant context
    exists, so the membership lookup must bypass RLS.
    """
    factory = get_owner_session_factory()
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
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest) -> TokenResponse:
    """Issue a new access token from a refresh token.

    Reloads the user's primary membership so the new access token carries
    the correct org_id and role.
    """
    payload = decode_token(body.refresh_token, expected_type="refresh")
    user_id = payload["sub"]

    factory = get_owner_session_factory()
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
    )


@router.get("/me", response_model=MeResponse)
async def me(
    authorization: str | None = Header(default=None),
) -> MeResponse:
    """Return the current user + their org context.

    Uses the OWNER session factory because reading your own user record
    via a valid JWT is not a tenant-scoped operation — it happens before
    (or independently of) any org context. RLS on the `users` table
    requires app.current_org_id to be set, which /me has no reason to do.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token, expected_type="access")
    user_id = payload["sub"]

    factory = get_owner_session_factory()
    async with factory() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise NotFoundError("User not found")

        org_id = payload.get("org")
        role = payload.get("role")
        org_slug: str | None = None
        # S.7.5a — read onboarding state for the routing decision.
        onboarding_required = True
        if org_id:
            org_result = await session.execute(select(Org).where(Org.id == org_id))
            org = org_result.scalar_one_or_none()
            org_slug = org.slug if org else None
            onboarding_required = (org is None) or (org.onboarding_completed_at is None)

    return MeResponse(
        user=UserResponse.model_validate(user),
        org_id=org_id,
        org_slug=org_slug,
        role=role,
        onboarding_required=onboarding_required,
    )


@router.get("/tokens/balance")
async def token_balance(
    payload: dict = Depends(current_token_payload),
) -> dict:
    """Current user's token balance + today's usage + USD equivalents."""
    from sqlalchemy import text as _sql
    from packages.db.session import get_owner_session_factory
    from packages.core.tokens import TOKENS_PER_USD, usd_from_tokens
    user_id = payload["sub"]

    # Owner session bypasses RLS — we only read our own row.
    factory = get_owner_session_factory()
    async with factory() as session:
        row = (await session.execute(_sql("""
            SELECT balance, lifetime_granted, lifetime_consumed
            FROM user_tokens WHERE user_id = :uid
        """), {"uid": user_id})).first()

        today = (await session.execute(_sql("""
            SELECT
                COALESCE(-SUM(delta), 0) AS consumed_today,
                COUNT(*) AS calls_today
            FROM token_ledger
            WHERE user_id = :uid
              AND delta < 0
              AND created_at >= (now() AT TIME ZONE 'UTC')::date
        """), {"uid": user_id})).first()

    balance = int(row[0]) if row else 0
    granted = int(row[1]) if row else 0
    consumed = int(row[2]) if row else 0
    consumed_today = int(today[0]) if today else 0
    calls_today = int(today[1]) if today else 0

    return {
        "balance": balance,
        "lifetime_granted": granted,
        "lifetime_consumed": consumed,
        "consumed_today": consumed_today,
        "calls_today": calls_today,
        "tokens_per_usd": TOKENS_PER_USD,
        "balance_usd": usd_from_tokens(balance),
        "consumed_today_usd": usd_from_tokens(consumed_today),
    }
