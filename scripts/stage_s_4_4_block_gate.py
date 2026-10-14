"""
SignalIQ — Phase S.4.4: Manual generate endpoint refuses when block is disabled.

The org_block_settings table already gates beat-scheduled generation.
This adds the same gate to the on-demand POST /api/trading/subblocks/{slug}/generate
endpoint, so a user with Trading disabled can't manually trigger generation.

Ships:
  1. Patch /api/trading/subblocks/{slug}/generate with a block-disabled check
  2. Same check applied to any other manual generate endpoints (none yet)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRADING_API = ROOT / "apps" / "api" / "routers" / "trading.py"


def _patch(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"❌ {path.name} not found")
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(f"\n❌ {path.name}: pattern not found for '{label}'")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


def step1_add_block_gate() -> None:
    print("\n[1/3] Patch manual /generate endpoint with block-disabled check")

    _patch(
        TRADING_API,
        '''    # Exhaustion gate — return 402 if the user is out of tokens.''',
        '''    # Block gate — refuse if the 'trading' block is disabled for this org.
    # The user must explicitly enable the block to generate content.
    from packages.db.session import get_owner_session_factory as _osf
    _block_factory = _osf()
    async with _block_factory() as _bs:
        _block_row = (await _bs.execute(sql_text("""
            SELECT auto_enabled FROM org_block_settings
            WHERE org_id = :oid AND block_slug = 'trading'
        """), {"oid": org_id})).first()
        # Missing row → treat as enabled (fail-open for orgs created before S.4)
        if _block_row is not None and not _block_row[0]:
            raise HTTPException(
                status_code=403,
                detail="Trading block is disabled for your organisation. "
                       "Enable it on the dashboard to generate content.",
            )

    # Exhaustion gate — return 402 if the user is out of tokens.''',
        "block-disabled gate",
    )


def step2_restart_and_verify() -> bool:
    print("\n[2/3] Restart API + smoke test")

    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    with Path("/tmp/signaliq-api.log").open("w") as f:
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

    # Test: disable trading block, then try manual generate → expect 403
    r = subprocess.run([
        "bash", "-c",
        "TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login "
        "-H 'Content-Type: application/json' "
        "-d '{\"identifier\":\"demo\",\"password\":\"DemoPass123!\"}' "
        "| python -c \"import sys,json; print(json.load(sys.stdin)['access_token'])\") && "
        "echo '--- disable trading block ---' && "
        "curl -s -X PATCH http://localhost:8000/api/settings/blocks/trading "
        "-H \"Authorization: Bearer $TOKEN\" "
        "-H 'Content-Type: application/json' "
        "-d '{\"auto_enabled\": false}' > /dev/null && "
        "echo '--- try manual generate → expect 403 ---' && "
        "curl -s -X POST http://localhost:8000/api/trading/subblocks/trading_psychology/generate "
        "-H \"Authorization: Bearer $TOKEN\" -w '\\nHTTP %{http_code}\\n' && "
        "echo '' && "
        "echo '--- re-enable trading block ---' && "
        "curl -s -X PATCH http://localhost:8000/api/settings/blocks/trading "
        "-H \"Authorization: Bearer $TOKEN\" "
        "-H 'Content-Type: application/json' "
        "-d '{\"auto_enabled\": true}' > /dev/null && "
        "echo '--- try manual generate → expect 202 ---' && "
        "curl -s -X POST http://localhost:8000/api/trading/subblocks/trading_psychology/generate "
        "-H \"Authorization: Bearer $TOKEN\" -w '\\nHTTP %{http_code}\\n'"
    ], capture_output=True, text=True, timeout=30)
    print(r.stdout)
    return "HTTP 403" in r.stdout and "HTTP 202" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.4.4: Manual generate refuses when block off")
    print("=" * 70)

    step1_add_block_gate()
    ok = step2_restart_and_verify()

    print("=" * 70)
    if ok:
        print("✅ S.4.4 complete — block toggle now gates manual generation")
        sys.exit(0)
    print("❌ S.4.4 failed — see output")
    sys.exit(1)


if __name__ == "__main__":
    main()
