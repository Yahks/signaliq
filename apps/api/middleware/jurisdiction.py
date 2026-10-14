"""Jurisdiction gating middleware.

Blocks API access from countries where football-prediction content is
illegal. Returns 451 Unavailable For Legal Reasons.

Bypass paths:
  - /health                (monitoring)
  - /api/health/*          (monitoring)
  - /docs, /redoc, /openapi.json
  - /api/auth/login, /register, /refresh  (need to see 451 on the auth flow)
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from packages.core.logging import get_logger
from packages.security.jurisdiction import check_jurisdiction

log = get_logger(__name__)

BYPASS_PATHS = {
    "/health", "/", "/docs", "/redoc", "/openapi.json",
}
BYPASS_PREFIXES = (
    "/api/health",
    "/docs",
    "/redoc",
)


class JurisdictionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Skip bypass paths
        if path in BYPASS_PATHS:
            return await call_next(request)
        for prefix in BYPASS_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        headers = {k.lower(): v for k, v in request.headers.items()}
        fallback = request.client.host if request.client else None

        try:
            allowed, country = check_jurisdiction(headers=headers, fallback_ip=fallback)
        except Exception as exc:
            log.warning("jurisdiction.middleware_error", error=str(exc)[:200])
            # Fail open on unexpected errors — don't break the API
            allowed, country = True, None

        if not allowed:
            log.warning(
                "jurisdiction.blocked",
                country=country,
                path=path,
                ip=(headers.get("x-forwarded-for") or fallback or "")[:20],
            )
            return JSONResponse(
                status_code=451,
                content={
                    "error": {
                        "code": "unavailable_for_legal_reasons",
                        "message": (
                            "This service is not available in your jurisdiction. "
                            "Football prediction and betting-adjacent content is "
                            "regulated or prohibited in your country."
                        ),
                        "context": {"country": country},
                    }
                },
            )

        response = await call_next(request)
        if country:
            response.headers["X-SignalIQ-Country"] = country
        return response
