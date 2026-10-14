"""Auth extension: username + country.

Adds two columns to users:
  - username  varchar(64)  unique, nullable
  - country   varchar(64)  nullable

Backfills existing users with a username derived from their email
prefix (deduped with a numeric suffix if needed).

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("country", sa.String(64), nullable=True))

    # Backfill username from email prefix, ensuring uniqueness
    op.execute("""
        WITH candidates AS (
            SELECT
                id,
                LOWER(SPLIT_PART(email, '@', 1)) AS base,
                ROW_NUMBER() OVER (
                    PARTITION BY LOWER(SPLIT_PART(email, '@', 1))
                    ORDER BY created_at
                ) AS rn
            FROM users
            WHERE username IS NULL
        )
        UPDATE users
        SET username = CASE
            WHEN c.rn = 1 THEN c.base
            ELSE c.base || '_' || c.rn::text
        END
        FROM candidates c
        WHERE users.id = c.id
    """)

    # Now that everything has a username, make it unique + required
    op.create_unique_constraint("uq_users_username", "users", ["username"])


def downgrade() -> None:
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "country")
    op.drop_column("users", "username")
