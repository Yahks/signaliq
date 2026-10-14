"""ai_credentials.key_fingerprint.

api_key_enc is Fernet ciphertext — non-deterministic by design. Two
encryptions of the same plaintext never match. That made the
duplicate-key pre-check always miss and the UNIQUE (org_id, api_key_enc)
constraint never fire.

key_fingerprint = sha256(plaintext) is deterministic, stable, and safe
to compare. This migration adds the column, backfills existing rows,
and moves the uniqueness constraint onto it.

Revision ID: 0044
Revises: 0043
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add the column, nullable for the backfill.
    op.add_column(
        "ai_credentials",
        sa.Column("key_fingerprint", sa.Text(), nullable=True),
    )

    # 2. Backfill. Decrypting every key requires the application's
    #    cryptography module and the SECRET_KEY — so we do the backfill
    #    in Python, not SQL.
    import hashlib
    from sqlalchemy import text as sql_text

    from packages.core.credentials import decrypt_key

    bind = op.get_bind()
    rows = bind.execute(
        sql_text("SELECT id::text, api_key_enc FROM ai_credentials")
    ).all()
    for row_id, enc in rows:
        try:
            plain = decrypt_key(enc)
            fp = hashlib.sha256(plain.encode("utf-8")).hexdigest()
        except Exception:
            fp = None
        bind.execute(
            sql_text(
                "UPDATE ai_credentials SET key_fingerprint = :fp WHERE id = :id"
            ),
            {"fp": fp, "id": row_id},
        )

    # 3. Drop any leftover rows where the backfill failed (undecryptable
    #    keys — should not exist in practice).
    bind.execute(sql_text(
        "DELETE FROM ai_credentials WHERE key_fingerprint IS NULL"
    ))

    # 4. Merge duplicates that share (org_id, key_fingerprint) — same
    #    physical key stored more than once. Done in three plain steps
    #    rather than one window-function query, to avoid the SQL pattern
    #    that Postgres rejected.

    # 4a. Compute the merged values for each group that has >1 row.
    bind.execute(sql_text("""
        CREATE TEMP TABLE _merged AS
        SELECT org_id,
               key_fingerprint,
               (SELECT array_agg(DISTINCT r ORDER BY r)
                  FROM ai_credentials ac2, unnest(ac2.roles) AS r
                 WHERE ac2.org_id = ac.org_id
                   AND ac2.key_fingerprint = ac.key_fingerprint) AS merged_roles,
               MIN(priority) AS min_priority,
               MIN(created_at) AS first_at
        FROM ai_credentials ac
        WHERE key_fingerprint IS NOT NULL
        GROUP BY org_id, key_fingerprint
        HAVING COUNT(*) > 1
    """))

    # 4b. Update the survivor rows (oldest per group) with the merged
    #     roles + minimum priority.
    bind.execute(sql_text("""
        UPDATE ai_credentials a
        SET roles      = m.merged_roles,
            priority   = m.min_priority,
            is_active  = true
        FROM _merged m
        WHERE a.org_id = m.org_id
          AND a.key_fingerprint = m.key_fingerprint
          AND a.created_at = m.first_at
    """))

    # 4c. Delete the non-survivors in each group.
    bind.execute(sql_text("""
        DELETE FROM ai_credentials a
        USING _merged m
        WHERE a.org_id = m.org_id
          AND a.key_fingerprint = m.key_fingerprint
          AND a.created_at > m.first_at
    """))

    bind.execute(sql_text("DROP TABLE _merged"))

    # 5. NOT NULL now that every row has a fingerprint and duplicates
    #    are gone.
    op.alter_column("ai_credentials", "key_fingerprint", nullable=False)

    # 6. Drop the never-firing constraint and add the correct one.
    op.execute(
        "ALTER TABLE ai_credentials "
        "DROP CONSTRAINT IF EXISTS uq_ai_credentials_org_key"
    )
    op.execute(
        "ALTER TABLE ai_credentials "
        "ADD CONSTRAINT uq_ai_credentials_org_fingerprint "
        "UNIQUE (org_id, key_fingerprint)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE ai_credentials "
        "DROP CONSTRAINT IF EXISTS uq_ai_credentials_org_fingerprint"
    )
    op.execute(
        "ALTER TABLE ai_credentials "
        "ADD CONSTRAINT uq_ai_credentials_org_key "
        "UNIQUE (org_id, api_key_enc)"
    )
    op.drop_column("ai_credentials", "key_fingerprint")
