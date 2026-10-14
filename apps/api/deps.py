"""FastAPI dependencies: current user, current org, RBAC."""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.error import AuthError, PermissionError
from packages.core.security import decode_token
from packages.db.session import get_session
from packages.db.tenant import set_current_org


async def current_token_payload(
    authorization: str | None = Header(default=None),
) -> dict:
    """Extract and validate the access token from the Authorization header."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    return decode_token(token, expected_type="access")


async def current_user_id(payload: dict = Depends(current_token_payload)) -> str:
    from packages.core.context import current_user_id as _uid_ctx
    uid = payload["sub"]
    _uid_ctx.set(uid)
    return uid


async def current_org_id(payload: dict = Depends(current_token_payload)) -> str:
    from packages.core.context import current_org_id as _org_ctx
    org = payload.get("org")
    if not org:
        raise AuthError("Token has no org context")
    _org_ctx.set(org)
    return org


async def current_role(payload: dict = Depends(current_token_payload)) -> str:
    role = payload.get("role")
    if not role:
        raise AuthError("Token has no role")
    return role


async def tenant_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncIterator[AsyncSession]:
    """A DB session with `app.current_org_id` already set.

    Use this dependency on any endpoint that reads or writes tenant data.
    """
    await set_current_org(session, org_id)
    yield session


def require_role(*allowed: str):  # type: ignore[no-untyped-def]
    """Dependency factory: reject unless the current role is in `allowed`."""
    async def _checker(role: str = Depends(current_role)) -> str:
        if role not in allowed:
            raise PermissionError(f"Requires one of: {', '.join(allowed)}")
        return role
    return _checker
