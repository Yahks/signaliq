"""fundamental_signal_state_drafts: model-drafted signal facts.

S.9.87 — the reviewer's errors on fundamental_metrics are the facts
the article had to state but the signal table did not carry:
frequency, size rule, exit rule, point-in-time, composite rule,
holding period.

Every one of those facts is in the trader's process document —
position_sizing has the size rule, exit_logic the exit, daily_scan
the timing and frequency, holding_period the window. The drafter
reads all of trader_process and fills each field from the column
that states it, citing that column. A field whose value does not
appear in its cited column is rejected before the draft is stored.

Drafts follow the market_conditions_drafts pattern: model writes,
checks run, reviewed_at promotes.

Revision ID: 0093
Revises: 0092
Create Date: 2026-10-25
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0093"
down_revision: str | None = "0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS fundamental_signal_state_drafts (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            signal text NOT NULL,
            state text NOT NULL,
            direction text NOT NULL,
            frequency text NOT NULL,
            rule_definition text NOT NULL,
            size_rule text NOT NULL,
            exit_rule text NOT NULL,
            point_in_time text NOT NULL,
            holding_period text NOT NULL,
            composite_rule text NOT NULL,
            source_columns jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            reviewed_at timestamptz,
            deleted_at timestamptz,
            CHECK (composite_rule IN ('warning', 'veto')),
            CHECK (direction IN ('bullish', 'bearish'))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_fssd_org_signal_state
        ON fundamental_signal_state_drafts (org_id, signal, state)
        WHERE deleted_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_fssd_org_signal_state")
    op.execute("DROP TABLE IF EXISTS fundamental_signal_state_drafts")
