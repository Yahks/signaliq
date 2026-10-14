"""Per-key Groq token visibility.

Adds:
  ai_calls.credential_id                - which key made each call
  ai_calls.rate_headers                 - raw x-ratelimit-* from the call
  ai_credentials.last_remaining_tokens  - last-known remaining, from the
                                          most recent successful call
  ai_credentials.last_rate_headers      - raw headers from that call
  ai_credentials.last_rate_at           - when they were captured

The ai_calls.credential_id FK is SET NULL on delete: removing a
credential should never erase the audit trail of calls that used it.
Historical ai_calls rows have NULL credential_id (no way to backfill).

Revision ID: 0045
Revises: 0044
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ai_calls: per-key attribution + raw rate headers
    op.add_column(
        "ai_calls",
        sa.Column(
            "credential_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("ai_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "ai_calls",
        sa.Column("rate_headers", postgresql.JSONB, nullable=True),
    )
    op.create_index(
        "ix_ai_calls_credential",
        "ai_calls",
        ["credential_id", "created_at"],
    )

    # ai_credentials: last-known rate state
    op.add_column(
        "ai_credentials",
        sa.Column("last_remaining_tokens", sa.Integer, nullable=True),
    )
    op.add_column(
        "ai_credentials",
        sa.Column("last_rate_headers", postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "ai_credentials",
        sa.Column("last_rate_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_credentials", "last_rate_at")
    op.drop_column("ai_credentials", "last_rate_headers")
    op.drop_column("ai_credentials", "last_remaining_tokens")

    op.drop_index("ix_ai_calls_credential", table_name="ai_calls")
    op.drop_column("ai_calls", "rate_headers")
    op.drop_column("ai_calls", "credential_id")
