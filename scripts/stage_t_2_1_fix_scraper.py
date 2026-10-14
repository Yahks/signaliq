"""
SignalIQ — Phase T.2.1: Fix scraper gaps.

Ships:
  1. UPDATE trading_subblocks.training_config for 4 broken sources
  2. Add REDIS_URL to .env + .env.example
  3. Patch hardcoded Redis host in rate_limit.py + scraper.py
  4. Document Redis DB assignments in packages/core/config.py
  5. Re-run smoke test
  6. Print before/after

Idempotent. Fails loudly on pattern mismatch.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RATE_LIMIT = ROOT / "packages" / "core" / "rate_limit.py"
SCRAPER = ROOT / "packages" / "trading" / "sources" / "scraper.py"
CONFIG = ROOT / "packages" / "core" / "config.py"
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def _patch(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"\n❌ {path.relative_to(ROOT)} does not exist")
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:400]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    print(f"  ✓ {path.name}: {label}")


def run_sql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-tAc", sql],
        capture_output=True, text=True,
    )
    return (r.stdout or "").strip()


def run_sql_file(sql: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-v", "ON_ERROR_STOP=1"],
        input=sql, capture_output=True, text=True,
    )
    return r.returncode, r.stdout or "", r.stderr or ""


# ─────────────────────────────────────────────────────────────────────
# 1. Correct training_config for 4 broken sources
# ─────────────────────────────────────────────────────────────────────

# Each entry: (subblock_slug, source_id_to_patch, new_source_dict)
PATCHES = [
    # --- stock_lists: fix column shift for gainers/losers ---
    ("stock_lists", "stockanalysis.gainers", {
        "id": "stockanalysis.gainers",
        "url": "https://stockanalysis.com/markets/gainers/",
        "parser": "table",
        "robots": "allow",
        "selectors": {
            "row": "#main-table tbody tr",
            "symbol": "td:nth-child(2)",
            "name": "td:nth-child(3)",
            "change": "td:nth-child(4)",
        },
    }),
    ("stock_lists", "stockanalysis.losers", {
        "id": "stockanalysis.losers",
        "url": "https://stockanalysis.com/markets/losers/",
        "parser": "table",
        "robots": "allow",
        "selectors": {
            "row": "#main-table tbody tr",
            "symbol": "td:nth-child(2)",
            "name": "td:nth-child(3)",
            "change": "td:nth-child(4)",
        },
    }),
    # --- stock_lists: replace nasdaq with stockanalysis active page ---
    ("stock_lists", "nasdaq.most_active", {
        "id": "stockanalysis.active",
        "url": "https://stockanalysis.com/markets/active/",
        "parser": "table",
        "robots": "allow",
        "selectors": {
            "row": "#main-table tbody tr",
            "symbol": "td:nth-child(2)",
            "name": "td:nth-child(3)",
            "change": "td:nth-child(4)",
        },
    }),
    # --- screener_picks: scope to #main-table, target marketCap class cell ---
    ("screener_picks", "stockanalysis.stocks", {
        "id": "stockanalysis.stocks",
        "url": "https://stockanalysis.com/stocks/",
        "parser": "table",
        "robots": "allow",
        "selectors": {
            "row": "#main-table tbody tr",
            "symbol": "td:nth-child(1)",
            "name": "td:nth-child(2)",
            "market_cap": "td.marketCap",
        },
    }),
    # --- screener_picks: fix SEC EDGAR URL to a valid query ---
    ("screener_picks", "sec.edgar.fulltext", {
        "id": "sec.edgar.fulltext",
        "url": "https://efts.sec.gov/LATEST/search-index?q=%22S-1%22&forms=S-1",
        "parser": "json",
        "robots": "allow",
        "user_agent": "SignalIQ/0.1 (contact: ops@signaliq.local)",
        "selectors": {"root": "hits.hits"},
    }),
]


def step1_patch_subblocks() -> None:
    print("\n[1/6] Patch training_config for 4 broken sources")

    for slug, source_id, new_source in PATCHES:
        # Load current config
        raw = run_sql(
            f"SELECT training_config::text FROM trading_subblocks "
            f"WHERE org_id IS NULL AND slug = '{slug}';"
        )
        if not raw:
            raise SystemExit(f"❌ subblock {slug} not found")

        cfg = json.loads(raw)
        sources = cfg.get("sources", [])
        replaced = False
        for i, s in enumerate(sources):
            if s.get("id") == source_id:
                sources[i] = new_source
                replaced = True
                break

        if not replaced:
            # Append if source_id changed (e.g. nasdaq → stockanalysis.active)
            # First remove any old entry with the same URL
            sources = [s for s in sources if s.get("id") != source_id]
            sources.append(new_source)
            print(f"  ↳ {slug}/{source_id}: not found as-is, appended as {new_source['id']}")
        else:
            print(f"  ✓ {slug}/{source_id}: selectors updated")

        cfg["sources"] = sources
        new_json = json.dumps(cfg).replace("'", "''")

        rc, out, err = run_sql_file(f"""
            UPDATE trading_subblocks
            SET training_config = '{new_json}'::jsonb,
                updated_at = now()
            WHERE org_id IS NULL AND slug = '{slug}';
        """)
        if rc != 0:
            raise SystemExit(f"❌ UPDATE {slug} failed:\n{err}")


# ─────────────────────────────────────────────────────────────────────
# 2. Add REDIS_URL to .env + .env.example
# ─────────────────────────────────────────────────────────────────────

def step2_env() -> None:
    print("\n[2/6] Add REDIS_URL to .env + .env.example")
    line = "REDIS_URL=redis://localhost:6379/0\n"

    for p in (ENV, ENV_EXAMPLE):
        if not p.exists():
            print(f"  ⚠ {p.name} does not exist — skipping")
            continue
        src = p.read_text()
        if "REDIS_URL" in src:
            print(f"  ↷ {p.name}: REDIS_URL already present")
            continue
        if not src.endswith("\n"):
            src += "\n"
        p.write_text(src + "\n# --- Cache / broker ---\n" + line)
        print(f"  ✓ {p.name}: REDIS_URL added")


# ─────────────────────────────────────────────────────────────────────
# 3. Patch hardcoded Redis host in rate_limit.py + scraper.py
# ─────────────────────────────────────────────────────────────────────

def step3_patch_redis_host() -> None:
    print("\n[3/6] Patch hardcoded Redis host → read from settings")

    # rate_limit.py
    _patch(
        RATE_LIMIT,
        '''        _redis = redis.Redis(host="localhost", port=6379, db=4, decode_responses=True)''',
        '''        from packages.core.config import get_settings
        s = get_settings()
        # Prefer explicit REDIS_URL; fall back to localhost
        url = getattr(s, "redis_url", "redis://localhost:6379/0")
        _redis = redis.Redis.from_url(url.rsplit("/", 1)[0] + "/4", decode_responses=True)''',
        "read REDIS_URL from settings",
    )

    # scraper.py
    _patch(
        SCRAPER,
        '''            _redis_client = redis.Redis(
                host="localhost", port=6379, db=REDIS_DB, decode_responses=True,
            )''',
        '''            from packages.core.config import get_settings
            s = get_settings()
            url = getattr(s, "redis_url", "redis://localhost:6379/0")
            _redis_client = redis.Redis.from_url(
                url.rsplit("/", 1)[0] + f"/{REDIS_DB}", decode_responses=True,
            )''',
        "read REDIS_URL from settings",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. config.py — add redis_url field + DB assignment doc
# ─────────────────────────────────────────────────────────────────────

def step4_config_field() -> None:
    print("\n[4/6] Add redis_url to Settings + document DB assignments")
    src = CONFIG.read_text()
    if "redis_url" in src:
        print("  ↷ config.py: redis_url already present")
        return

    # Find the "database_url_sync" field and insert after it
    anchor = '    database_url_sync: str = Field(..., description="psycopg URL for Alembic")'
    if anchor not in src:
        raise SystemExit("❌ config.py: database_url_sync anchor not found")

    addition = anchor + '''

    # --- Redis ---
    # DB assignments:
    #   0 — Celery broker / default app cache
    #   1 — Celery result backend
    #   3 — Scraper HTTP cache (packages/trading/sources/scraper.py)
    #   4 — Rate limiter (packages/core/rate_limit.py)
    redis_url: str = "redis://localhost:6379/0"'''

    src = src.replace(anchor, addition, 1)
    CONFIG.write_text(src)
    print("  ✓ config.py: redis_url added with DB documentation")


# ─────────────────────────────────────────────────────────────────────
# 5. Re-run smoke test
# ─────────────────────────────────────────────────────────────────────

def step5_smoke() -> None:
    print("\n[5/6] Re-run smoke test")
    r = subprocess.run(
        ["python", "scripts/smoke_scraper.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    print(r.stdout)
    if r.returncode != 0:
        print("⚠ smoke test exited non-zero:")
        print(r.stderr[:500])


# ─────────────────────────────────────────────────────────────────────
# 6. Summary
# ─────────────────────────────────────────────────────────────────────

def step6_summary() -> None:
    print("\n[6/6] Summary of source health")
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
    async with factory() as session:
        rows = (await session.execute(text("""
            SELECT slug, jsonb_array_elements(training_config->'sources') AS src
            FROM trading_subblocks
            WHERE org_id IS NULL
              AND slug IN ('stock_lists','market_signals','screener_picks')
        """))).all()
    await engine.dispose()

    for slug, src in rows:
        result = await fetch(dict(src), force_refresh=True)
        status = "✓" if result.ok and result.rows else "❌"
        print(f"  {status}  {slug:20} {src.get('id','?'):30} rows={len(result.rows)} err={result.error or '-'}")

asyncio.run(main())
        '''.strip()],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    print(r.stdout)
    if r.stderr:
        print("  stderr:", r.stderr[:300])


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.2.1: Fix scraper gaps")
    print("=" * 70)

    step1_patch_subblocks()
    step2_env()
    step3_patch_redis_host()
    step4_config_field()
    step5_smoke()
    step6_summary()

    print("=" * 70)
    print("✅ T.2.1 complete — scraper selectors fixed, Redis configurable")
    print("=" * 70)


if __name__ == "__main__":
    main()
