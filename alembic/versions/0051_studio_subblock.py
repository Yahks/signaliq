"""S.8.3 — studio subblock row.

Studio is not a real subblock in the sense that it fires on a
schedule. But its generated articles become trading_posts, and
trading_posts.subblock_id is NOT NULL with an FK to trading_subblocks.
A synthetic 'studio' row gives those posts a valid parent, and lets
the existing drafts page and dedup logic handle them like any other.

enabled=false so nothing schedules it. sort_order=999 places it
last in any listing that doesn't filter it out.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text as sql_text


revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        INSERT INTO trading_subblocks (
            org_id, block_id, slug, name, description, category, cadence,
            source_kind, theme, style_guide, training_config,
            enabled, sort_order
        )
        SELECT
            NULL,
            id,
            'studio',
            'Studio',
            'On-demand articles generated from user chat prompts',
            'chat',
            'on_demand',
            'studio',
            '{"icon": "MessageSquare", "color": "#8b5cf6", "layout": "chat"}'::jsonb,
            'Write as a trading professional responding to a user request.',
            '{}'::jsonb,
            false,
            999
        FROM blocks
        WHERE slug = 'trading'
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        DELETE FROM trading_subblocks
        WHERE slug = 'studio' AND org_id IS NULL
    """))
