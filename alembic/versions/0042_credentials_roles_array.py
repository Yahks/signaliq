"""ai_credentials: role → roles[].

Before: one row per (provider, role) pair. Adding a second Groq key
overwrote the first because the label was deterministic
(`{provider}-{role}`) and upsert used `(org_id, label)` as the
uniqueness key.

After: one row per key. Roles it serves are an array. Adding multiple
keys of the same provider now augments the rotation pool.

Data migration:
  - every existing row's single role becomes a 1-element array
  - no rows are merged (verified: no two rows share an encrypted key)
  - the `role` column is dropped after backfill

Revision ID: 0042
Revises: 0041
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_credentials",
        sa.Column("roles", sa.ARRAY(sa.Text()), nullable=True),
    )
    op.execute("UPDATE ai_credentials SET roles = ARRAY[role]")
    op.execute("ALTER TABLE ai_credentials ALTER COLUMN roles SET NOT NULL")
    op.drop_column("ai_credentials", "role")
    op.execute(
        "CREATE INDEX ix_ai_credentials_roles "
        "ON ai_credentials USING gin (roles)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ai_credentials_roles")
    op.add_column(
        "ai_credentials",
        sa.Column("role", sa.String(16), nullable=True),
    )
    op.execute("UPDATE ai_credentials SET role = roles[1]")
    op.execute("ALTER TABLE ai_credentials ALTER COLUMN role SET NOT NULL")
    op.drop_column("ai_credentials", "roles")
