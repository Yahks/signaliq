"""trader_process: one fundamental column per signal.

S.9.89 — fundamental_metrics was one paragraph containing three
signals' rules. The drafter could not reliably pick the right
sentence for the right state: it stored the contango veto sentence
as the rule for contango_narrowing (which is bullish — the bearish
bias eroding), and it pulled OPEC's "monthly" frequency into a
spread row. Vocabulary checks cannot fix that: the words overlap
across states.

Three columns, one per signal. Each drafter call reads exactly one
column and cannot pull across.

The spread column states all four states and their directions
explicitly, so "widening backwardation is bullish" is written down,
not inferred.

Revision ID: 0095
Revises: 0094
Create Date: 2026-10-25
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0095"
down_revision: str | None = "0094"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPEC = (
    "I check the latest OPEC monthly production report; a cut of more "
    "than 0.5 million barrels per day nudges me toward longs."
)
API = (
    "I review the API weekly inventory change; a draw greater than 5 "
    "million barrels supports a bullish bias."
)
SPREAD = (
    "I compare the spot-price-vs-futures spread. Backwardation — spot "
    "above futures — is bullish; contango — futures above spot — is "
    "bearish. Widening backwardation is more bullish; narrowing "
    "backwardation toward contango is the bullish bias eroding. "
    "Widening contango is more bearish; narrowing contango toward "
    "backwardation is the bearish bias eroding. I avoid new longs only "
    "when the market has moved into contango."
)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE trader_process
        ADD COLUMN IF NOT EXISTS fundamental_opec_cut text NOT NULL DEFAULT ''
    """)
    op.execute("""
        ALTER TABLE trader_process
        ADD COLUMN IF NOT EXISTS fundamental_api_draw text NOT NULL DEFAULT ''
    """)
    op.execute("""
        ALTER TABLE trader_process
        ADD COLUMN IF NOT EXISTS fundamental_spread text NOT NULL DEFAULT ''
    """)
    op.execute("""
        ALTER TABLE trader_process_history
        ADD COLUMN IF NOT EXISTS fundamental_opec_cut text NOT NULL DEFAULT ''
    """)
    op.execute("""
        ALTER TABLE trader_process_history
        ADD COLUMN IF NOT EXISTS fundamental_api_draw text NOT NULL DEFAULT ''
    """)
    op.execute("""
        ALTER TABLE trader_process_history
        ADD COLUMN IF NOT EXISTS fundamental_spread text NOT NULL DEFAULT ''
    """)
    # Seed the three columns from the corrected single paragraph.
    op.execute(f"""
        UPDATE trader_process
        SET fundamental_opec_cut = $o${OPEC}$o$,
            fundamental_api_draw = $a${API}$a$,
            fundamental_spread = $s${SPREAD}$s$
        WHERE deleted_at IS NULL AND fundamental_metrics <> ''
    """)


def downgrade() -> None:
    for tbl in ("trader_process", "trader_process_history"):
        for col in ("fundamental_opec_cut", "fundamental_api_draw",
                    "fundamental_spread"):
            op.execute(f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS {col}")
