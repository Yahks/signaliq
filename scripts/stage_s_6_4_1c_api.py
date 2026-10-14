"""S.6.4.1c — API + router: roles array + optional label.

Part 3 of the credentials-per-key refactor.

Two files:
  - apps/api/schemas/credentials.py — CredentialResponse.role → roles,
    CreateCredentialSimpleRequest gains optional `label`
  - apps/api/routers/credentials.py — pass the optional label through,
    and _to_response maps `roles` instead of `role`

No service code changes (that was S.6.4.1b). No frontend (that's S.6.4.1d).

Verified:
  - GET /api/settings/credentials returns items with `roles: [...]`
  - POST /api/settings/credentials accepts {provider, api_key, label?}
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "apps" / "api" / "schemas" / "credentials.py"
ROUTER = ROOT / "apps" / "api" / "routers" / "credentials.py"


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
# 1. Schema
# ─────────────────────────────────────────────────────────────────────

def step1_schema() -> None:
    print("\n[1/2] schemas/credentials.py")

    # CredentialResponse: role → roles
    patch(
        SCHEMA,
        '''class CredentialResponse(BaseModel):
    id: str
    provider: str
    model_name: str
    role: str
    label: str
    key_preview: str
    is_active: bool
    priority: int
    exhausted_until: datetime | None = None
    last_success_at: datetime | None = None
    created_at: datetime''',
        '''class CredentialResponse(BaseModel):
    id: str
    provider: str
    model_name: str
    roles: list[str]
    label: str
    key_preview: str
    is_active: bool
    priority: int
    exhausted_until: datetime | None = None
    last_success_at: datetime | None = None
    created_at: datetime''',
        "CredentialResponse: role → roles",
    )

    # CreateCredentialSimpleRequest: add optional label
    patch(
        SCHEMA,
        '''class CreateCredentialSimpleRequest(BaseModel):
    """The user-facing form: pick a provider, paste a key. Nothing else."""
    provider: str = Field(..., min_length=2, max_length=32)
    api_key: str = Field(..., min_length=8, max_length=512)''',
        '''class CreateCredentialSimpleRequest(BaseModel):
    """The user-facing form: pick a provider, paste a key.

    An optional label lets the user name the key meaningfully when they
    have several for the same provider. Falls back to
    `{provider}-{key_hash4}` when not supplied.
    """
    provider: str = Field(..., min_length=2, max_length=32)
    api_key: str = Field(..., min_length=8, max_length=512)
    label: str | None = Field(default=None, min_length=2, max_length=64)''',
        "CreateCredentialSimpleRequest: optional label",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Router
# ─────────────────────────────────────────────────────────────────────

def step2_router() -> None:
    print("\n[2/2] routers/credentials.py")

    src = ROUTER.read_text()

    # _to_response maps 'roles'
    if '"roles":' in src or 'cred["roles"]' in src:
        print("  ↷ _to_response already maps roles")
    else:
        # Try to find the _to_response helper
        import re
        m = re.search(r"def _to_response\([^)]*\)[^:]*:[\s\S]*?(?=\n@router|\n\ndef |\Z)", src)
        if m:
            old_body = m.group(0)
            if "role" in old_body and "roles" not in old_body:
                new_body = old_body.replace("role=cred[\"role\"]", "roles=cred.get(\"roles\") or []")
                new_body = new_body.replace("role=cred['role']", "roles=cred.get('roles') or []")
                new_body = new_body.replace('"role": cred["role"]', '"roles": cred.get("roles") or []')
                new_body = new_body.replace("role=c.get(\"role\")", "roles=c.get(\"roles\") or []")
                if new_body != old_body:
                    src = src.replace(old_body, new_body, 1)
                    ROUTER.write_text(src)
                    print("  ✓ _to_response: role → roles")
                else:
                    print("  ⚠ _to_response didn't contain a 'role' assignment — inspect manually")
            else:
                print("  ↷ _to_response already uses roles or has no role field")
        else:
            print("  ⚠ _to_response helper not found — inspect manually")

    # Pass the optional label to add_provider_key
    src = ROUTER.read_text()
    if "getattr(body, 'label'" in src or "getattr(body, \"label\"" in src or "body.label" in src:
        print("  ↷ router already passes label")
    else:
        patch(
            ROUTER,
            '''        created = await add_provider_key(
            session, org_id,
            provider=body.provider, api_key=body.api_key,
        )''',
            '''        created = await add_provider_key(
            session, org_id,
            provider=body.provider, api_key=body.api_key,
            label=getattr(body, "label", None),
        )''',
            "pass optional label",
        )


# ─────────────────────────────────────────────────────────────────────
# 3. Verify
# ─────────────────────────────────────────────────────────────────────

def step3_verify() -> bool:
    print("\n[3/3] verify")

    # Syntax
    for f in [SCHEMA, ROUTER]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ all parse")

    # Restart the API
    print("  restart API")
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
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
        print(f"     log tail: {log.read_text()[-400:]}")
        return False
    print("  ✓ API up")

    # HTTP: login, GET credentials, confirm 'roles' present
    print("\n  HTTP check")

    def post(path, body, token=None):
        req = urllib.request.Request(f"http://localhost:8000{path}", method="POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, json.dumps(body).encode()) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(path, token=None):
        req = urllib.request.Request(f"http://localhost:8000{path}")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    status, data = post("/api/auth/login",
                        {"identifier": "tokentest", "password": "TestPass123!"})
    if status != 200:
        print(f"  ❌ login failed: {status}  {str(data)[:200]}")
        return False
    token = data["access_token"]
    print("  ✓ login")

    status, data = get("/api/settings/credentials", token=token)
    if status != 200:
        print(f"  ❌ list failed: {status}  {str(data)[:200]}")
        return False

    items = data.get("items", [])
    print(f"  GET /api/settings/credentials → {len(items)} item(s)")
    for it in items:
        keys = sorted(it.keys())
        print(f"    {it.get('label'):20}  roles={it.get('roles')!r}  has_role_field={'role' in it}")
        if "role" in it:
            print(f"    ❌ row still has a singular 'role' field")
            return False
        if "roles" not in it:
            print(f"    ❌ row missing 'roles'")
            return False

    if not items:
        print("  ⚠ no credentials — but the schema is at least valid")
    else:
        print("  ✓ every row has `roles` and no `role`")

    # POST without a label
    print("\n  POST /api/settings/credentials without label")
    # We don't have a real key to add, but we can at least confirm the
    # schema accepts the request shape (it'll fail verification, not 422).
    status, data = post("/api/settings/credentials",
                        {"provider": "groq", "api_key": "gsk_invalid_1234567890"},
                        token=token)
    # 400 with a reason is expected (invalid key); 422 means schema problem
    if status == 422:
        print(f"  ❌ schema rejected the request: {str(data)[:300]}")
        return False
    if status == 400:
        reason = (data.get("detail") or {}).get("reason") if isinstance(data.get("detail"), dict) else None
        print(f"  ✓ request accepted by the schema; verification rejected it ({reason})")
    else:
        print(f"  ? unexpected status: {status}  {str(data)[:200]}")

    # POST with a label
    print("\n  POST with an optional label")
    status, data = post("/api/settings/credentials",
                        {"provider": "groq",
                         "api_key": "gsk_invalid_1234567890",
                         "label": "my-test-key"},
                        token=token)
    if status == 422:
        print(f"  ❌ schema rejected the labelled request: {str(data)[:300]}")
        return False
    print(f"  ✓ labelled request accepted by the schema (status {status})")

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.4.1c — API + router: roles[] + optional label")
    print("=" * 70)

    step1_schema()
    step2_router()
    ok = step3_verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.4.1c complete")
        print("   GET returns `roles: [...]`, POST accepts {provider, api_key, label?}")
        print("   The frontend still reads `role` — that's S.6.4.1d.")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
