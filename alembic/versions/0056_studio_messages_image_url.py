"""S.8.11.1 — studio_messages.image_url.

Adds a nullable image_url column for user-uploaded chart images.
NULL means the message has no attached image.

The image itself lives at apps/api/static/uploads/{uuid}.{ext},
served by the /static mount added in S.8.10.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text as sql_text


revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        ALTER TABLE studio_messages
        ADD COLUMN image_url varchar(500) NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        ALTER TABLE studio_messages DROP COLUMN IF EXISTS image_url
    """))
