"""post_feedback: polymorphic post reference.

The post_feedback table was designed for news-chain posts. Its
post_id column has a FK to generated_posts(id). But the trading
drafts page also inserts into post_feedback when a human approves or
rejects a trading_post. Those inserts violate the FK:

  insert or update on table "post_feedback" violates foreign key
  constraint "post_feedback_post_id_fkey"
  DETAIL: Key is not present in table "generated_posts".

The FK is dropped and replaced with a (post_kind, post_id) discriminator
pair. post_kind ∈ {'news', 'trading'}. Existing rows are backfilled to
'news' (the only kind that existed before this migration).

The FK enforcement is lost, but the alternative — two parallel feedback
tables — duplicates the schema and the endpoint logic. One table with a
discriminator is the correct shape.

Revision ID: 0046
Revises: 0045
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop the FK that assumes only generated_posts exists.
    op.execute(
        "ALTER TABLE post_feedback "
        "DROP CONSTRAINT IF EXISTS post_feedback_post_id_fkey"
    )

    # 2. Add the discriminator. Every existing row is a news post.
    op.add_column(
        "post_feedback",
        sa.Column(
            "post_kind",
            sa.String(16),
            nullable=False,
            server_default="news",
        ),
    )

    # 3. Composite index for filtering by kind.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_post_feedback_post "
        "ON post_feedback (post_kind, post_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_post_feedback_post")
    op.drop_column("post_feedback", "post_kind")
    op.execute(
        "ALTER TABLE post_feedback "
        "ADD CONSTRAINT post_feedback_post_id_fkey "
        "FOREIGN KEY (post_id) REFERENCES generated_posts(id) ON DELETE CASCADE"
    )
