"""fundamental_signal_composite: composite rule belongs to the signal.

S.9.88 — the trader's document states a veto for one signal only:
"I avoid new longs only when the market has moved into contango."
The cut and the draw are advisory ("nudges me toward longs",
"supports a bullish bias"); only the spread vetoes.

Per-row composite_rule could not express that: the four spread
states share one rule (veto) while cut and draw share another
(advisory). The cross-row consistency check rejected a correct
answer because the schema was wrong.

- fundamental_signal_composite(org_id, signal, composite_rule)
  seeded from the trader's own words.
- fundamental_signal_state_drafts.composite_rule is dropped; the
  drafter reads the signal's rule instead of asking for one.

Revision ID: 0094
Revises: 0093
Create Date: 2026-10-25
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0094"
down_revision: str | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# From the trader's fundamental_metrics text:
#   "a cut ... nudges me toward longs"           -> advisory
#   "a draw ... supports a bullish bias"         -> advisory
#   "I avoid new longs only when ... contango"   -> veto
RULES = [
    ("opec_cut", "warning"),
    ("api_draw", "warning"),
    ("spread", "veto"),
]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS fundamental_signal_composite (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            signal text NOT NULL,
            composite_rule text NOT NULL,
            source_text text,
            created_at timestamptz NOT NULL DEFAULT now(),
            deleted_at timestamptz,
            CHECK (composite_rule IN ('warning', 'veto'))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_fsc_org_signal
        ON fundamental_signal_composite (org_id, signal)
        WHERE deleted_at IS NULL
    """)
    for sig, rule in RULES:
        op.execute(f"""
            INSERT INTO fundamental_signal_composite
                (org_id, signal, composite_rule, source_text)
            SELECT DISTINCT tp.org_id, '{sig}', '{rule}',
                   $src$I avoid new longs only when the market has moved
into contango.$src$
            FROM trader_process tp
            WHERE tp.deleted_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM fundamental_signal_composite c
                WHERE c.org_id = tp.org_id AND c.signal = '{sig}'
                  AND c.deleted_at IS NULL
              )
        """)
    # Per-row composite is superseded.
    op.execute("""
        ALTER TABLE fundamental_signal_state_drafts
        DROP COLUMN IF EXISTS composite_rule
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE fundamental_signal_state_drafts
        ADD COLUMN IF NOT EXISTS composite_rule text
    """)
    op.execute("DROP INDEX IF EXISTS ix_fsc_org_signal")
    op.execute("DROP TABLE IF EXISTS fundamental_signal_composite")
