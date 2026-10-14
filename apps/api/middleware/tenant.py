"""Tenant middleware.

On every request that carries a valid access token, sets the Postgres
session variable `app.current_org_id` for the request's transaction.

Uses the JWT's `org` claim. If the token has no org (e.g. refresh-only),
the variable is not set and RLS will block all tenant-scoped queries
(which is the correct default — fail closed, not open).
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from packages.core.security import decode_token


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Extracts org_id from Bearer token and stores it in request.state.

    The actual `SET LOCAL app.current_org_id` happens inside the request-scoped
    DB session (see apps/api/deps.py) because we need it inside the same
    transaction as the query.
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        request.state.org_id = None
        request.state.user_id = None
        request.state.role = None

        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            try:
                payload = decode_token(token, expected_type="access")
                request.state.user_id = payload.get("sub")
                request.state.org_id = payload.get("org")
                request.state.role = payload.get("role")
            except Exception:
                # Invalid token → treat as anonymous. Auth-required endpoints
                # will reject; public endpoints proceed.
                pass

        return await call_next(request)
