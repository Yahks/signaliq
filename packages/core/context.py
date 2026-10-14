"""Request/task context variables.

These carry the current user and org through async call stacks without
passing them as explicit arguments to every function. Set at the
boundary (HTTP dependency or Celery task start), read anywhere else.

Used by:
  - packages.ai.router — to debit the current user on every LLM call
  - packages.core.tokens — to associate ledger entries with user + org
"""
from __future__ import annotations

from contextvars import ContextVar

# Set by apps/api/deps.py::current_user_id and by Celery task entrypoints.
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)
current_org_id: ContextVar[str | None] = ContextVar("current_org_id", default=None)


# ─────────────────────────────────────────────────────────────────────
# Convenience helper — used at Celery task entrypoints
# ─────────────────────────────────────────────────────────────────────

async def set_context_from_org(session, org_id: str) -> str | None:
    """Resolve the org owner and set both contextvars.

    Idempotent: if current_user_id is already set, don't override.
    Returns the user_id (or None if the org has no owner).

    Called at the top of every Celery task that makes LLM calls, so
    the router's debit hook knows who to charge.
    """
    if current_user_id.get() is not None:
        return current_user_id.get()

    from sqlalchemy import text as _sql
    row = (await session.execute(_sql("""
        SELECT user_id::text FROM memberships
        WHERE org_id = :oid AND role = 'owner'
        LIMIT 1
    """), {"oid": org_id})).first()

    uid = row[0] if row else None
    if uid:
        current_user_id.set(uid)
    current_org_id.set(org_id)
    return uid
