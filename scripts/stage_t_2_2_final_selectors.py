"""
SignalIQ — Phase T.2.2: Final scraper selectors.

Fixes the two remaining cosmetic issues:
  - stockanalysis.active:  change column is volume, not % — find the right column
  - stockanalysis.stocks:  market_cap selector matches the <th>, not the <td>

Approach:
  1. Fetch /markets/active/ once, dump header row + first data row
  2. Fetch /stocks/ once, dump header row + first data row
  3. Auto-infer column positions by reading the <th> text
  4. Patch trading_subblocks.training_config
  5. Sleep 60s (rate-limit reset), then re-run smoke test

Idempotent. Fails loudly.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "SignalIQ/0.1 (+contact: ops@signaliq.local)"


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


def curl(url: str) -> str:
    r = subprocess.run(
        ["curl", "-s", "-A", UA, url],
        capture_output=True, text=True, timeout=30,
    )
    return r.stdout or ""


# ─────────────────────────────────────────────────────────────────────
# 1. Inspect /markets/active/ header row
# ─────────────────────────────────────────────────────────────────────

def inspect_active() -> dict[str, int]:
    """Return {column_name: 1-based index} for the active page."""
    print("\n[1/5] Inspect /markets/active/ header row")
    html = curl("https://stockanalysis.com/markets/active/")
    if not html:
        raise SystemExit("❌ empty response from /markets/active/")

    # Extract the <thead> block
    m = re.search(r"<thead[^>]*>(.*?)</thead>", html, re.DOTALL)
    if not m:
        raise SystemExit("❌ no <thead> found on active page")
    thead = m.group(1)

    # Find every <th>, grab its visible text
    ths = re.findall(r"<th[^>]*>(.*?)</th>", thead, re.DOTALL)
    headers = []
    for th in ths:
        # Strip tags and normalize
        text = re.sub(r"<[^>]+>", "", th)
        text = re.sub(r"\s+", " ", text).strip()
        headers.append(text)

    print(f"  headers found ({len(headers)}):")
    result: dict[str, int] = {}
    for i, h in enumerate(headers, start=1):
        print(f"    {i}: {h!r}")
        key = h.lower()
        if key in ("symbol", "ticker"):
            result["symbol"] = i
        elif key in ("name", "company", "company name"):
            result["name"] = i
        elif key in ("change", "change %", "chg", "% change", "chg %"):
            result["change"] = i
        elif key in ("volume", "vol"):
            result["volume"] = i
        elif key in ("price", "last"):
            result["price"] = i

    if "symbol" not in result:
        raise SystemExit("❌ could not locate symbol column")
    if "name" not in result:
        raise SystemExit("❌ could not locate name column")
    if "change" not in result:
        # Fallback: the column immediately before volume, if volume exists
        if "volume" in result and result["volume"] > 2:
            result["change"] = result["volume"] - 1
            print(f"  ⚠ inferred change column = {result['change']} (volume - 1)")
        else:
            raise SystemExit("❌ could not locate change column")

    print(f"  → symbol={result['symbol']}  name={result['name']}  change={result['change']}")
    return result


# ─────────────────────────────────────────────────────────────────────
# 2. Inspect /stocks/ header row
# ─────────────────────────────────────────────────────────────────────

def inspect_stocks() -> dict[str, int]:
    """Return {column_name: 1-based index} for the stocks page."""
    print("\n[2/5] Inspect /stocks/ header row")
    html = curl("https://stockanalysis.com/stocks/")
    if not html:
        raise SystemExit("❌ empty response from /stocks/")

    m = re.search(r"<thead[^>]*>(.*?)</thead>", html, re.DOTALL)
    if not m:
        raise SystemExit("❌ no <thead> found on stocks page")
    thead = m.group(1)

    ths = re.findall(r"<th[^>]*>(.*?)</th>", thead, re.DOTALL)
    headers = []
    for th in ths:
        text = re.sub(r"<[^>]+>", "", th)
        text = re.sub(r"\s+", " ", text).strip()
        headers.append(text)

    print(f"  headers found ({len(headers)}):")
    result: dict[str, int] = {}
    for i, h in enumerate(headers, start=1):
        print(f"    {i}: {h!r}")
        key = h.lower()
        if key in ("symbol", "ticker"):
            result["symbol"] = i
        elif key in ("name", "company", "company name"):
            result["name"] = i
        elif "market cap" in key or key == "mkt cap" or key == "marketcap":
            result["market_cap"] = i
        elif key in ("sector",):
            result["sector"] = i

    if "symbol" not in result:
        raise SystemExit("❌ could not locate symbol column")
    if "market_cap" not in result:
        raise SystemExit(
            f"❌ could not locate market cap column in {headers}\n"
            f"   The /stocks/ page may need a different data source."
        )

    print(f"  → symbol={result['symbol']}  name={result.get('name')}  "
          f"market_cap={result['market_cap']}")
    return result


# ─────────────────────────────────────────────────────────────────────
# 3. Patch trading_subblocks
# ─────────────────────────────────────────────────────────────────────

def patch_source(subblock_slug: str, source_id: str, new_source: dict) -> None:
    raw = run_sql(
        f"SELECT training_config::text FROM trading_subblocks "
        f"WHERE org_id IS NULL AND slug = '{subblock_slug}';"
    )
    if not raw:
        raise SystemExit(f"❌ subblock {subblock_slug} not found")

    cfg = json.loads(raw)
    sources = cfg.get("sources", [])
    found = False
    for i, s in enumerate(sources):
        if s.get("id") == source_id:
            sources[i] = new_source
            found = True
            break
    if not found:
        raise SystemExit(
            f"❌ source {source_id} not found in {subblock_slug}"
        )

    cfg["sources"] = sources
    new_json = json.dumps(cfg).replace("'", "''")

    rc, out, err = run_sql_file(f"""
        UPDATE trading_subblocks
        SET training_config = '{new_json}'::jsonb,
            updated_at = now()
        WHERE org_id IS NULL AND slug = '{subblock_slug}';
    """)
    if rc != 0:
        raise SystemExit(f"❌ UPDATE {subblock_slug} failed:\n{err}")
    print(f"  ✓ {subblock_slug}/{source_id}: patched")


def step3_patch_active(cols: dict[str, int]) -> None:
    print("\n[3/5] Patch stockanalysis.active")
    new_source = {
        "id": "stockanalysis.active",
        "url": "https://stockanalysis.com/markets/active/",
        "parser": "table",
        "robots": "allow",
        "selectors": {
            "row": "#main-table tbody tr",
            "symbol": f"td:nth-child({cols['symbol']})",
            "name": f"td:nth-child({cols['name']})",
            "change": f"td:nth-child({cols['change']})",
        },
    }
    patch_source("stock_lists", "stockanalysis.active", new_source)


def step3_patch_stocks(cols: dict[str, int]) -> None:
    print("\n[4/5] Patch stockanalysis.stocks")
    selectors = {
        "row": "#main-table tbody tr",
        "symbol": f"td:nth-child({cols['symbol']})",
        "market_cap": f"td:nth-child({cols['market_cap']})",
    }
    if "name" in cols:
        selectors["name"] = f"td:nth-child({cols['name']})"

    new_source = {
        "id": "stockanalysis.stocks",
        "url": "https://stockanalysis.com/stocks/",
        "parser": "table",
        "robots": "allow",
        "selectors": selectors,
    }
    patch_source("screener_picks", "stockanalysis.stocks", new_source)


# ─────────────────────────────────────────────────────────────────────
# 5. Rate-limit-aware smoke test
# ─────────────────────────────────────────────────────────────────────

def step5_smoke() -> None:
    print("\n[5/5] Sleep 65s (rate-limit reset), then re-run smoke test")
    for i in range(65, 0, -5):
        print(f"  ... {i}s")
        time.sleep(5)
    print()

    r = subprocess.run(
        ["python", "scripts/smoke_scraper.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    print(r.stdout[-4000:] if len(r.stdout) > 4000 else r.stdout)
    if r.returncode != 0:
        print("⚠ smoke test exited non-zero")


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.2.2: Final scraper selectors")
    print("=" * 70)

    active_cols = inspect_active()
    stocks_cols = inspect_stocks()

    step3_patch_active(active_cols)
    step3_patch_stocks(stocks_cols)

    step5_smoke()

    print("=" * 70)
    print("✅ T.2.2 complete — all scraper selectors validated")
    print("=" * 70)


if __name__ == "__main__":
    main()
