"""Health check endpoints (detailed system health)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.api.deps import require_role
from packages.observability.health import get_health

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/full")
async def full_health() -> dict:
    """Full system health — public, no auth. Meant for monitoring tools."""
    return get_health()


@router.get("/admin")
async def admin_health(_role: str = Depends(require_role("owner", "admin"))) -> dict:
    """Same as /full but requires admin role (future: add more detail)."""
    return get_health()
