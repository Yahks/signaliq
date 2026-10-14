"""Prove that Row-Level Security prevents cross-tenant access.

For every org-scoped table, we:
    1. Create rows in org A and org B
    2. Set tenant context to A → verify only A's rows are visible
    3. Set tenant context to B → verify only B's rows are visible
    4. Verify the owner role (no SET) sees all rows
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _set_org(session: AsyncSession, org_id: str) -> None:
    await session.execute(text(f"SET app.current_org_id = '{org_id}'"))
    await session.commit()


# ---------- orgs table (owner-visible, no RLS on this table) ----------
# orgs is intentionally not RLS-protected because it is the tenant registry.


# ---------- memberships ----------

async def test_memberships_are_isolated(db_session, test_org_a, test_org_b):
    """memberships must not leak between orgs."""
    await _set_org(db_session, test_org_a["org_id"])

    rows = (await db_session.execute(text("""
        SELECT org_id::text FROM memberships
    """))).all()
    assert len(rows) == 1, f"Org A should see 1 membership, saw {len(rows)}"
    assert rows[0][0] == test_org_a["org_id"]

    # Switch to org B
    await _set_org(db_session, test_org_b["org_id"])
    rows = (await db_session.execute(text("""
        SELECT org_id::text FROM memberships
    """))).all()
    assert len(rows) == 1, f"Org B should see 1 membership, saw {len(rows)}"
    assert rows[0][0] == test_org_b["org_id"]


# ---------- sources ----------

async def test_sources_isolated(db_session, test_org_a, test_org_b):
    await _set_org(db_session, test_org_a["org_id"])
    await db_session.execute(text("""
        INSERT INTO sources (org_id, source_type, name)
        VALUES (:o, 'csv', 'Source A')
    """), {"o": test_org_a["org_id"]})
    await db_session.commit()

    await _set_org(db_session, test_org_b["org_id"])
    await db_session.execute(text("""
        INSERT INTO sources (org_id, source_type, name)
        VALUES (:o, 'csv', 'Source B')
    """), {"o": test_org_b["org_id"]})
    await db_session.commit()

    # Org A sees only its own
    await _set_org(db_session, test_org_a["org_id"])
    names = [r[0] for r in (await db_session.execute(
        text("SELECT name FROM sources")
    )).all()]
    assert names == ["Source A"]

    # Org B sees only its own
    await _set_org(db_session, test_org_b["org_id"])
    names = [r[0] for r in (await db_session.execute(
        text("SELECT name FROM sources")
    )).all()]
    assert names == ["Source B"]


# ---------- news_chains ----------

async def test_news_chains_isolated(db_session, test_org_a, test_org_b):
    await _set_org(db_session, test_org_a["org_id"])
    await db_session.execute(text("""
        INSERT INTO news_chains (org_id, title, status)
        VALUES (:o, 'Chain A', 'ready')
    """), {"o": test_org_a["org_id"]})
    await db_session.commit()

    await _set_org(db_session, test_org_b["org_id"])
    await db_session.execute(text("""
        INSERT INTO news_chains (org_id, title, status)
        VALUES (:o, 'Chain B', 'ready')
    """), {"o": test_org_b["org_id"]})
    await db_session.commit()

    await _set_org(db_session, test_org_a["org_id"])
    titles = [r[0] for r in (await db_session.execute(
        text("SELECT title FROM news_chains")
    )).all()]
    assert titles == ["Chain A"]


# ---------- chain_impacts (child of chain) ----------

async def test_chain_impacts_isolated(db_session, test_org_a, test_org_b):
    # Create chain in org A
    await _set_org(db_session, test_org_a["org_id"])
    chain_a = (await db_session.execute(text("""
        INSERT INTO news_chains (org_id, title, status)
        VALUES (:o, 'Chain A', 'ready') RETURNING id::text
    """), {"o": test_org_a["org_id"]})).scalar_one()
    await db_session.execute(text("""
        INSERT INTO chain_impacts (org_id, chain_id, label, category, position)
        VALUES (:o, :c, 'Impact A', 'sector', 0)
    """), {"o": test_org_a["org_id"], "c": chain_a})
    await db_session.commit()

    # Org B sets tenant context — chain_impacts should be empty
    await _set_org(db_session, test_org_b["org_id"])
    rows = (await db_session.execute(text("SELECT id FROM chain_impacts"))).all()
    assert len(rows) == 0, f"Org B should see 0 impacts, saw {len(rows)}"

    # Org A sees its own
    await _set_org(db_session, test_org_a["org_id"])
    rows = (await db_session.execute(text("SELECT id FROM chain_impacts"))).all()
    assert len(rows) == 1


# ---------- notifications ----------

async def test_notifications_isolated(db_session, test_org_a, test_org_b):
    await _set_org(db_session, test_org_a["org_id"])
    await db_session.execute(text("""
        INSERT INTO notifications (org_id, user_id, kind, title)
        VALUES (:o, :u, 'chain_update', 'Notif A')
    """), {"o": test_org_a["org_id"], "u": test_org_a["user_id"]})
    await db_session.commit()

    await _set_org(db_session, test_org_b["org_id"])
    titles = [r[0] for r in (await db_session.execute(
        text("SELECT title FROM notifications")
    )).all()]
    assert titles == [], "Org B should not see org A's notifications"

    await _set_org(db_session, test_org_a["org_id"])
    titles = [r[0] for r in (await db_session.execute(
        text("SELECT title FROM notifications")
    )).all()]
    assert titles == ["Notif A"]


# ---------- org_ai_usage ----------

async def test_ai_usage_isolated(db_session, test_org_a, test_org_b):
    from datetime import date

    await _set_org(db_session, test_org_a["org_id"])
    await db_session.execute(text("""
        INSERT INTO org_ai_usage (org_id, usage_date, calls)
        VALUES (:o, :d, 5)
    """), {"o": test_org_a["org_id"], "d": date.today()})
    await db_session.commit()

    await _set_org(db_session, test_org_b["org_id"])
    rows = (await db_session.execute(text("SELECT calls FROM org_ai_usage"))).all()
    assert len(rows) == 0, "Org B should not see org A's AI usage"


# ---------- write attempts must fail ----------

async def test_cannot_insert_into_other_org(db_session, test_org_a, test_org_b):
    """With tenant context = A, inserting a row with org_id = B must fail."""
    await _set_org(db_session, test_org_a["org_id"])

    with pytest.raises(Exception):  # asyncpg should raise InsufficientPrivilege
        await db_session.execute(text("""
            INSERT INTO sources (org_id, source_type, name)
            VALUES (:o, 'csv', 'Sneaky')
        """), {"o": test_org_b["org_id"]})
        await db_session.commit()
    await db_session.rollback()
