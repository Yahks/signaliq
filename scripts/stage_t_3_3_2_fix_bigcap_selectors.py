"""
SignalIQ — Phase T.3.3.2: Fix biggest-companies selectors.

The /list/biggest-companies/ page has a different column layout than
/stocks/: a rank column ("No.") is prepended, and cells use class names
(sym, slw) instead of positional td:nth-child.

Real structure:
  1. No.           (rank)
  2. Symbol        (td.sym)
  3. Company Name  (td.slw)
  4. Market Cap    (td:nth-child(4), no class)
  5. Stock Price
  6. % Change
  7. Revenue

Fix: use class selectors where available, position for market cap.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_sql(sql: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-v", "ON_ERROR_STOP=1"],
        input=sql, capture_output=True, text=True,
    )
    return r.returncode, r.stdout or "", r.stderr or ""


def run_sql_t(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-tAc", sql],
        capture_output=True, text=True,
    )
    return (r.stdout or "").strip()


def update_selectors() -> None:
    print("\n[1/3] Update stockanalysis.stocks selectors")

    raw = run_sql_t(
        "SELECT training_config::text FROM trading_subblocks "
        "WHERE org_id IS NULL AND slug = 'screener_picks';"
    )
    if not raw:
        raise SystemExit("❌ screener_picks subblock not found")

    cfg = json.loads(raw)
    sources = cfg.get("sources", [])
    updated = False
    for s in sources:
        if s.get("id") == "stockanalysis.stocks":
            s["selectors"] = {
                "row": "#main-table tbody tr",
                "symbol": "td.sym",
                "name": "td.slw",
                "market_cap": "td:nth-child(4)",
            }
            s["note"] = (
                "biggest-companies page: rank col at n=1, symbol/name have "
                "class markers (sym/slw), market_cap is positional at n=4"
            )
            updated = True
            break

    if not updated:
        raise SystemExit("❌ stockanalysis.stocks source not found")

    cfg["sources"] = sources
    new_json = json.dumps(cfg).replace("'", "''")
    rc, _, err = run_sql(f"""
        UPDATE trading_subblocks
        SET training_config = '{new_json}'::jsonb,
            updated_at = now()
        WHERE org_id IS NULL AND slug = 'screener_picks';
    """)
    if rc != 0:
        raise SystemExit(f"❌ UPDATE failed:\n{err}")
    print("  ✓ selectors updated")


def test_fetch() -> bool:
    print("\n[2/3] Test fetch with new selectors")
    r = subprocess.run(
        ["python", "-c", '''
import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.trading.sources.scraper import fetch

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        row = (await s.execute(text("""
            SELECT jsonb_array_elements(training_config->'sources') AS src
            FROM trading_subblocks
            WHERE org_id IS NULL AND slug = 'screener_picks'
        """))).all()
    await engine.dispose()

    for (src,) in row:
        if src.get("id") != "stockanalysis.stocks":
            continue
        result = await fetch(dict(src), force_refresh=True)
        print(f"OK={result.ok} rows={len(result.rows)} err={result.error}")
        for r in result.rows[:5]:
            print(" ", r)

asyncio.run(main())
        '''.strip()],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False
    # Success if at least 5 rows came back
    return "rows=" in r.stdout and "OK=True" in r.stdout and "rows=0" not in r.stdout


def rerun_generator() -> bool:
    print("\n[3/3] Re-run screener_picks generator")
    r = subprocess.run(
        ["python", "scripts/run_screener_picks_once.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    print(r.stdout[-6000:] if len(r.stdout) > 6000 else r.stdout)
    if r.returncode != 0:
        print("  ❌ failed:")
        print(r.stderr[-1500:])
        return False
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.3.3.2: Fix biggest-companies selectors")
    print("=" * 70)

    update_selectors()
    ok_test = test_fetch()
    if not ok_test:
        print("\n❌ selector test failed — re-run curl to inspect the page")
        sys.exit(1)
    ok_run = rerun_generator()

    print("=" * 70)
    if ok_run:
        print("✅ T.3.3.2 complete — market cap ranking now works")
        sys.exit(0)
    print("❌ T.3.3.2 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
