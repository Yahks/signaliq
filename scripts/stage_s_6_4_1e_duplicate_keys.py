"""S.6.4.1e — reject duplicate keys and label collisions.

Two behaviors to add to add_provider_key:

1. If the same api_key_enc already exists for this org under any label,
   reject with reason 'duplicate_key' and name the existing label. Prevents
   the same key being registered twice under different names.

2. If the label the user chose is already in use by a DIFFERENT key,
   reject with reason 'label_in_use'. Prevents the upsert from silently
   overwriting the old key with the new one.

Both raise CredentialVerificationError; the classifier gets new cases
for both reasons so the frontend shows readable messages.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
# 1. credentials.py — two new pre-checks in add_provider_key
# ─────────────────────────────────────────────────────────────────────

def step1_prechecks() -> None:
    print("\n[1/3] credentials.py — duplicate + label checks")

    # The check goes right after the label is decided, before the upsert.
    patch(
        CREDS,
        '''    # User-supplied label wins if present. Otherwise hash-derive from the key.
    if label and label.strip():
        final_label = label.strip()[:64]
    else:
        final_label = f"{provider}-{_key_hash(api_key)}"

    cred = await upsert_credential(
        session, org_id,
        provider=provider, model_name=first_model, roles=all_roles,
        label=final_label, api_key=api_key, priority=min_priority,
    )
    return [cred]''',
        '''    # User-supplied label wins if present. Otherwise hash-derive from the key.
    if label and label.strip():
        final_label = label.strip()[:64]
    else:
        final_label = f"{provider}-{_key_hash(api_key)}"

    # ── S.6.4.1e — reject duplicates before the upsert ────────────────
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

    # Check 2: is the label in use by a DIFFERENT key? (Different key means
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

    # ── No collisions — safe to upsert ────────────────────────────────
    cred = await upsert_credential(
        session, org_id,
        provider=provider, model_name=first_model, roles=all_roles,
        label=final_label, api_key=api_key, priority=min_priority,
    )
    return [cred]''',
        "duplicate_key + label_in_use pre-checks",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. error_text.py — classifier cases for the two new reasons
# ─────────────────────────────────────────────────────────────────────

def step2_classifier() -> None:
    print("\n[2/3] error_text.py — new reasons")

    patch(
        ERROR_TEXT,
        '''    if "no_coherent_chain" in lower:''',
        '''    # S.6.4.1e — credentials-specific reasons. These come from
    # add_provider_key via CredentialVerificationError, and the detail
    # is already a user-facing message ("This key is already registered
    # as X"). Just pass it through.
    if "duplicate_key" in lower:
        return {
            "kind": "duplicate_key",
            "message": (
                "This key is already registered for your organisation. "
                "Check your existing keys below."
            ),
            "action": (
                "Delete the existing entry first if you want to replace it, "
                "or use a different key."
            ),
            "provider": provider,
        }

    if "label_in_use" in lower:
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

    if "no_coherent_chain" in lower:''',
        "add duplicate_key + label_in_use cases",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Verify
# ─────────────────────────────────────────────────────────────────────

def step3_verify() -> bool:
    print("\n[3/3] verify")

    # Syntax
    for f in [CREDS, ERROR_TEXT]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ all parse")

    # Stale-identifier grep — catch any remaining `role` references
    print("\n  stale-identifier check (S.6.4.1e learned this the hard way)")
    r = subprocess.run(
        ["grep", "-n", "-E", r"\brole\b", str(CREDS)],
        capture_output=True, text=True,
    )
    hits = [l for l in (r.stdout or "").splitlines()
            if "roles" not in l and "role:" not in l]
    # Filter out comments/docstrings
    real_hits = [l for l in hits
                 if not l.strip().startswith("#")
                 and not l.strip().startswith('"')]
    if real_hits:
        print(f"  ⚠ {len(real_hits)} potential stale 'role' references:")
        for l in real_hits[:10]:
            print(f"     {l}")
    else:
        print("  ✓ no stale singular 'role' references")

    # Classifier unit test
    print("\n  classifier test")
    code = r'''
import sys
sys.path.insert(0, ".")
from packages.ai.error_text import classify
cases = [
    ('This key is already registered as groq-6c35', "duplicate_key"),
    ('duplicate_key: already exists', "duplicate_key"),
    ('label_in_use: my-key', "label_in_use"),
    ('That label is already in use by a different key', "label_in_use"),
    # Existing cases must still work
    ('Groq HTTP 429: rate limit reached', "rate_limit"),
    ('Groq HTTP 401: Invalid API Key', "invalid_key"),
]
ok = True
for raw, expected in cases:
    c = classify(raw)
    got = c.get("kind")
    mark = "✓" if got == expected else "❌"
    print(f"  {mark} {raw[:50]:50} → {got}")
    if got != expected:
        ok = False
raise SystemExit(0 if ok else 1)
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=15,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-300:])
        return False

    # Restart the API cleanly by port, not name
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

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.4.1e — reject duplicate keys and label collisions")
    print("=" * 70)

    step1_prechecks()
    step2_classifier()
    ok = step3_verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.4.1e complete")
        print()
        print("Browser: add the SAME key twice → 'already registered'")
        print("         add a DIFFERENT key with the same label → 'label in use'")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
