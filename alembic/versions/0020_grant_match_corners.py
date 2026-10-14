"""Grant access to match_corners for the app role.

Migration 0019 forgot to GRANT on match_corners. This fixes it.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON match_corners TO signaliq_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON match_corners FROM signaliq_app")
