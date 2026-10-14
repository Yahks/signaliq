"""Metrics: AI cost, usage, health."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import current_org_id
from packages.ai.budget.cost_tracker import get_usage_summary
from packages.db.models.org import Org
from packages.db.session import get_session
from packages.db.tenant import set_current_org

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


async def _scoped_session(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(get_session),
) -> AsyncSession:
    await set_current_org(session, org_id)
    return session


@router.get("/ai-cost")
async def ai_cost_summary(
    org_id: str = Depends(current_org_id),
) -> dict:
    """Return today's AI usage + caps for the current org."""
    return get_usage_summary(org_id)


@router.get("/ai-cost/history")
async def ai_cost_history(
    days: int = 30,
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped_session),
) -> dict:
    """Return the last N days of usage from Postgres."""
    rows = (await session.execute(sql_text("""
        SELECT usage_date, input_tokens, output_tokens, calls, cost_usd
        FROM org_ai_usage
        WHERE org_id = :oid
          AND usage_date >= (CURRENT_DATE - CAST(:days AS integer))
        ORDER BY usage_date DESC
    """), {"oid": org_id, "days": days})).all()

    return {
        "days": [
            {
                "date": r[0].isoformat(),
                "input_tokens": int(r[1]),
                "output_tokens": int(r[2]),
                "calls": int(r[3]),
                "cost_usd": float(r[4]),
            }
            for r in rows
        ]
    }


@router.get("/ai-cost/config")
async def get_ai_config(
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_scoped_session),
) -> dict:
    """Return the org's ai_config (budget settings)."""
    row = (await session.execute(
        select(Org).where(Org.id == org_id)
    )).scalar_one_or_none()
    if not row:
        return {}
    return row.settings if hasattr(row, "settings") else {}
