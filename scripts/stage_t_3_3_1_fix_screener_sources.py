"""
SignalIQ — Phase T.3.3.1: Fix screener_picks sources.

Two problems in T.3.3 output:

  1. Top-20 by market cap is wrong — the /stocks/ page returns rows
     alphabetically, so we only see the largest A-prefix symbols.
     Fix: switch to a market-cap-ranked page.

  2. EDGAR "recent S-1 filings" returned filings from 2007-2024.
     Fix: add date-range params with a template placeholder the scraper
     substitutes at fetch time.

Also:
  - scraper.py: support {{today}} / {{today_minus_N}} URL templates
  - Delete the misleading post 4e07edf0 OR mark it draft with a note
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRAPER = ROOT / "packages" / "trading" / "sources" / "scraper.py"


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
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:400]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. Probe candidate market-cap-ranking pages
# ─────────────────────────────────────────────────────────────────────

def probe_ranked_pages() -> str | None:
    """Return the first ranked-by-cap URL that returns 200."""
    print("\n[1/5] Probe market-cap ranking pages")

    candidates = [
        "https://stockanalysis.com/list/biggest-companies/",
        "https://stockanalysis.com/list/market-cap/",
        "https://stockanalysis.com/stocks/ranked-by-market-cap/",
    ]
    for url in candidates:
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-A", "SignalIQ/0.1 (+contact: ops@signaliq.local)", url],
            capture_output=True, text=True, timeout=20,
        )
        code = (r.stdout or "").strip()
        print(f"  {code}  {url}")
        if code == "200":
            return url
    print("  ⚠ no ranked page returned 200")
    return None


# ─────────────────────────────────────────────────────────────────────
# 2. Add URL template substitution to scraper.py
# ─────────────────────────────────────────────────────────────────────

def step2_add_url_templates() -> None:
    print("\n[2/5] Add URL template substitution to scraper.py")

    _patch_and_verify(
        SCRAPER,
        '''# ─────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────''',
        '''# ─────────────────────────────────────────────────────────────────────
# URL template substitution
# ─────────────────────────────────────────────────────────────────────

def _substitute_url_templates(url: str) -> str:
    """Replace {{today}} and {{today_minus_N}} placeholders in a URL.

    Examples:
      "https://x/?startdt={{today_minus_7}}&enddt={{today}}"
        → "https://x/?startdt=2026-09-12&enddt=2026-09-19"
    """
    from datetime import UTC, datetime, timedelta
    import re as _re

    today = datetime.now(UTC).date()

    def _repl(m: "_re.Match[str]") -> str:
        token = m.group(1).strip()
        if token == "today":
            return today.isoformat()
        if token.startswith("today_minus_"):
            try:
                n = int(token[len("today_minus_"):])
                return (today - timedelta(days=n)).isoformat()
            except ValueError:
                return m.group(0)
        return m.group(0)

    return _re.sub(r"\\{\\{\\s*([^}]+?)\\s*\\}\\}", _repl, url)


# ─────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────''',
        "URL template substitution helper",
    )

    # Hook it into fetch() — after cache key computation, before robots check
    _patch_and_verify(
        SCRAPER,
        '''    if not url:
        return ScrapeResult(ok=False, source_id=source_id, url="", parser=parser_name,
                            error="missing url")

    # 1. Cache''',
        '''    if not url:
        return ScrapeResult(ok=False, source_id=source_id, url="", parser=parser_name,
                            error="missing url")

    # 0. Expand {{today}} / {{today_minus_N}} templates
    url = _substitute_url_templates(url)

    # 1. Cache''',
        "hook template expansion into fetch()",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Update screener_picks.training_config.sources
# ─────────────────────────────────────────────────────────────────────

def step3_update_config(ranked_url: str | None) -> None:
    print("\n[3/5] Update screener_picks.training_config")

    raw = run_sql_t(
        "SELECT training_config::text FROM trading_subblocks "
        "WHERE org_id IS NULL AND slug = 'screener_picks';"
    )
    if not raw:
        raise SystemExit("❌ screener_picks subblock not found")

    cfg = json.loads(raw)
    sources = cfg.get("sources", [])
    updated = 0

    for s in sources:
        sid = s.get("id")
        if sid == "stockanalysis.stocks":
            # Prefer the ranked-by-cap page; fall back to /stocks/ with an
            # explicit note that this is alphabetically sorted, not by cap.
            if ranked_url:
                s["url"] = ranked_url
                s["note"] = "market-cap-ranked page (sorted by cap descending)"
                updated += 1
            else:
                s["note"] = (
                    "⚠ /stocks/ returns rows alphabetically, NOT by market cap. "
                    "Top-20 by cap is impossible from this source. "
                    "Generator must not claim 'top by cap'."
                )
                updated += 1
        elif sid == "sec.edgar.fulltext":
            # Add date range via template placeholders
            s["url"] = (
                "https://efts.sec.gov/LATEST/search-index"
                "?q=%22S-1%22&forms=S-1"
                "&dateRange=custom"
                "&startdt={{today_minus_7}}"
                "&enddt={{today}}"
            )
            s["note"] = "S-1 filings from last 7 days (date range templated)"
            updated += 1

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
    print(f"  ✓ updated {updated} source(s) in screener_picks")


# ─────────────────────────────────────────────────────────────────────
# 4. Mark the misleading post 4e07edf0 as draft
# ─────────────────────────────────────────────────────────────────────

def step4_mark_post_draft() -> None:
    print("\n[4/5] Mark misleading post 4e07edf0 as draft")

    row = run_sql_t(
        "SELECT id::text FROM trading_posts "
        "WHERE id::text LIKE '4e07edf0%';"
    )
    if not row:
        print("  ↷ post already gone or already marked")
        return

    note = "T.3.3 output: 'Top 20 by market cap' was actually the alphabetically-first 20 A-prefix symbols from /stocks/. Not true top-20. Superseded by T.3.3.1."
    note_esc = note.replace("'", "''")

    rc, _, err = run_sql(f"""
        UPDATE trading_posts
        SET status = 'draft',
            published_at = NULL,
            source_payload = jsonb_set(
                source_payload,
                '{{supersede_note}}',
                to_jsonb('{note_esc}'::text)
            )
        WHERE id = '{row}';
    """)
    if rc != 0:
        raise SystemExit(f"❌ UPDATE failed:\n{err}")
    print(f"  ✓ post {row[:8]} marked draft + supersede_note added")


# ─────────────────────────────────────────────────────────────────────
# 5. Re-run screener_picks
# ─────────────────────────────────────────────────────────────────────

def step5_rerun() -> bool:
    print("\n[5/5] Re-run screener_picks")
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
    print("SignalIQ — Phase T.3.3.1: Fix screener_picks sources")
    print("=" * 70)

    ranked_url = probe_ranked_pages()
    step2_add_url_templates()
    step3_update_config(ranked_url)
    step4_mark_post_draft()
    ok = step5_rerun()

    print("=" * 70)
    if ok:
        print("✅ T.3.3.1 complete — screener_picks sources corrected")
        sys.exit(0)
    print("❌ T.3.3.1 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
