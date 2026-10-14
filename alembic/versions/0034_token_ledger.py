"""Token ledger system.

Adds two tables:
  - user_tokens:    per-user balance (single row per user)
  - token_ledger:   immutable log of every grant + debit

Backfills every existing user with 1,000,000 tokens (signup_bonus)
so nobody's workflow breaks when the debit hook goes live.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_tokens",
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("balance", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("lifetime_granted", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("lifetime_consumed", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "token_ledger",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("delta", sa.BigInteger, nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("ref_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("balance_after", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_token_ledger_user_time", "token_ledger",
                    ["user_id", "created_at"])
    op.create_index("ix_token_ledger_reason", "token_ledger", ["reason"])

    # RLS on token_ledger — user can only see their own rows
    op.execute("ALTER TABLE token_ledger ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE token_ledger FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_token_ledger ON token_ledger
            USING (
                NULLIF(current_setting('app.current_user_id', true), '') IS NULL
                OR user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
    """)

    # RLS on user_tokens — same
    op.execute("ALTER TABLE user_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_tokens FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_user_tokens ON user_tokens
            USING (
                NULLIF(current_setting('app.current_user_id', true), '') IS NULL
                OR user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
    """)

    # Backfill: every existing user gets 1,000,000 tokens
    op.execute("""
        INSERT INTO user_tokens (user_id, balance, lifetime_granted)
        SELECT id, 1000000, 1000000 FROM users
        ON CONFLICT (user_id) DO NOTHING
    """)
    op.execute("""
        INSERT INTO token_ledger (user_id, delta, reason, balance_after, metadata)
        SELECT id, 1000000, 'signup_bonus_backfill', 1000000,
               '{"note": "auto-granted during S.2 rollout"}'::jsonb
        FROM users
        WHERE id NOT IN (
            SELECT DISTINCT user_id FROM token_ledger
            WHERE reason LIKE 'signup_bonus%'
        )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_user_tokens ON user_tokens")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_token_ledger ON token_ledger")
    op.drop_index("ix_token_ledger_reason", table_name="token_ledger")
    op.drop_index("ix_token_ledger_user_time", table_name="token_ledger")
    op.drop_table("token_ledger")
    op.drop_table("user_tokens")
