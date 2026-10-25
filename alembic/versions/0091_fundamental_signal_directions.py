"""fundamental_signal_directions: sign of each fundamental signal.

S.9.83 — the FM8 article called a 6 mb inventory draw bearish
("eroded the trend") while the trader's section says a draw
supports a bullish bias. A word-list check cannot catch that: the
model can express bearishness as stalled, eroded, evaporated, faded,
weakened, sold off, or a hundred other verbs.

The full fix is to make the direction a fact, not a reading. This
table holds, per org and signal, which direction is bullish. The
article must state the direction it is treating a signal as, and the
validator compares to this value.

Seeded from the trader's section for the orgs that have one:
  opec_cut_gt_0_5mbpd   bullish
  api_draw_gt_5mb       bullish
  spread_widening_gt_0_50  bearish

Revision ID: 0091
Revises: 0090
Create Date: 2026-10-25
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNALS = [
    ("opec_cut_gt_0_5mbpd", "bullish",
     "a cut of more than 0.5 million barrels per day nudges me toward longs"),
    ("api_draw_gt_5mb", "bullish",
     "a draw greater than 5 million barrels supports a bullish bias"),
    ("spread_widening_gt_0_50", "bearish",
     "a widening beyond $0.50 signals potential contango risk, prompting "
     "me to avoid new longs that day"),
]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS fundamental_signal_directions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            signal text NOT NULL,
            direction text NOT NULL,
            source_text text,
            created_at timestamptz NOT NULL DEFAULT now(),
            deleted_at timestamptz
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_fsd_org_signal
        ON fundamental_signal_directions (org_id, signal)
        WHERE deleted_at IS NULL
    """)
    for signal, direction, source in SIGNALS:
        op.execute(f"""
            INSERT INTO fundamental_signal_directions
                (org_id, signal, direction, source_text)
            SELECT DISTINCT tp.org_id, '{signal}', '{direction}',
                   $src${source}$src$
            FROM trader_process tp
            WHERE tp.deleted_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM fundamental_signal_directions f
                WHERE f.org_id = tp.org_id AND f.signal = '{signal}'
                  AND f.deleted_at IS NULL
              )
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_fsd_org_signal")
    op.execute("DROP TABLE IF EXISTS fundamental_signal_directions")
