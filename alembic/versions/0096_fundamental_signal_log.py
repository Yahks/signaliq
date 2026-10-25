"""fundamental_signal_log: the signal as it fires, dated.

S.9.93 — the article has never been testable because the signal
was never recorded. This table is the time series: one row per
(org, date, signal) with the state and the value it was derived
from.

Spread rows carry real data: front-month CL=F minus the next
contract month (CLF27.NYM etc.), fetched from Yahoo. Today's
reading is backwardation (front 103.27 > second 100.94), and the
state (widening/narrowing) follows from the day-over-day change.

API draw and OPEC cut rows carry 'unavailable' until a source for
the release numbers exists; the table records that honestly rather
than inventing a value.

Revision ID: 0096
Revises: 0095
Create Date: 2026-10-25
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0096"
down_revision: str | None = "0095"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS fundamental_signal_log (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            observed_on date NOT NULL,
            signal text NOT NULL,
            state text NOT NULL,
            value numeric,
            source text,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_fsl_org_date_signal
        ON fundamental_signal_log (org_id, observed_on, signal)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_fsl_org_date_signal")
    op.execute("DROP TABLE IF EXISTS fundamental_signal_log")
