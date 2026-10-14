"""S.7.6a — soft-delete columns for trash.

Three tables get deleted_at timestamptz NULL. NULL = visible;
non-null = in trash, restorable for 2 days, then purged by a worker.

Partial indexes WHERE deleted_at IS NULL on the columns each list
query uses, so the filter is cheap.

Every read path in the codebase gets an "AND deleted_at IS NULL" —
except two, by design:
  - pick_next_topic (topic rotation): a deleted post still counts as
    covered. Deleting a post must not let a topic come back.
  - persist_post's dedup check: a deleted duplicate is still a
    duplicate. Re-generating the same content produces the same hash
    within 24h and is refused.
And the idempotency check in briefs.py — same logic as dedup.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text as sql_text


revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Columns
    bind.execute(sql_text("""
        ALTER TABLE trading_posts
        ADD COLUMN deleted_at timestamptz NULL
    """))
    bind.execute(sql_text("""
        ALTER TABLE generated_posts
        ADD COLUMN deleted_at timestamptz NULL
    """))
    bind.execute(sql_text("""
        ALTER TABLE daily_briefs
        ADD COLUMN deleted_at timestamptz NULL
    """))

    # 2. Partial indexes — "visible rows" is the common case.
    bind.execute(sql_text("""
        CREATE INDEX ix_trading_posts_visible
          ON trading_posts (org_id, subblock_id, published_at DESC)
          WHERE deleted_at IS NULL
    """))
    bind.execute(sql_text("""
        CREATE INDEX ix_generated_posts_visible
          ON generated_posts (org_id, created_at DESC)
          WHERE deleted_at IS NULL
    """))
    bind.execute(sql_text("""
        CREATE INDEX ix_daily_briefs_visible
          ON daily_briefs (org_id, brief_date DESC)
          WHERE deleted_at IS NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("DROP INDEX IF EXISTS ix_daily_briefs_visible"))
    bind.execute(sql_text("DROP INDEX IF EXISTS ix_generated_posts_visible"))
    bind.execute(sql_text("DROP INDEX IF EXISTS ix_trading_posts_visible"))
    bind.execute(sql_text("ALTER TABLE daily_briefs DROP COLUMN IF EXISTS deleted_at"))
    bind.execute(sql_text("ALTER TABLE generated_posts DROP COLUMN IF EXISTS deleted_at"))
    bind.execute(sql_text("ALTER TABLE trading_posts DROP COLUMN IF EXISTS deleted_at"))
