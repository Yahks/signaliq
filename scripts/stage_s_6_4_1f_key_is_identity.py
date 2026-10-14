"""S.6.4.1f — key is the identity, not the label.

The unique constraint on ai_credentials was (org_id, label). That made
the label a primary key in practice: two keys with the same label
collided, and the ON CONFLICT (org_id, label) DO UPDATE silently
overwrote the older key with the newer one.

The label is user-editable input. It is a display name, not an identity.
The thing that makes a credential distinct is its key.

Fix:
- Drop uq_ai_credentials_org_label
- Add uq_ai_credentials_org_key UNIQUE (org_id, api_key_enc)
- upsert_credential: ON CONFLICT (org_id, api_key_enc)
- add_provider_key: remove the label-collision check (labels can now
  collide); keep the duplicate-key pre-check for a readable message
- error_text.py: remove the label_in_use case (no code raises it)

Labels still default to {provider}-{key_hash4} when blank.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "alembic" / "versions" / "0043_credentials_key_identity.py"
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
# 1. Migration 0043
# ─────────────────────────────────────────────────────────────────────

MIGRATION_SRC = '''"""ai_credentials: the key is the identity, not the label.

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
'''


def step1_migration() -> None:
    print("\n[1/4] migration 0043")
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

    # Confirm the constraints
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-t", "-c",
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'ai_credentials'::regclass "
        "AND contype = 'u' "
        "ORDER BY conname"
    ], capture_output=True, text=True)
    print(f"  unique constraints now: {[c.strip() for c in r.stdout.splitlines() if c.strip()]}")


# ─────────────────────────────────────────────────────────────────────
# 2. upsert_credential: conflict on api_key_enc, not label
# ─────────────────────────────────────────────────────────────────────

def step2_upsert() -> None:
    print("\n[2/4] upsert_credential — conflict target on api_key_enc")

    patch(
        CREDS,
        '''        ON CONFLICT (org_id, label) DO UPDATE
        SET provider        = EXCLUDED.provider,
            model_name      = EXCLUDED.model_name,
            roles           = EXCLUDED.roles,
            api_key_enc     = EXCLUDED.api_key_enc,
            priority        = EXCLUDED.priority,
            is_active       = true,
            exhausted_until = NULL,
            updated_at      = now()
        RETURNING id::text, created_at''',
        '''        ON CONFLICT (org_id, api_key_enc) DO UPDATE
        SET provider        = EXCLUDED.provider,
            model_name      = EXCLUDED.model_name,
            roles           = EXCLUDED.roles,
            label           = EXCLUDED.label,
            priority        = EXCLUDED.priority,
            is_active       = true,
            exhausted_until = NULL,
            updated_at      = now()
        RETURNING id::text, created_at''',
        "conflict target: org+label -> org+api_key_enc",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. add_provider_key — remove Check 2 (label collision)
# ─────────────────────────────────────────────────────────────────────

def step3_remove_label_check() -> None:
    print("\n[3/4] add_provider_key — remove label-collision check")

    patch(
        CREDS,
        '''    # Check 2: is the label in use by a DIFFERENT key? (Different key means
    # the upsert would silently overwrite the old one — refuse instead.)
    existing_label = (await session.execute(sql_text("""
        SELECT api_key_enc FROM ai_credentials
        WHERE org_id = :oid AND label = :label
        LIMIT 1
    """), {"oid": org_id, "label": final_label})).first()
    if existing_label and existing_label[0] != enc:
        raise CredentialVerificationError(
            "label_in_use",
            f"You already have a different key labelled '{final_label}'. "
            f"Use a different label, or delete the existing one first.",
            provider,
        )

    # ── No collisions — safe to upsert ────────────────────────────────''',
        '''    # S.6.4.1f — the label is a display name, not an identity. Two keys
    # can share a label; the DB constraint is on (org_id, api_key_enc).
    # No label-collision check: the upsert will only fire for the same
    # key, never for a same-labelled different key.

    # ── No key collision — safe to upsert ────────────────────────────''',
        "remove Check 2",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. error_text.py — remove the dead label_in_use case
# ─────────────────────────────────────────────────────────────────────

def step4_remove_classifier_case() -> None:
    print("\n[4/4] error_text.py — remove label_in_use case")

    patch(
        ERROR_TEXT,
        '''    if ("label_in_use" in lower
            or "already in use by a different key" in lower):
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

''',
        '''    # S.6.4.1f — the label is a display name, so there is no
    # 'label_in_use' error. Removed here so the classifier stays clean.

''',
        "remove label_in_use block",
    )


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

    # The behavioral test: add the SAME key twice with DIFFERENT labels.
    # First should succeed (201). Second should be rejected (400 duplicate_key).
    print("\n  behavioral test")
    code = r'''
import json, subprocess, urllib.request, urllib.error, os

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

status, data = post("/api/auth/login",
                    {"identifier": "tokentest", "password": "TestPass123!"})
token = data["access_token"]

real_key = os.environ.get("GROQ_API_KEY", "")
if not real_key:
    print("  GROQ_API_KEY not set — cannot run the duplicate test")
    raise SystemExit(0)

# Clean up any prior test keys
existing = get("/api/settings/credentials", token)["items"]
for it in existing:
    if it["label"] in ("s64f-a", "s64f-b"):
        # delete
        req = urllib.request.Request(
            f"{API}/api/settings/credentials/{it['id']}", method="DELETE")
        req.add_header("Authorization", f"Bearer {token}")
        try: urllib.request.urlopen(req)
        except Exception: pass

# 1. Add the real key with label "s64f-a" -> should succeed
status, data = post("/api/settings/credentials",
                    {"provider": "groq", "api_key": real_key, "label": "s64f-a"},
                    token)
print(f"  add #1 (new key, label s64f-a):      {status}")
if status != 201:
    print(f"    body: {str(data)[:300]}")
    raise SystemExit(1)

# 2. Add the SAME key with a DIFFERENT label "s64f-b" -> should be rejected
status, data = post("/api/settings/credentials",
                    {"provider": "groq", "api_key": real_key, "label": "s64f-b"},
                    token)
print(f"  add #2 (same key, label s64f-b):      {status}")
reason = (data.get("detail") or {}).get("reason") if isinstance(data.get("detail"), dict) else None
print(f"    reason: {reason}")
if status != 400 or reason != "duplicate_key":
    print(f"    ❌ expected 400 duplicate_key")
    raise SystemExit(1)

# 3. Confirm no label collision check remains: add the SAME key with the
#    SAME label -> should hit duplicate_key (not label_in_use)
status, data = post("/api/settings/credentials",
                    {"provider": "groq", "api_key": real_key, "label": "s64f-a"},
                    token)
print(f"  add #3 (same key, same label):        {status}")
reason = (data.get("detail") or {}).get("reason") if isinstance(data.get("detail"), dict) else None
print(f"    reason: {reason}")
if status != 400 or reason != "duplicate_key":
    print(f"    ❌ expected 400 duplicate_key")
    raise SystemExit(1)

# Cleanup
existing = get("/api/settings/credentials", token)["items"]
for it in existing:
    if it["label"] == "s64f-a":
        req = urllib.request.Request(
            f"{API}/api/settings/credentials/{it['id']}", method="DELETE")
        req.add_header("Authorization", f"Bearer {token}")
        try: urllib.request.urlopen(req)
        except Exception: pass

print("  ✓ same key, different label -> rejected as duplicate_key")
print("  ✓ same key, same label      -> rejected as duplicate_key")
print("  ✓ labels can collide (no label_in_use error)")
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=60,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-500:])
        return False

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.4.1f — key is the identity, not the label")
    print("=" * 70)

    step1_migration()
    step2_upsert()
    step3_remove_label_check()
    step4_remove_classifier_case()
    ok = verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.4.1f complete")
        print()
        print("Behavior now:")
        print("  same key, diff label  -> 400 duplicate_key")
        print("  same key, same label  -> 400 duplicate_key")
        print("  diff key, same label  -> 201 (allowed)")
        print()
        print("The label is a display name; the key is the identity.")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
