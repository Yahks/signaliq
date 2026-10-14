"""S.7.3 — onboarding state on orgs.

Two nullable timestamptz columns on orgs:

  onboarding_guide_seen_at    when the user clicked through the how-to
  onboarding_completed_at     when the user finished picking blocks

Three states:

  both NULL            brand-new org — show the guide, then the picker
  guide_seen_at set    user saw the guide, has not picked blocks yet
  both set             fully onboarded

No constraint ties the two together — a user could re-open the guide
later without resetting completion, and the API treats "completed is
set" as the authoritative "onboarded" flag.

Existing rows are backfilled to now(). Every org in the dev DB
predates onboarding; sending them back through the picker would be a
surprise. They already have org_block_settings rows (news+trading)
which is the ground truth of "what they use."

Guardrail: this migration does NOT create org_block_settings rows.
A new org will sign up with no rows and zero blocks enabled — which is
the design. The picker (S.7.5) writes the rows.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text as sql_text


revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(sql_text("""
        ALTER TABLE orgs
        ADD COLUMN onboarding_guide_seen_at timestamptz,
        ADD COLUMN onboarding_completed_at timestamptz
    """))

    # Backfill: every existing org has been using the system without
    # onboarding. Mark them as fully onboarded so they are not sent
    # back through the guide + picker on their next login.
    bind.execute(sql_text("""
        UPDATE orgs
        SET onboarding_guide_seen_at = now(),
            onboarding_completed_at = now()
        WHERE onboarding_completed_at IS NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sql_text("""
        ALTER TABLE orgs
        DROP COLUMN IF EXISTS onboarding_guide_seen_at,
        DROP COLUMN IF EXISTS onboarding_completed_at
    """))


if __name__ == "__main__":
    print("Revision:", revision)
    print("Down revision:", down_revision)
    print("Adds orgs.onboarding_guide_seen_at, orgs.onboarding_completed_at")
    print("Backfills all existing orgs to now() (already onboarded).")
