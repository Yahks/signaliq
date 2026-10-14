"""ai_credentials: the key is the identity, not the label.

Before: UNIQUE (org_id, label). The label was treated as a primary key,
so two keys with the same label collided and the upsert overwrote the
older one. But the label is user-editable display text, not an identity.

After: UNIQUE (org_id, api_key_enc). The key is what makes a credential
distinct. Labels can collide; the display just shows two rows with the
same label.

Revision ID: 0043
Revises: 0042
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ai_credentials "
        "DROP CONSTRAINT IF EXISTS uq_ai_credentials_org_label"
    )
    op.execute(
        "ALTER TABLE ai_credentials "
        "ADD CONSTRAINT uq_ai_credentials_org_key "
        "UNIQUE (org_id, api_key_enc)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_credentials_org_label "
        "ON ai_credentials (org_id, label)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ai_credentials_org_label")
    op.execute(
        "ALTER TABLE ai_credentials "
        "DROP CONSTRAINT IF EXISTS uq_ai_credentials_org_key"
    )
    op.execute(
        "ALTER TABLE ai_credentials "
        "ADD CONSTRAINT uq_ai_credentials_org_label "
        "UNIQUE (org_id, label)"
    )
