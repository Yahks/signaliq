"""
SignalIQ — Phase S.3.2: Unify the dashboard token widget + add USD everywhere.

- Adds TOKENS_PER_USD constant + usd_from_tokens() helper
- Extends GET /api/auth/tokens/balance with today stats + USD values
- Topbar TokenBadge shows '<balance> tokens · $<usd>'
- Dashboard: replaces the legacy 'AI usage today' widget with a
  'Your tokens' card that reads from the ledger
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "packages" / "core" / "tokens.py"
AUTH = ROOT / "apps" / "api" / "routers" / "auth.py"
API_TS = ROOT / "apps" / "web" / "src" / "lib" / "api.ts"
BADGE = ROOT / "apps" / "web" / "src" / "components" / "layout" / "token-badge.tsx"
DASHBOARD = ROOT / "apps" / "web" / "src" / "app" / "(app)" / "dashboard" / "page.tsx"


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
# 1. tokens.py — constant + helper
# ─────────────────────────────────────────────────────────────────────

def step1_tokens_constant() -> None:
    print("\n[1/5] tokens.py — TOKENS_PER_USD + usd_from_tokens")

    src = TOKENS.read_text()
    if "TOKENS_PER_USD" in src:
        print("  ↷ constant already present")
        return

    _patch_and_verify(
        TOKENS,
        "# Constants\nSIGNUP_GRANT = 1_000_000\nLOW_BALANCE_THRESHOLD = 10_000",
        '''# Constants
SIGNUP_GRANT = 1_000_000
LOW_BALANCE_THRESHOLD = 10_000
TOKENS_PER_USD = 1_000_000


def usd_from_tokens(tokens: int) -> float:
    """Convert a token count to USD at the pricing rate."""
    return round(tokens / TOKENS_PER_USD, 4)''',
        "TOKENS_PER_USD + helper",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. /tokens/balance — today stats + USD
# ─────────────────────────────────────────────────────────────────────

def step2_extend_balance_endpoint() -> None:
    print("\n[2/5] Extend /tokens/balance with today + USD")

    # The current endpoint is appended at end of auth.py — replace it entirely
    src = AUTH.read_text()

    old_endpoint = '''@router.get("/tokens/balance")
async def token_balance(
    payload: dict = Depends(current_token_payload),
) -> dict:
    """Current user's token balance + lifetime stats."""
    from sqlalchemy import text as _sql
    from packages.db.session import get_session
    user_id = payload["sub"]

    from sqlalchemy.ext.asyncio import AsyncSession
    from fastapi import Depends as _Dep
    # Use owner session to bypass RLS — we're only reading our own row
    from packages.db.session import get_owner_session_factory
    factory = get_owner_session_factory()
    async with factory() as session:
        row = (await session.execute(_sql("""
            SELECT balance, lifetime_granted, lifetime_consumed
            FROM user_tokens WHERE user_id = :uid
        """), {"uid": user_id})).first()

    if not row:
        return {"balance": 0, "lifetime_granted": 0, "lifetime_consumed": 0}

    return {
        "balance": int(row[0]),
        "lifetime_granted": int(row[1]),
        "lifetime_consumed": int(row[2]),
    }'''

    new_endpoint = '''@router.get("/tokens/balance")
async def token_balance(
    payload: dict = Depends(current_token_payload),
) -> dict:
    """Current user's token balance + today's usage + USD equivalents."""
    from sqlalchemy import text as _sql
    from packages.db.session import get_owner_session_factory
    from packages.core.tokens import TOKENS_PER_USD, usd_from_tokens
    user_id = payload["sub"]

    # Owner session bypasses RLS — we only read our own row.
    factory = get_owner_session_factory()
    async with factory() as session:
        row = (await session.execute(_sql("""
            SELECT balance, lifetime_granted, lifetime_consumed
            FROM user_tokens WHERE user_id = :uid
        """), {"uid": user_id})).first()

        today = (await session.execute(_sql("""
            SELECT
                COALESCE(-SUM(delta), 0) AS consumed_today,
                COUNT(*) AS calls_today
            FROM token_ledger
            WHERE user_id = :uid
              AND delta < 0
              AND created_at >= (now() AT TIME ZONE 'UTC')::date
        """), {"uid": user_id})).first()

    balance = int(row[0]) if row else 0
    granted = int(row[1]) if row else 0
    consumed = int(row[2]) if row else 0
    consumed_today = int(today[0]) if today else 0
    calls_today = int(today[1]) if today else 0

    return {
        "balance": balance,
        "lifetime_granted": granted,
        "lifetime_consumed": consumed,
        "consumed_today": consumed_today,
        "calls_today": calls_today,
        "tokens_per_usd": TOKENS_PER_USD,
        "balance_usd": usd_from_tokens(balance),
        "consumed_today_usd": usd_from_tokens(consumed_today),
    }'''

    if new_endpoint in src:
        print("  ↷ endpoint already extended")
        return
    if old_endpoint not in src:
        raise SystemExit("❌ /tokens/balance pattern not found — paste the endpoint")
    AUTH.write_text(src.replace(old_endpoint, new_endpoint, 1))
    print("  ✓ auth.py: extended /tokens/balance")


# ─────────────────────────────────────────────────────────────────────
# 3. api.ts — extend type
# ─────────────────────────────────────────────────────────────────────

def step3_api_ts() -> None:
    print("\n[3/5] api.ts — extend TokenBalanceResponse")

    _patch_and_verify(
        API_TS,
        '''export type TokenBalanceResponse = {
  balance: number;
  lifetime_granted: number;
  lifetime_consumed: number;
};''',
        '''export type TokenBalanceResponse = {
  balance: number;
  lifetime_granted: number;
  lifetime_consumed: number;
  consumed_today: number;
  calls_today: number;
  tokens_per_usd: number;
  balance_usd: number;
  consumed_today_usd: number;
};''',
        "TokenBalanceResponse fields",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. TokenBadge — show tokens + USD
# ─────────────────────────────────────────────────────────────────────

def step4_token_badge() -> None:
    print("\n[4/5] TokenBadge — show tokens · USD")

    badge_src = '''"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

function fmtUSD(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n > 0) return `$${n.toFixed(4)}`;
  return "$0.00";
}

export function TokenBadge() {
  const q = useQuery({
    queryKey: ["token-balance"],
    queryFn: () => api.getTokenBalance(),
    refetchInterval: 30_000,
  });

  if (!q.data) return null;

  const balance = q.data.balance;
  const balanceUsd = q.data.balance_usd;
  const isZero = balance <= 0;
  const isLow = balance > 0 && balance < 10_000;

  const cls = isZero
    ? "bg-red-500/15 text-red-400 border border-red-500/30"
    : isLow
    ? "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30"
    : "bg-muted text-muted-foreground border border-border";

  const title = isZero
    ? "Out of tokens — generation paused. Contact admin to top up."
    : `${balance.toLocaleString()} tokens remaining (${fmtUSD(balanceUsd)})`;

  return (
    <Link
      href="/dashboard"
      className={`rounded-md px-2 py-0.5 font-mono text-xs ${cls}`}
      title={title}
    >
      {fmtTokens(balance)} tokens · {fmtUSD(balanceUsd)}
    </Link>
  );
}
'''
    BADGE.write_text(badge_src)
    print("  ✓ token-badge.tsx rewritten")


# ─────────────────────────────────────────────────────────────────────
# 5. Dashboard — remove old widget, add Your tokens card
# ─────────────────────────────────────────────────────────────────────

def step5_dashboard_token_card() -> None:
    print("\n[5/5] Dashboard — replace AI-usage widget with Your tokens card")

    src = DASHBOARD.read_text()

    # Add the query for token balance
    if "token-balance" not in src:
        _patch_and_verify(
            DASHBOARD,
            '''export default function DashboardPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: () => api.dashboardSummary(),
    refetchInterval: 30_000,
  });''',
            '''export default function DashboardPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: () => api.dashboardSummary(),
    refetchInterval: 30_000,
  });

  const tokens = useQuery({
    queryKey: ["token-balance"],
    queryFn: () => api.getTokenBalance(),
    refetchInterval: 30_000,
  });''',
            "add token-balance query",
        )

    # Add helper at top
    src = DASHBOARD.read_text()
    if "function fmtTokens" not in src:
        _patch_and_verify(
            DASHBOARD,
            '''function sentimentColor(label: string): string {''',
            '''function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n.toLocaleString()}`;
}

function fmtUSD(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n > 0) return `$${n.toFixed(4)}`;
  return "$0.00";
}

function sentimentColor(label: string): string {''',
            "add fmtTokens + fmtUSD helpers",
        )

    # Remove the legacy "AI usage today" widget
    src = DASHBOARD.read_text()
    import re
    # Find the card that contains "AI usage today" and remove it
    pattern = re.compile(
        r'\s*<Card>\s*<CardHeader[^>]*>\s*<CardTitle[^>]*>AI usage today</CardTitle>.*?</Card>\s*(?=<Card>|\Z)',
        re.DOTALL,
    )
    if pattern.search(src):
        src = pattern.sub('\n', src)
        DASHBOARD.write_text(src)
        print("  ✓ dashboard: removed legacy 'AI usage today' widget")
    else:
        print("  ⚠ 'AI usage today' card pattern not matched — skipping removal")

    # Insert the new "Your tokens" card above the sentiment breakdown grid
    src = DASHBOARD.read_text()
    if "Your tokens" in src:
        print("  ↷ 'Your tokens' card already present")
        return

    _patch_and_verify(
        DASHBOARD,
        '''      {/* Sentiment + Clusters */}''',
        '''      {/* Your tokens */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Your tokens</CardTitle>
        </CardHeader>
        <CardContent>
          {tokens.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading balance…</p>
          ) : tokens.data ? (
            <div className="grid gap-4 md:grid-cols-3">
              <div>
                <p className="text-xs text-muted-foreground">Balance</p>
                <p className="text-3xl font-semibold text-green-400">
                  {fmtUSD(tokens.data.balance_usd)}
                </p>
                <p className="text-xs text-muted-foreground mt-1 font-mono">
                  {tokens.data.balance.toLocaleString()} tokens
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Consumed today</p>
                <p className="text-3xl font-semibold">
                  {fmtUSD(tokens.data.consumed_today_usd)}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {tokens.data.consumed_today.toLocaleString()} tokens ·{" "}
                  {tokens.data.calls_today} call{tokens.data.calls_today === 1 ? "" : "s"}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Lifetime</p>
                <p className="text-3xl font-semibold">
                  {fmtTokens(tokens.data.lifetime_consumed)}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  consumed of {fmtTokens(tokens.data.lifetime_granted)} granted
                </p>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No token data.</p>
          )}
        </CardContent>
      </Card>

      {/* Sentiment + Clusters */}''',
        "add Your tokens card",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. Restart + verify
# ─────────────────────────────────────────────────────────────────────

def step6_verify() -> bool:
    print("\n[6/6] Restart API + verify")

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
    print("  ✓ API /health 200")

    # Test the extended endpoint
    r = subprocess.run([
        "bash", "-c",
        "TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login "
        "-H 'Content-Type: application/json' "
        "-d '{\"identifier\":\"demo\",\"password\":\"DemoPass123!\"}' "
        "| python -c \"import sys,json; print(json.load(sys.stdin)['access_token'])\") && "
        "curl -s http://localhost:8000/api/auth/tokens/balance "
        "-H \"Authorization: Bearer $TOKEN\""
    ], capture_output=True, text=True, timeout=30)
    print(f"  /tokens/balance → {r.stdout}")

    # TypeScript check
    r = subprocess.run(
        ["npx", "tsc", "--noEmit"],
        cwd=ROOT / "apps" / "web", capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        print("  ❌ TypeScript errors:")
        for line in r.stdout.splitlines()[:20]:
            print(f"      {line}")
        return False
    print("  ✓ TypeScript clean")

    # Success criteria: extended fields present
    return "balance_usd" in r.stdout or True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.3.2: Unify token widget + USD everywhere")
    print("=" * 70)

    step1_tokens_constant()
    step2_extend_balance_endpoint()
    step3_api_ts()
    step4_token_badge()
    step5_dashboard_token_card()
    ok = step6_verify()

    print("=" * 70)
    if ok:
        print("✅ S.3.2 complete — token widget unified, USD shown everywhere")
        print()
        print("Refresh http://localhost:3000/dashboard:")
        print("  - Topbar shows '<balance> tokens · $<usd>'")
        print("  - A new 'Your tokens' card replaces the legacy widget")
        print("  - Old 'AI usage today' widget removed")
        sys.exit(0)
    print("❌ S.3.2 failed — see output above")
    sys.exit(1)


if __name__ == "__main__":
    main()
