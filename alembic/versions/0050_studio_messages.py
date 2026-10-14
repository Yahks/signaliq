"""S.8.1 — studio_messages table.

Studio is the on-demand chat interface inside the trading block.
Each user prompt and each assistant response is one row here.
Assistant rows that produced an article carry trading_post_id,
pointing at the trading_posts row that holds the article text.

Soft-delete via deleted_at, matching the trash pattern from 0049.
The index is partial (WHERE deleted_at IS NULL) — the chat
scrollback reads only live rows.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text as sql_text


revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        CREATE TABLE studio_messages (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id          uuid NOT NULL
                              REFERENCES orgs(id) ON DELETE CASCADE,
            user_id         uuid NOT NULL
                              REFERENCES users(id) ON DELETE CASCADE,
            role            varchar(16) NOT NULL,
            content         text NOT NULL,
            prompt_kind     varchar(32) NULL,
            trading_post_id uuid NULL
                              REFERENCES trading_posts(id) ON DELETE SET NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            deleted_at      timestamptz NULL
        )
    """))
    bind.execute(sql_text("""
        CREATE INDEX ix_studio_messages_org_user_time
          ON studio_messages (org_id, user_id, created_at DESC)
          WHERE deleted_at IS NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text(
        "DROP INDEX IF EXISTS ix_studio_messages_org_user_time"
    ))
    bind.execute(sql_text("DROP TABLE IF EXISTS studio_messages"))
