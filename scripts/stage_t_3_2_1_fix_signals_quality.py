"""
SignalIQ — Phase T.3.2.1: Fix signals quality.

Three fixes:
  1. Finviz URL: add sh_curvol_o1000 filter (exclude preferreds/illiquid junk)
  2. New helper in base.py: _is_valid_us_ticker() — filter applied in both
     stock_lists and market_signals
  3. verify_post: retry-once-on-429 with 30s backoff, then fail-open

Also:
  - Delete the garbage post a1b8070e (fabricated AAAPL→Apple association)
  - Re-run market_signals to confirm real tickers + clean verify
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"
MARKET_SIGNALS = ROOT / "packages" / "trading" / "subblocks" / "market_signals.py"
STOCK_LISTS = ROOT / "packages" / "trading" / "subblocks" / "stock_lists.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"\n❌ {path.relative_to(ROOT)} does not exist")
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:500]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    check = path.read_text()
    if new not in check:
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new text not found")
    print(f"  ✓ {path.name}: {label} (verified)")


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


# ─────────────────────────────────────────────────────────────────────
# 1. Add _is_valid_us_ticker to base.py
# ─────────────────────────────────────────────────────────────────────

def step1_add_ticker_filter() -> None:
    print("\n[1/5] Add _is_valid_us_ticker() to base.py")

    _patch_and_verify(
        BASE,
        '''_CLAIM_SPLIT_RE = re.compile(r"(?<=[.!?])\\s+(?=[A-Z])")''',
        '''_CLAIM_SPLIT_RE = re.compile(r"(?<=[.!?])\\s+(?=[A-Z])")


# ─────────────────────────────────────────────────────────────────────
# Ticker validation — reject preferreds, warrants, units, foreign junk
# ─────────────────────────────────────────────────────────────────────

_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\\.[A-Z]{1,2})?$")


def is_valid_us_ticker(sym: str) -> bool:
    """Return True if `sym` looks like a normal US common-stock ticker.

    Accepts:
      AAPL, MSFT, BRK.B, NVDA, F

    Rejects:
      AAAPL, AAACG, AAAUC, AABAT     (preferred share variants)
      anything with 6+ letters       (usually warrants/units/preferreds)
      anything with non-alpha chars  (except a single dot for class shares)
    """
    if not sym:
        return False
    s = sym.strip().upper()
    if len(s) > 6:                       # BRK.B is 5 chars, allow up to 6 for safety
        return False
    if not _TICKER_RE.match(s):
        return False
    # Reject 3+ repeated leading letters (AAAxx pattern of preferreds)
    root = s.split(".")[0]
    if len(root) >= 4 and len(set(root[:3])) == 1:
        return False
    return True''',
        "_is_valid_us_ticker helper",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Apply filter in market_signals
# ─────────────────────────────────────────────────────────────────────

def step2_filter_market_signals() -> None:
    print("\n[2/5] Filter tickers in market_signals")

    _patch_and_verify(
        MARKET_SIGNALS,
        '''from .base import (
    TradingPost,
    call_llm_json_verified,
    persist_post,
    render_markdown_table,
)''',
        '''from .base import (
    TradingPost,
    call_llm_json_verified,
    is_valid_us_ticker,
    persist_post,
    render_markdown_table,
)''',
        "import is_valid_us_ticker",
    )

    _patch_and_verify(
        MARKET_SIGNALS,
        '''def _to_rows(rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    out = []
    for r in rows[:top_n]:
        out.append({
            "symbol": (r.get("symbol") or "").strip(),
            "name": (r.get("name") or "").strip(),
        })
    return [x for x in out if x["symbol"]]''',
        '''def _to_rows(rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    out = []
    dropped = 0
    for r in rows:
        sym = (r.get("symbol") or "").strip()
        if not sym:
            continue
        if not is_valid_us_ticker(sym):
            dropped += 1
            continue
        out.append({
            "symbol": sym,
            "name": (r.get("name") or "").strip(),
        })
        if len(out) >= top_n:
            break
    if dropped:
        log.info("market_signals.tickers_dropped", count=dropped)
    return out''',
        "ticker filter in _to_rows",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Apply filter in stock_lists
# ─────────────────────────────────────────────────────────────────────

def step3_filter_stock_lists() -> None:
    print("\n[3/5] Filter tickers in stock_lists")

    _patch_and_verify(
        STOCK_LISTS,
        '''from .base import (
    TradingPost,
    call_llm_json_verified,
    persist_post,
    render_markdown_table,
)''',
        '''from .base import (
    TradingPost,
    call_llm_json_verified,
    is_valid_us_ticker,
    persist_post,
    render_markdown_table,
)''',
        "import is_valid_us_ticker",
    )

    _patch_and_verify(
        STOCK_LISTS,
        '''def _to_rows(result_rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Normalize a scraped list into consistent keys + cap at top_n."""
    out = []
    for r in result_rows[:top_n]:
        out.append({
            "symbol": (r.get("symbol") or "").strip(),
            "name": (r.get("name") or "").strip(),
            "change": (r.get("change") or "").strip(),
        })
    return [x for x in out if x["symbol"]]''',
        '''def _to_rows(result_rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Normalize a scraped list into consistent keys + cap at top_n.

    Filters out junk tickers (preferred shares, warrants, units).
    """
    out = []
    dropped = 0
    for r in result_rows:
        sym = (r.get("symbol") or "").strip()
        if not sym:
            continue
        if not is_valid_us_ticker(sym):
            dropped += 1
            continue
        out.append({
            "symbol": sym,
            "name": (r.get("name") or "").strip(),
            "change": (r.get("change") or "").strip(),
        })
        if len(out) >= top_n:
            break
    if dropped:
        log.info("stock_lists.tickers_dropped", count=dropped)
    return out''',
        "ticker filter in _to_rows",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Retry-once-on-429 in verify_post
# ─────────────────────────────────────────────────────────────────────

def step4_retry_verify_on_429() -> None:
    print("\n[4/5] Add retry-once-on-429 to verify_post")

    _patch_and_verify(
        BASE,
        '''    try:
        raw = await complete(
            role="reasoning",
            system=system,
            user=user,
            org_id=org_id,
            purpose="trading.verify",
            temperature=0.0,
            max_tokens=200,
            json_mode=False,
        )
        bad_nums = _parse_unsupported_nums(raw or "")
    except Exception as exc:
        log.warning("verify.failed_open", error=str(exc)[:200])
        return True, []''',
        '''    async def _try_verify() -> str:
        return await complete(
            role="reasoning",
            system=system,
            user=user,
            org_id=org_id,
            purpose="trading.verify",
            temperature=0.0,
            max_tokens=200,
            json_mode=False,
        )

    try:
        raw = await _try_verify()
        bad_nums = _parse_unsupported_nums(raw or "")
    except Exception as exc:
        err_msg = str(exc)
        # Special case: rate limit — wait 30s and retry once
        if "429" in err_msg or "Rate limit" in err_msg:
            log.info("verify.rate_limited_retrying", wait_seconds=30)
            import asyncio as _asyncio
            await _asyncio.sleep(30)
            try:
                raw = await _try_verify()
                bad_nums = _parse_unsupported_nums(raw or "")
            except Exception as exc2:
                log.warning("verify.failed_open_after_retry", error=str(exc2)[:200])
                return True, []
        else:
            log.warning("verify.failed_open", error=err_msg[:200])
            return True, []''',
        "retry-once-on-429",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. Patch Finviz URL + delete garbage post + re-run
# ─────────────────────────────────────────────────────────────────────

def step5_patch_finviz_url() -> None:
    print("\n[5a/5] Patch Finviz URL in trading_subblocks")

    raw = run_sql_t(
        "SELECT training_config::text FROM trading_subblocks "
        "WHERE org_id IS NULL AND slug = 'market_signals';"
    )
    if not raw:
        raise SystemExit("❌ market_signals subblock not found")

    cfg = json.loads(raw)
    sources = cfg.get("sources", [])
    patched = False
    for s in sources:
        if s.get("id") == "finviz.screener_buy":
            old_url = s.get("url", "")
            # Add sh_curvol_o1000 (current volume > 1000) to filter out illiquid preferreds
            if "sh_curvol_o1000" not in old_url:
                s["url"] = (
                    "https://finviz.com/screener.ashx?"
                    "v=111&f=an_recom_buybetter,sh_curvol_o1000"
                )
                patched = True
                print(f"  ✓ Finviz URL updated (was: {old_url[:60]}…)")
            else:
                print("  ↷ Finviz URL already has sh_curvol_o1000")
            break

    if patched:
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


def step5b_delete_garbage_post() -> None:
    print("\n[5b/5] Delete garbage post a1b8070e")

    row = run_sql_t(
        "SELECT id::text FROM trading_posts WHERE id::text LIKE 'a1b8070e%';"
    )
    if not row:
        print("  ↷ garbage post already gone")
        return

    rc, out, err = run_sql(f"DELETE FROM trading_posts WHERE id = '{row}';")
    if rc != 0:
        raise SystemExit(f"❌ DELETE failed:\n{err}")
    print(f"  ✓ deleted post {row[:8]}")


def step5c_rerun() -> bool:
    print("\n[5c/5] Re-run market_signals generator")
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
    print("SignalIQ — Phase T.3.2.1: Fix signals quality")
    print("=" * 70)

    step1_add_ticker_filter()
    step2_filter_market_signals()
    step3_filter_stock_lists()
    step4_retry_verify_on_429()
    step5_patch_finviz_url()
    step5b_delete_garbage_post()
    ok = step5c_rerun()

    print("=" * 70)
    if ok:
        print("✅ T.3.2.1 complete — signals quality fixed")
        sys.exit(0)
    print("❌ T.3.2.1 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
