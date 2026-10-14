"""Global per-IP rate limit middleware.

Applies to every request. Auth endpoints get a stricter limit.

Bypasses:
  - /health (monitoring)
  - /docs, /redoc, /openapi.json (dev docs)
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from packages.core.logging import get_logger
from packages.core.rate_limit import check_auth_limit, check_ip_limit

log = get_logger(__name__)

# Endpoints that skip per-IP rate limiting
BYPASS_PATHS = {"/health", "/", "/docs", "/redoc", "/openapi.json"}

# Endpoints that use the stricter auth limit
AUTH_PATHS = {"/api/auth/login", "/api/auth/register", "/api/auth/refresh"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Skip bypass paths
        if path in BYPASS_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)

        # Client IP — respect X-Forwarded-For if behind a proxy
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if not ip:
            ip = request.client.host if request.client else "unknown"

        # Auth endpoints get a stricter limit
        if path in AUTH_PATHS:
            result = check_auth_limit(ip)
        else:
            result = check_ip_limit(ip, path.split("?")[0])

        if not result.allowed:
            log.warning(
                "ratelimit.blocked",
                ip=ip[:20],
                path=path,
                current=result.current,
                limit=result.limit,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": f"Too many requests. Try again in {result.retry_after_seconds}s.",
                        "context": {
                            "limit": result.limit,
                            "window_seconds": result.window_seconds,
                            "retry_after_seconds": result.retry_after_seconds,
                        },
                    }
                },
                headers={"Retry-After": str(result.retry_after_seconds)},
            )

        response = await call_next(request)
        # Advertise remaining quota in a non-standard header (useful for debugging)
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, result.limit - result.current))
        return response
