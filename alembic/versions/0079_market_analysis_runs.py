"""market_analysis_runs — log of every rotation for the subblock.

S.9.44 — every market_analysis article picks a trade, a history
window, an angle, and a focus. This table records the picks so
the next run can avoid the last 20 combinations and so the
monthly trade reset knows what has been used.

Revision ID: 0079
Revises: 0078
Create Date: 2026-10-15
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS market_analysis_runs (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id      uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
            ran_at      timestamptz NOT NULL DEFAULT now(),
            trade_id    uuid REFERENCES trade_log(id) ON DELETE SET NULL,
            window_days integer NOT NULL,
            angle       text NOT NULL,
            focus       text NOT NULL,
            template_id text NOT NULL,
            post_id     uuid REFERENCES trading_posts(id) ON DELETE SET NULL
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ma_runs_recent
        ON market_analysis_runs (org_id, ran_at DESC)
    """)
    op.execute(
        "ALTER TABLE market_analysis_runs ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE market_analysis_runs FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_ma_runs "
        "ON market_analysis_runs"
    )
    op.execute("""
        CREATE POLICY tenant_isolation_ma_runs ON market_analysis_runs
        USING (
            org_id = NULLIF(
                current_setting('app.current_org_id', true), ''
            )::uuid
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS market_analysis_runs CASCADE")
