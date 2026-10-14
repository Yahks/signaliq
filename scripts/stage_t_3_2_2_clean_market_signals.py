"""
SignalIQ — Phase T.3.2.2: Clean market_signals sources.

Drops Finviz from market_signals' active sources (keeps the config in
place with enabled=false for future re-use). Adds the sh_opt_common
filter attempt so if a future subblock turns it back on, it's ready.

Updates the generator to respect source.enabled.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKET_SIGNALS = ROOT / "packages" / "trading" / "subblocks" / "market_signals.py"
STOCK_LISTS = ROOT / "packages" / "trading" / "subblocks" / "stock_lists.py"


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


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
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


# ─────────────────────────────────────────────────────────────────────
# 1. Update market_signals.training_config.sources
# ─────────────────────────────────────────────────────────────────────

def step1_update_market_signals_config() -> None:
    print("\n[1/4] Update market_signals.training_config")

    raw = run_sql_t(
        "SELECT training_config::text FROM trading_subblocks "
        "WHERE org_id IS NULL AND slug = 'market_signals';"
    )
    if not raw:
        raise SystemExit("❌ market_signals subblock not found")

    cfg = json.loads(raw)
    sources = cfg.get("sources", [])
    updated = 0
    for s in sources:
        if s.get("id") == "finviz.screener_buy":
            # Disable Finviz for this subblock (kept for future use)
            s["enabled"] = False
            s["note"] = (
                "Returns preferred-share variants that pollute the ticker list. "
                "Kept in place for future subblocks that want the full universe. "
                "If re-enabled, consider adding sh_opt_common filter."
            )
            # Pre-apply the sh_opt_common filter attempt in case anyone re-enables it
            url = s.get("url", "")
            if "sh_opt_common" not in url:
                s["url"] = (
                    "https://finviz.com/screener.ashx?"
                    "v=111&f=an_recom_buybetter,sh_curvol_o1000,sh_opt_common"
                )
            updated += 1
        else:
            # Ensure other sources are explicitly enabled
            s.setdefault("enabled", True)

    if updated == 0:
        print("  ↷ Finviz already disabled for market_signals")
    else:
        print(f"  ✓ Finviz disabled + sh_opt_common URL added")

    cfg["sources"] = sources
    new_json = json.dumps(cfg).replace("'", "''")
    rc, _, err = run_sql(f"""
        UPDATE trading_subblocks
        SET training_config = '{new_json}'::jsonb,
            updated_at = now()
        WHERE org_id IS NULL AND slug = 'market_signals';
    """)
    if rc != 0:
        raise SystemExit(f"❌ UPDATE failed:\n{err}")
    print("  ✓ market_signals.training_config updated")


# ─────────────────────────────────────────────────────────────────────
# 2. market_signals generator: respect source.enabled
# ─────────────────────────────────────────────────────────────────────

def step2_market_signals_respects_enabled() -> None:
    print("\n[2/4] market_signals respects source.enabled")

    _patch_and_verify(
        MARKET_SIGNALS,
        '''    # --- 1. Fetch ---
    fetched: dict[str, list[dict[str, Any]]] = {}
    source_urls: list[str] = []
    for src in sources:
        sid = src.get("id")
        if not sid:
            continue
        result = await fetch(src, force_refresh=True)''',
        '''    # --- 1. Fetch (respecting enabled flag) ---
    fetched: dict[str, list[dict[str, Any]]] = {}
    source_urls: list[str] = []
    for src in sources:
        sid = src.get("id")
        if not sid:
            continue
        if not src.get("enabled", True):
            log.info("market_signals.source_disabled", source_id=sid)
            continue
        result = await fetch(src, force_refresh=True)''',
        "skip disabled sources",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. stock_lists generator: also respect enabled
# ─────────────────────────────────────────────────────────────────────

def step3_stock_lists_respects_enabled() -> None:
    print("\n[3/4] stock_lists respects source.enabled")

    _patch_and_verify(
        STOCK_LISTS,
        '''    # --- 1. Fetch each source ---
    fetched: dict[str, list[dict[str, Any]]] = {}
    source_urls: list[str] = []
    for src in sources:
        sid = src.get("id")
        if not sid:
            continue
        result = await fetch(src, force_refresh=True)''',
        '''    # --- 1. Fetch each source (respecting enabled flag) ---
    fetched: dict[str, list[dict[str, Any]]] = {}
    source_urls: list[str] = []
    for src in sources:
        sid = src.get("id")
        if not sid:
            continue
        if not src.get("enabled", True):
            log.info("stock_lists.source_disabled", source_id=sid)
            continue
        result = await fetch(src, force_refresh=True)''',
        "skip disabled sources",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Re-run market_signals to verify cleaner output
# ─────────────────────────────────────────────────────────────────────

def step4_rerun() -> bool:
    print("\n[4/4] Re-run market_signals")
    r = subprocess.run(
        ["python", "scripts/run_market_signals_once.py"],
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
    print("SignalIQ — Phase T.3.2.2: Clean market_signals sources")
    print("=" * 70)

    step1_update_market_signals_config()
    step2_market_signals_respects_enabled()
    step3_stock_lists_respects_enabled()
    ok = step4_rerun()

    print("=" * 70)
    if ok:
        print("✅ T.3.2.2 complete — market_signals uses StockAnalysis only")
        print("   Finviz config kept in place (enabled=false) for future use")
        sys.exit(0)
    print("❌ T.3.2.2 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
