"""generated_posts.verification_status.

Adds a column distinguishing 'this post passed factual verification' from
'this post was never verified because the verifier itself failed'.

Values:
  - 'unknown'   — written before this column existed (backfill)
  - 'verified'  — verifier ran and found 0 unsupported facts
  - 'unverified' — verifier found unsupported facts that regen couldn't clear
  - 'error'     — verifier itself failed (LLM error, timeout, etc.)

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generated_posts",
        sa.Column(
            "verification_status",
            sa.String(16),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_index(
        "ix_generated_posts_verification_status",
        "generated_posts",
        ["org_id", "verification_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_generated_posts_verification_status", table_name="generated_posts")
    op.drop_column("generated_posts", "verification_status")
