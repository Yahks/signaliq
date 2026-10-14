"""
SignalIQ — Phase S.1.3: Restore username + country to RegisterRequest.

My S.1 removal regex was too greedy — it stripped username + country
alongside org_name + org_slug. The router still reads body.username
and body.country, so register 500s.

Fix: add both fields back to RegisterRequest.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "apps" / "api" / "schemas" / "auth.py"


def step1_fix_schema() -> None:
    print("\n[1/3] Restore username + country to RegisterRequest")

    src = SCHEMA.read_text()

    # Check current state
    import re
    m = re.search(
        r'class RegisterRequest\(BaseModel\):(.*?)(?=\nclass )',
        src, re.DOTALL,
    )
    if not m:
        raise SystemExit("❌ RegisterRequest class not found")

    block = m.group(0)
    if "username" in block and "country" in block:
        print("  ↷ RegisterRequest already has username + country")
        return

    # Insert username + country right after the email field
    old = '    email: EmailStr\n'
    new = (
        '    email: EmailStr\n'
        '    username: str | None = Field(default=None, min_length=3, max_length=64)\n'
        '    country: str | None = Field(default=None, max_length=64)\n'
    )

    if old not in src:
        raise SystemExit("❌ 'email: EmailStr' anchor not found")

    src = src.replace(old, new, 1)
    SCHEMA.write_text(src)
    print("  ✓ added username + country fields")


def step2_verify() -> bool:
    print("\n[2/3] Verify schema loads")
    r = subprocess.run([
        "python", "-c", """
from apps.api.schemas.auth import RegisterRequest
print("fields:")
for name, field in RegisterRequest.model_fields.items():
    print(f"  {name:20} required={field.is_required()}")
"""
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-500:])
        return False
    return "username" in r.stdout and "country" in r.stdout


def step3_restart_and_test() -> bool:
    print("\n[3/3] Restart API + register test")

    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(5)

    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout}")
        return False
    print("  ✓ /health 200")

    # Register test
    r = subprocess.run([
        "curl", "-s", "-X", "POST", "http://localhost:8000/api/auth/register",
        "-H", "Content-Type: application/json",
        "-d", '''{
            "email": "newtest@example.com",
            "username": "newtest",
            "password": "TestPass123!",
            "full_name": "New Test User",
            "country": "Nigeria",
            "accepted_terms": true,
            "accepted_age": true,
            "accepted_risk": true
        }''',
        "-w", "\n\nHTTP %{http_code}",
    ], capture_output=True, text=True, timeout=30)
    print(r.stdout[:800])
    if "HTTP 200" not in r.stdout:
        # Show the API log tail
        log_tail = subprocess.run(
            ["tail", "-15", "/tmp/signaliq-api.log"],
            capture_output=True, text=True,
        )
        print("--- API log ---")
        print(log_tail.stdout)
        return False

    # DB check
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        """
        SELECT u.email, u.username, u.country,
               o.slug AS org_slug, o.name AS org_name,
               m.role
        FROM users u
        JOIN memberships m ON m.user_id = u.id
        JOIN orgs o ON o.id = m.org_id
        WHERE u.email = 'newtest@example.com';
        """
    ], capture_output=True, text=True)
    print(r.stdout)
    return "newtest" in r.stdout and "Nigeria" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.1.3: Fix RegisterRequest schema")
    print("=" * 70)

    step1_fix_schema()
    ok_schema = step2_verify()
    if not ok_schema:
        print("❌ schema still broken")
        sys.exit(1)

    ok = step3_restart_and_test()

    print("=" * 70)
    if ok:
        print("✅ S.1.3 complete — register works end-to-end")
        sys.exit(0)
    print("❌ S.1.3 failed — see output above")
    sys.exit(1)


if __name__ == "__main__":
    main()
