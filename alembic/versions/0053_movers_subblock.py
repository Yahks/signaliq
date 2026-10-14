"""S.8.9 — movers subblock.

A scheduled subblock that scans a universe of stocks, flags those
that crossed a daily move threshold, and generates an article per
flag. Uses the Studio generation pipeline.

Enabled by default with cadence='hourly_market'. Individual orgs
can disable via org_block_settings (or the subblock's enabled flag
on their override row).

Not part of the seed data — orgs that enable trading get this
subblock from the global row.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text as sql_text


revision = "0053"
down_revision = "0052"
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
            'movers',
            'Market Movers',
            'Auto-generated articles on stocks crossing daily move thresholds',
            'market',
            'hourly_market',
            'studio',
            '{"icon": "Zap", "color": "#f59e0b", "layout": "feed"}'::jsonb,
            'Write as a trading professional explaining an unusual move.',
            '{"threshold_pct": 5.0, "max_articles_per_run": 3, "universe": "sp100"}'::jsonb,
            false,
            20
        FROM blocks
        WHERE slug = 'trading'
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        DELETE FROM trading_subblocks
        WHERE slug = 'movers' AND org_id IS NULL
    """))
