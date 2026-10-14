"""S.8.9.3 — enable movers subblock.

Migration 0053 registered movers with enabled=false so a new
deploy wouldn't immediately start scanning. After S.8.9's
verification the subblock is proven, so it's enabled at the
global level like every other trading subblock.

Orgs that don't want movers can insert an org-specific
trading_subblocks row with enabled=false; load_subblock prefers
the org row over the global one.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text as sql_text


revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        UPDATE trading_subblocks
        SET enabled = true
        WHERE slug = 'movers' AND org_id IS NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        UPDATE trading_subblocks
        SET enabled = false
        WHERE slug = 'movers' AND org_id IS NULL
    """))
