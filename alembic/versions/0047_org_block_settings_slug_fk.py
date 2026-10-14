"""S.7.1 — enforce org_block_settings.block_slug against blocks.slug.

Before this migration, org_block_settings.block_slug is a free-form
varchar. Nothing prevents a row with slug='typo' or a slug for a block
that doesn't exist. The onboarding picker (S.7.5) will only present
blocks that have a `blocks` row, but the DB has no such guarantee.

Fix: make `blocks.slug` unique, then add a foreign key from
org_block_settings.block_slug to blocks.slug.

Why a FK and not a CHECK:
  A CHECK would hardcode the canonical slug set {football, trading,
  news, gossip, jobs}. Adding a sixth block later would require a new
  migration just to update the CHECK. A FK against blocks.slug means
  "you can enable a block if and only if it exists in the blocks
  registry" — self-maintaining, and the exact invariant we want.

Preconditions (already true in the dev DB):
  - blocks has rows for football, news, trading (all is_system=true)
  - org_block_settings contains only 'news' and 'trading' today,
    both of which are present in blocks
  - blocks.slug currently has no UNIQUE constraint (this adds one)

Postconditions:
  - blocks.slug is UNIQUE
  - org_block_settings.block_slug REFERENCES blocks.slug
  - An INSERT or UPDATE to org_block_settings with an unknown slug
    fails at the DB layer, not just at the API layer.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text as sql_text


revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. blocks.slug must be unique before it can be an FK target.
    #    Postgres lets you add a UNIQUE constraint while there's only
    #    one row per slug (which is the case). If a duplicate exists,
    #    the ALTER will fail — that would be a real data problem and
    #    should stop the migration.
    bind.execute(sql_text(
        "ALTER TABLE blocks ADD CONSTRAINT uq_blocks_slug UNIQUE (slug)"
    ))

    # 2. Add the FK. If org_block_settings currently holds a slug that
    #    isn't in blocks, this fails and the transaction rolls back.
    #    That's the correct behavior: we want to know.
    bind.execute(sql_text("""
        ALTER TABLE org_block_settings
        ADD CONSTRAINT fk_org_block_settings_slug
        FOREIGN KEY (block_slug) REFERENCES blocks(slug)
        ON UPDATE CASCADE ON DELETE RESTRICT
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        ALTER TABLE org_block_settings
        DROP CONSTRAINT IF EXISTS fk_org_block_settings_slug
    """))
    bind.execute(sql_text(
        "ALTER TABLE blocks DROP CONSTRAINT IF EXISTS uq_blocks_slug"
    ))


if __name__ == "__main__":
    print("Revision:", revision)
    print("Down revision:", down_revision)
    print("Effect:")
    print("  - UNIQUE on blocks.slug")
    print("  - FK org_block_settings.block_slug -> blocks.slug")
    print()
    print("Apply:   alembic upgrade head")
    print("Rollback: alembic downgrade -1")
