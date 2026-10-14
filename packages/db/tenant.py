"""Tenant context helpers.

Every request that touches tenant-scoped data must call set_current_org()
before executing any query. This sets a Postgres session variable that
Row-Level Security policies read.

Note: Postgres' SET LOCAL does not accept bound parameters — the value
must be a literal in the SQL text. We validate the input as a UUID before
interpolation to prevent injection.
"""
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _validate_uuid(value: str) -> str:
    """Return the canonical UUID string, or raise ValueError."""
    return str(_uuid.UUID(value))


async def set_current_org(session: AsyncSession, org_id: str) -> None:
    """Set `app.current_org_id` for the current session.

    Uses session-scoped SET, not SET LOCAL. SET LOCAL is transaction-scoped
    and is cleared by any commit inside the request. When a request calls a
    helper that commits (e.g. charge_for_upload → debit → commit), the next
    query against an RLS table runs with the GUC unset and Postgres raises
    'invalid input syntax for type uuid: ""' from the RLS policy.

    The connection pool resets session state on return (pool_reset_on_return
    defaults to 'rollback'), so the GUC does not leak across requests.
    """
    safe = _validate_uuid(org_id)
    await session.execute(text(f"SET app.current_org_id = '{safe}'"))
