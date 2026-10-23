"""market_condition_rules: per-regime rules as rows, not a dict.

S.9.74 — the regime -> rule mapping was a hardcoded Python dict
(ADJUSTMENTS_BY_REGIME). That made it unversionable and
un-editable per org. This table holds the same seven rules as
rows, seeded from the dict for every org that has a
market_conditions row, so existing behaviour is unchanged.

The skeleton reads the table and falls back to the dict when an
org has no rows.

Revision ID: 0089
Revises: 0088
Create Date: 2026-10-23
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0089"
down_revision: str | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULES = [
    ("Compressed-Volatility",
     "cut base size to 0.5 unit, tighten stops to 0.5%, suspend the "
     "0.75% stop-loss rule"),
    ("Expanded-Volatility",
     "keep full size, raise the profit target to 2%, widen the "
     "trailing stop to 0.7%"),
    ("Thin-Liquidity",
     "use limit orders, split orders into \u22640.25-unit chunks, avoid "
     "trades that would exceed a 0.5 bp slippage estimate"),
    ("High-Correlation",
     "cap total concurrent units at 3, skip the lowest-edge breakout "
     "signal"),
    ("Narrow-Breadth",
     "skip the day's scan entirely unless the volatility index exceeds "
     "its 20-day average by 2\u03c3 AND the positioning survey is below "
     "70% net-long"),
    ("Elevated-Volatility-Index",
     "raise the stop-loss to 1%, trade only the breakout micro-strategy"),
    ("Crowded-Longs",
     "exit any open long at market close, ban new longs until the "
     "positioning survey falls below 75% net-long"),
]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS market_condition_rules (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            regime text NOT NULL,
            rule_text text NOT NULL,
            effective_from timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            deleted_at timestamptz
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_mcr_org_regime
        ON market_condition_rules (org_id, regime)
        WHERE deleted_at IS NULL
    """)
    # Seed: one row per (org, regime) for every org with a live
    # market_conditions row. Dollar-quoted so the rule text (which
    # contains commas, ≤, σ) needs no escaping.
    for regime, rule in RULES:
        op.execute(f"""
            INSERT INTO market_condition_rules (org_id, regime, rule_text)
            SELECT DISTINCT mc.org_id, '{regime}', $r${rule}$r$
            FROM market_conditions mc
            WHERE mc.deleted_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM market_condition_rules r
                WHERE r.org_id = mc.org_id
                  AND r.regime = '{regime}' AND r.deleted_at IS NULL
              )
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_mcr_org_regime")
    op.execute("DROP TABLE IF EXISTS market_condition_rules")
