"""fundamental_signal_states: per-state signal directions.

S.9.85 — the spread has four states, each with a different
direction:

  backwardation widening  -> bullish (more bullish than the level)
  backwardation narrowing  -> bearish (the bullish bias eroding)
  contango widening        -> bearish
  contango narrowing       -> bullish

An article that covers "the spread" in general must hold all four
at once, and produces sentences that contradict each other. The
fix: one state per coordinate, and the direction is a row.

fundamental_signal_directions is replaced (it held one direction
per signal, which is only correct for the one-way signals).

Revision ID: 0092
Revises: 0091
Create Date: 2026-10-25
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES = [
    # signal, state, direction, description
    ("opec_cut", "above_0_5mbpd", "bullish",
     "a cut of more than 0.5 million barrels per day nudges me toward longs"),
    ("api_draw", "above_5mb", "bullish",
     "a draw greater than 5 million barrels supports a bullish bias"),
    ("spread", "backwardation_widening", "bullish",
     "spot above futures, and the gap widening: more bullish than "
     "backwardation alone"),
    ("spread", "backwardation_narrowing", "bearish",
     "spot above futures, but the gap closing toward contango: the "
     "bullish bias is eroding"),
    ("spread", "contango_widening", "bearish",
     "futures above spot, and the gap widening: more bearish"),
    ("spread", "contango_narrowing", "bullish",
     "futures above spot, but the gap closing toward backwardation: "
     "the bearish bias is eroding"),
]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS fundamental_signal_states (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            signal text NOT NULL,
            state text NOT NULL,
            direction text NOT NULL,
            description text,
            created_at timestamptz NOT NULL DEFAULT now(),
            deleted_at timestamptz
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_fss_org_signal_state
        ON fundamental_signal_states (org_id, signal, state)
        WHERE deleted_at IS NULL
    """)
    for signal, state, direction, desc in STATES:
        op.execute(f"""
            INSERT INTO fundamental_signal_states
                (org_id, signal, state, direction, description)
            SELECT DISTINCT tp.org_id, '{signal}', '{state}',
                   '{direction}', $d${desc}$d$
            FROM trader_process tp
            WHERE tp.deleted_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM fundamental_signal_states f
                WHERE f.org_id = tp.org_id AND f.signal = '{signal}'
                  AND f.state = '{state}' AND f.deleted_at IS NULL
              )
        """)
    # The old one-direction-per-signal table is superseded.
    op.execute("DROP TABLE IF EXISTS fundamental_signal_directions")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_fss_org_signal_state")
    op.execute("DROP TABLE IF EXISTS fundamental_signal_states")
