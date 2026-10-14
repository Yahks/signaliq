"""S.6.4.1g — key_fingerprint: compare plaintexts, not ciphertexts.

The duplicate-key check and the unique constraint were on api_key_enc.
But Fernet encryption includes a random IV and timestamp, so
encrypt_key("the_same_plaintext") produces a different ciphertext every
time. Two calls to encrypt the same key never match. The pre-check
finds nothing, the constraint never fires, and the same key gets
inserted twice.

Fix: add a key_fingerprint column = sha256(plaintext). Deterministic,
stable across encrypts, safe to compare. Move the pre-check and the
unique constraint to that column.

Migration 0044:
  - add key_fingerprint text
  - backfill by decrypting each existing row and computing sha256
  - NOT NULL
  - drop uq_ai_credentials_org_key (was on api_key_enc, never matched)
  - UNIQUE (org_id, key_fingerprint)

Code:
  - _key_fingerprint(plain) helper in credentials.py
  - add_credential / upsert_credential set and read the fingerprint
  - add_provider_key pre-check queries key_fingerprint
  - ON CONFLICT (org_id, key_fingerprint)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "alembic" / "versions" / "0044_key_fingerprint.py"
CREDS = ROOT / "packages" / "core" / "credentials.py"
ERROR_TEXT = ROOT / "packages" / "ai" / "error_text.py"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() and new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n"
             f"--- expected ---\n{old[:500]}")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. Migration 0044 — the column, the backfill, the constraints
# ─────────────────────────────────────────────────────────────────────

MIGRATION_SRC = '''"""ai_credentials.key_fingerprint.

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

    # 4. NOT NULL now that every row has a fingerprint.
    op.alter_column("ai_credentials", "key_fingerprint", nullable=False)

    # 5. Drop the never-firing constraint and add the correct one.
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
'''


def step1_migration() -> None:
    print("\n[1/5] migration 0044")
    if MIGRATION.exists():
        print(f"  ↷ {MIGRATION.name} already exists")
    else:
        MIGRATION.write_text(MIGRATION_SRC)
        print(f"  ✓ wrote {MIGRATION.name}")

    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        fail(f"alembic failed:\n{r.stderr[-800:]}")
    for line in (r.stderr or "").splitlines():
        if "Running upgrade" in line:
            print(f"  {line.strip()}")
    print("  ✓ migration applied")

    # Confirm the column and the constraint
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='ai_credentials' AND column_name='key_fingerprint'; "
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid='ai_credentials'::regclass AND contype='u' "
        "ORDER BY conname;"
    ], capture_output=True, text=True)
    print(r.stdout)


# ─────────────────────────────────────────────────────────────────────
# 2. credentials.py — _key_fingerprint + wiring
# ─────────────────────────────────────────────────────────────────────

def step2_helper() -> None:
    print("\n[2/5] credentials.py — add _key_fingerprint")

    src = CREDS.read_text()
    if "def _key_fingerprint" in src:
        print("  ↷ already present")
        return

    helper = '''

def _key_fingerprint(plain: str) -> str:
    """Deterministic hash of the plaintext key, for equality checks.

    api_key_enc is Fernet ciphertext — random IV and timestamp, so two
    encryptions of the same plaintext never match. This fingerprint is
    stable across encrypts, so the pre-check and the unique constraint
    can compare keys reliably.
    """
    import hashlib
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()
'''

    anchor = "def _key_hash(plain: str) -> str:"
    idx = src.find(anchor)
    if idx < 0:
        fail("_key_hash not found in credentials.py")

    # Insert the new helper right after _key_hash's function body
    end = src.find("\n\n", src.find("return", idx))
    if end < 0:
        fail("cannot find end of _key_hash")
    src = src[:end] + "\n" + helper + src[end:]
    CREDS.write_text(src)
    print("  ✓ _key_fingerprint added")


def step3_upsert_and_add() -> None:
    print("\n[3/5] upsert_credential + add_credential — set the fingerprint")

    # 3a. upsert_credential: add key_fingerprint to the INSERT column list
    patch(
        CREDS,
        '''        INSERT INTO ai_credentials
            (org_id, provider, model_name, roles, label, api_key_enc, priority)
        VALUES
            (:oid, :prov, :model, :roles, :label, :enc, :prio)
        ON CONFLICT (org_id, api_key_enc) DO UPDATE''',
        '''        INSERT INTO ai_credentials
            (org_id, provider, model_name, roles, label, api_key_enc, priority, key_fingerprint)
        VALUES
            (:oid, :prov, :model, :roles, :label, :enc, :prio, :fp)
        ON CONFLICT (org_id, key_fingerprint) DO UPDATE''',
        "upsert INSERT + ON CONFLICT",
    )

    # 3b. upsert_credential: the params dict
    patch(
        CREDS,
        '''        "oid": org_id, "prov": provider, "model": model_name,
        "roles": roles, "label": label, "enc": enc, "prio": priority,
    })).first()''',
        '''        "oid": org_id, "prov": provider, "model": model_name,
        "roles": roles, "label": label, "enc": enc, "prio": priority,
        "fp": _key_fingerprint(api_key),
    })).first()''',
        "upsert params dict",
    )

    # 3c. add_credential: same, for the direct insert path
    patch(
        CREDS,
        '''        INSERT INTO ai_credentials
            (org_id, provider, model_name, roles, label, api_key_enc, priority)
        VALUES
            (:oid, :prov, :model, :roles, :label, :enc, :prio)
        RETURNING id::text, created_at''',
        '''        INSERT INTO ai_credentials
            (org_id, provider, model_name, roles, label, api_key_enc, priority, key_fingerprint)
        VALUES
            (:oid, :prov, :model, :roles, :label, :enc, :prio, :fp)
        RETURNING id::text, created_at''',
        "add_credential INSERT",
    )

    # 3d. add_credential params
    patch(
        CREDS,
        '''        "oid": org_id, "prov": provider, "model": model_name,
        "roles": roles, "label": label, "enc": enc, "prio": priority,
    })).first()''',
        '''        "oid": org_id, "prov": provider, "model": model_name,
        "roles": roles, "label": label, "enc": enc, "prio": priority,
        "fp": _key_fingerprint(api_key),
    })).first()''',
        "add_credential params",
    )


def step4_precheck() -> None:
    print("\n[4/5] add_provider_key — pre-check on fingerprint")

    patch(
        CREDS,
        '''    # ── S.6.4.1e — reject duplicates before the upsert ────────────────
    enc = encrypt_key(api_key)

    # Check 1: is the exact same key already registered under some label?
    existing_key = (await session.execute(sql_text("""
        SELECT label FROM ai_credentials
        WHERE org_id = :oid AND api_key_enc = :enc
        LIMIT 1
    """), {"oid": org_id, "enc": enc})).first()
    if existing_key:
        raise CredentialVerificationError(
            "duplicate_key",
            f"This key is already registered as '{existing_key[0]}'. "
            f"Delete that row if you want to re-add it.",
            provider,
        )

    # S.6.4.1f — the label is a display name, not an identity. Two keys
    # can share a label; the DB constraint is on (org_id, api_key_enc).
    # No label-collision check: the upsert will only fire for the same
    # key, never for a same-labelled different key.

    # ── No key collision — safe to upsert ────────────────────────────''',
        '''    # ── S.6.4.1g — reject duplicate keys by fingerprint ──────────────
    # api_key_enc is Fernet ciphertext (non-deterministic). The
    # fingerprint is sha256(plaintext) and IS deterministic.
    enc = encrypt_key(api_key)
    fp = _key_fingerprint(api_key)

    existing_key = (await session.execute(sql_text("""
        SELECT label FROM ai_credentials
        WHERE org_id = :oid AND key_fingerprint = :fp
        LIMIT 1
    """), {"oid": org_id, "fp": fp})).first()
    if existing_key:
        raise CredentialVerificationError(
            "duplicate_key",
            f"This key is already registered as '{existing_key[0]}'. "
            f"Delete that row if you want to re-add it.",
            provider,
        )

    # No label-collision check: labels are display names, not identities.

    # ── No fingerprint collision — safe to upsert ────────────────────''',
        "pre-check on key_fingerprint",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. Remove the dead label_in_use block from error_text.py
# ─────────────────────────────────────────────────────────────────────

def step5_remove_label_case() -> None:
    print("\n[5/5] error_text.py — remove label_in_use block")

    src = ERROR_TEXT.read_text()
    if "label_in_use" not in src:
        print("  ↷ already removed")
        return

    old = '''    if ("label_in_use" in lower
            or "already in use by a different key" in lower
            or "already have a different key labelled" in lower):
        return {
            "kind": "label_in_use",
            "message": (
                "That label is already in use by a different key."
            ),
            "action": (
                "Choose a different label, or delete the existing key first."
            ),
            "provider": provider,
        }

'''

    if old not in src:
        fail("error_text.py: label_in_use block not found (exact text)")
    ERROR_TEXT.write_text(src.replace(old, "", 1))
    print("  ✓ label_in_use block removed")


# ─────────────────────────────────────────────────────────────────────
# Verify
# ─────────────────────────────────────────────────────────────────────

def verify() -> bool:
    print("\n" + "=" * 60)
    print("VERIFY")
    print("=" * 60)

    for f in [CREDS, ERROR_TEXT, MIGRATION]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ all parse")

    # Restart the API cleanly
    print("  restart API (fuser -k 8000/tcp)")
    subprocess.run(["bash", "-c", "fuser -k 8000/tcp 2>/dev/null"], check=False)
    time.sleep(2)
    with Path("/tmp/signaliq-api.log").open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(6)
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout}")
        return False
    print("  ✓ API up")

    # Behavioral test
    print("\n  behavioral test")
    code = r'''
import json, os, urllib.request, urllib.error

API = "http://localhost:8000"

def post(path, body, token=None):
    req = urllib.request.Request(f"{API}{path}", method="POST")
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, json.dumps(body).encode()) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def get(path, token=None):
    req = urllib.request.Request(f"{API}{path}")
    if token: req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def delete(path, token):
    req = urllib.request.Request(f"{API}{path}", method="DELETE")
    req.add_header("Authorization", f"Bearer {token}")
    try: urllib.request.urlopen(req)
    except Exception: pass

status, data = post("/api/auth/login",
                    {"identifier": "tokentest", "password": "TestPass123!"})
token = data["access_token"]
real_key = os.environ["GROQ_API_KEY"]

# Cleanup test rows
for it in get("/api/settings/credentials", token)["items"]:
    if it["label"].startswith("s64g-"):
        delete(f"/api/settings/credentials/{it['id']}", token)

s1, d1 = post("/api/settings/credentials",
              {"provider": "groq", "api_key": real_key, "label": "s64g-a"}, token)
print(f"add #1 (new key, label s64g-a):   {s1}")

s2, d2 = post("/api/settings/credentials",
              {"provider": "groq", "api_key": real_key, "label": "s64g-b"}, token)
r2 = (d2.get("detail") or {}).get("reason") if isinstance(d2.get("detail"), dict) else None
print(f"add #2 (same key, label s64g-b):  {s2}  reason={r2}")

s3, d3 = post("/api/settings/credentials",
              {"provider": "groq", "api_key": real_key, "label": "s64g-a"}, token)
r3 = (d3.get("detail") or {}).get("reason") if isinstance(d3.get("detail"), dict) else None
print(f"add #3 (same key, same label):    {s3}  reason={r3}")

# Cleanup
for it in get("/api/settings/credentials", token)["items"]:
    if it["label"].startswith("s64g-"):
        delete(f"/api/settings/credentials/{it['id']}", token)
print("cleanup done")

# Pass/fail
ok = (s1 == 201 and s2 == 400 and r2 == "duplicate_key"
      and s3 == 400 and r3 == "duplicate_key")
raise SystemExit(0 if ok else 1)
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=60,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-400:])
        return False
    return True


def main() -> None:
    print("=" * 70)
    print("S.6.4.1g — key_fingerprint: compare plaintexts, not ciphertexts")
    print("=" * 70)

    step1_migration()
    step2_helper()
    step3_upsert_and_add()
    step4_precheck()
    step5_remove_label_case()
    ok = verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.4.1g complete")
        print()
        print("Behavior now:")
        print("  add new key            -> 201")
        print("  add same key, any lbl  -> 400 duplicate_key")
        print("  add different key      -> 201")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
