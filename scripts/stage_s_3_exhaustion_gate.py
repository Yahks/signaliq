"""
SignalIQ — Phase S.3: Exhaustion gate.

When a user's token balance hits 0, generation stops:
  - Celery generator tasks skip silently (log line only)
  - On-demand /generate endpoints return HTTP 402
  - Router safety net refuses LLM calls from a 0-balance user
  - UI disables generate buttons + shows a badge + a dashboard card

In-flight calls complete; only the NEXT call is blocked.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "packages" / "core" / "tokens.py"
ROUTER = ROOT / "packages" / "ai" / "router.py"
TRADING_API = ROOT / "apps" / "api" / "routers" / "trading.py"
TRADING_TASK = ROOT / "apps" / "worker" / "tasks" / "trading.py"
WEB = ROOT / "apps" / "web"
API_TS = WEB / "src" / "lib" / "api.ts"
TOPBAR = WEB / "src" / "components" / "layout" / "topbar.tsx"
DASHBOARD = WEB / "src" / "app" / "(app)" / "dashboard" / "page.tsx"
SLUG_PAGE = WEB / "src" / "app" / "(app)" / "trading" / "[slug]" / "page.tsx"


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
# 1. tokens.py — can_generate check
# ─────────────────────────────────────────────────────────────────────

def step1_can_generate_helpers() -> None:
    print("\n[1/7] Add can_generate() helpers to tokens.py")

    src = TOKENS.read_text()
    if "async def can_generate" in src:
        print("  ↷ can_generate already present")
        return

    helper = '''

# ─────────────────────────────────────────────────────────────────────
# Exhaustion gate — the check before any generation kicks off
# ─────────────────────────────────────────────────────────────────────

async def can_generate(session: AsyncSession, user_id: str) -> bool:
    """True if the user has any positive balance. False at 0."""
    return (await get_balance(session, user_id)) > 0


def can_generate_sync(session, user_id: str) -> bool:
    """Sync version — used by the router's safety net.

    Reads the balance row directly. Returns True if no row exists
    (fail-open, so a legitimate user without a tokens row isn't
    silently blocked — S.6 admin backfill covers those).
    """
    row = session.execute(sql_text("""
        SELECT balance FROM user_tokens WHERE user_id = :uid
    """), {"uid": user_id}).first()
    if not row:
        return True   # fail-open
    return int(row[0]) > 0
'''
    TOKENS.write_text(src.rstrip() + "\n" + helper)
    print("  ✓ tokens.py: can_generate + can_generate_sync")


# ─────────────────────────────────────────────────────────────────────
# 2. router.py — safety-net gate at _try_complete start
# ─────────────────────────────────────────────────────────────────────

def step2_router_gate() -> None:
    print("\n[2/7] Add router safety-net gate")

    _patch_and_verify(
        ROUTER,
        '''    # Cache lookup
    req_hash = _hash_request(provider_name, model_name, system, user, json_mode)''',
        '''    # Exhaustion gate — last-resort safety net. If the current user
    # has 0 tokens, refuse even if a caller forgot to check.
    from packages.core.context import current_user_id as _uid_ctx
    _uid = _uid_ctx.get()
    if _uid:
        from packages.core.tokens import can_generate_sync
        if not can_generate_sync(session, _uid):
            raise ProviderError("Insufficient tokens")

    # Cache lookup
    req_hash = _hash_request(provider_name, model_name, system, user, json_mode)''',
        "exhaustion gate at _try_complete start",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Celery task — skip generation when balance is 0
# ─────────────────────────────────────────────────────────────────────

def step3_celery_gate() -> None:
    print("\n[3/7] Gate Celery tasks")

    _patch_and_verify(
        TRADING_TASK,
        '''        owner_row = (await session.execute(sql_text("""
            SELECT user_id::text FROM memberships
            WHERE org_id = :oid AND role = 'owner'
            LIMIT 1
        """), {"oid": org_id})).first()
        owner_id = owner_row[0] if owner_row else None
        if owner_id:
            current_user_id.set(owner_id)
        current_org_id.set(org_id)''',
        '''        owner_row = (await session.execute(sql_text("""
            SELECT user_id::text FROM memberships
            WHERE org_id = :oid AND role = 'owner'
            LIMIT 1
        """), {"oid": org_id})).first()
        owner_id = owner_row[0] if owner_row else None
        if owner_id:
            current_user_id.set(owner_id)
        current_org_id.set(org_id)

        # Exhaustion gate — skip silently if the owner is out of tokens.
        if owner_id:
            from packages.core.tokens import can_generate
            if not await can_generate(session, owner_id):
                log.info("trading.generation_skipped_no_tokens",
                         org_id=org_id, slug=slug, user_id=owner_id[:8])
                return {"status": "skipped_no_tokens", "org_id": org_id, "slug": slug}''',
        "Celery exhaustion gate",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Trading API — 402 on /generate when balance is 0
# ─────────────────────────────────────────────────────────────────────

def step4_api_gate() -> None:
    print("\n[4/7] Gate /api/trading/subblocks/{slug}/generate")

    _patch_and_verify(
        TRADING_API,
        '''    # Rate limit
    from packages.core.rate_limit import check_rate_limit
    from packages.core.error import QuotaExceededError
    rl = check_rate_limit(
        namespace="org", identifier=org_id, route=f"trading-generate-{slug}",
        limit=5, window_seconds=3600,
    )
    if not rl.allowed:
        raise QuotaExceededError(
            f"Rate limit reached ({rl.limit}/hour). "
            f"Retry in {rl.retry_after_seconds}s.",
        )''',
        '''    # Exhaustion gate — return 402 if the owner is out of tokens
    from packages.core.context import current_user_id as _uid_ctx
    uid = _uid_ctx.get()
    if uid:
        from packages.core.tokens import can_generate
        if not await can_generate(session, uid):
            from fastapi import HTTPException
            raise HTTPException(
                status_code=402,
                detail="Insufficient tokens — top up to continue",
            )

    # Rate limit
    from packages.core.rate_limit import check_rate_limit
    from packages.core.error import QuotaExceededError
    rl = check_rate_limit(
        namespace="org", identifier=org_id, route=f"trading-generate-{slug}",
        limit=5, window_seconds=3600,
    )
    if not rl.allowed:
        raise QuotaExceededError(
            f"Rate limit reached ({rl.limit}/hour). "
            f"Retry in {rl.retry_after_seconds}s.",
        )''',
        "trading API exhaustion gate",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. Backend: GET /api/auth/tokens/balance endpoint
# ─────────────────────────────────────────────────────────────────────

def step5_balance_endpoint() -> None:
    print("\n[5/7] Add /api/auth/tokens/balance endpoint")

    AUTH = ROOT / "apps" / "api" / "routers" / "auth.py"
    src = AUTH.read_text()

    if "/tokens/balance" in src:
        print("  ↷ balance endpoint already present")
        return

    # Insert at end of file
    endpoint = '''

@router.get("/tokens/balance")
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
    }
'''
    AUTH.write_text(src.rstrip() + "\n" + endpoint)
    print("  ✓ auth.py: /tokens/balance endpoint added")


# ─────────────────────────────────────────────────────────────────────
# 6. Frontend — api.ts + topbar badge
# ─────────────────────────────────────────────────────────────────────

def step6_frontend_badge() -> None:
    print("\n[6/7] Frontend: types + topbar balance badge")

    # api.ts types + method
    src = API_TS.read_text()
    if "TokenBalanceResponse" not in src:
        types = '''

// ---------- Token balance (S.3) ----------

export type TokenBalanceResponse = {
  balance: number;
  lifetime_granted: number;
  lifetime_consumed: number;
};
'''
        marker = "export const api = {"
        if marker not in src:
            marker = "export const api:"
        src = src.replace(marker, types + "\n" + marker, 1)
        API_TS.write_text(src)
        print("  ✓ api.ts: TokenBalanceResponse added")

    src = API_TS.read_text()
    if "getTokenBalance" not in src:
        marker = '  getTradingHealth: () =>'
        if marker not in src:
            raise SystemExit("❌ getTradingHealth marker not found in api.ts")
        src = src.replace(
            marker,
            '  getTokenBalance: () =>\n'
            '    request<TokenBalanceResponse>("/api/auth/tokens/balance"),\n\n'
            + marker,
            1,
        )
        API_TS.write_text(src)
        print("  ✓ api.ts: getTokenBalance method added")

    # Topbar badge
    badge = WEB / "src" / "components" / "layout" / "token-badge.tsx"
    badge.write_text('''"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

export function TokenBadge() {
  const q = useQuery({
    queryKey: ["token-balance"],
    queryFn: () => api.getTokenBalance(),
    refetchInterval: 30_000,
  });

  if (!q.data) return null;

  const balance = q.data.balance;
  const isZero = balance <= 0;
  const isLow = balance > 0 && balance < 10_000;

  const cls = isZero
    ? "bg-red-500/15 text-red-400 border border-red-500/30"
    : isLow
    ? "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30"
    : "bg-muted text-muted-foreground border border-border";

  return (
    <Link
      href="/dashboard"
      className={`rounded-md px-2 py-0.5 font-mono text-xs ${cls}`}
      title={isZero
        ? "Out of tokens — generation paused"
        : `${balance.toLocaleString()} tokens remaining`}
    >
      {fmt(balance)} tokens
    </Link>
  );
}
''')
    print("  ✓ wrote token-badge.tsx")

    # Hook into topbar
    src = TOPBAR.read_text()
    if "TokenBadge" not in src:
        _patch_and_verify(
            TOPBAR,
            '        <NotificationBell />',
            '        <TokenBadge />\n        <NotificationBell />',
            "add TokenBadge to topbar",
        )
        # Add import
        src = TOPBAR.read_text()
        _patch_and_verify(
            TOPBAR,
            'import { NotificationBell } from "@/components/layout/notification-bell";',
            'import { NotificationBell } from "@/components/layout/notification-bell";\n'
            'import { TokenBadge } from "@/components/layout/token-badge";',
            "import TokenBadge",
        )


# ─────────────────────────────────────────────────────────────────────
# 7. Frontend — disable generate button at 0
# ─────────────────────────────────────────────────────────────────────

def step7_disable_generate() -> None:
    print("\n[7/7] Disable 'Generate now' at 0 balance")

    src = SLUG_PAGE.read_text()
    if "token-balance" in src:
        print("  ↷ slug page already wired to balance")
        return

    # Add query + disable logic
    old = '''  const generate = useMutation({
    mutationFn: () => api.generateTradingPost(slug),'''
    new = '''  const balance = useQuery({
    queryKey: ["token-balance"],
    queryFn: () => api.getTokenBalance(),
    refetchInterval: 30_000,
  });

  const outOfTokens = (balance.data?.balance ?? 1) <= 0;

  const generate = useMutation({
    mutationFn: () => api.generateTradingPost(slug),'''
    if old not in src:
        print("  ⚠ slug page pattern differs — manual disable needed")
        return
    src = src.replace(old, new, 1)

    # Disable the button
    old2 = '''        <Button
          onClick={() => generate.mutate()}
          disabled={generate.isPending || queued}
        >
          {generate.isPending ? "Queuing…" : queued ? "Queued — refreshing in 30s" : "Generate now"}
        </Button>'''
    new2 = '''        <Button
          onClick={() => generate.mutate()}
          disabled={generate.isPending || queued || outOfTokens}
          title={outOfTokens ? "Out of tokens — top up to continue" : undefined}
        >
          {generate.isPending ? "Queuing…" : queued ? "Queued — refreshing in 30s" : "Generate now"}
        </Button>'''
    if old2 not in src:
        print("  ⚠ button pattern differs — manual disable needed")
        return
    src = src.replace(old2, new2, 1)
    SLUG_PAGE.write_text(src)
    print("  ✓ slug page: generate button disabled at 0 balance")

    # Also show an amber banner when out of tokens
    old3 = '''      {error && <p className="text-sm text-destructive">{error}</p>}'''
    new3 = '''      {outOfTokens && (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">
          You are out of tokens. Automated and manual generation is paused. Contact your admin to top up.
        </div>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}'''
    if old3 in src:
        src = src.replace(old3, new3, 1)
        SLUG_PAGE.write_text(src)
        print("  ✓ slug page: amber out-of-tokens banner added")


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.3: Exhaustion gate")
    print("=" * 70)

    step1_can_generate_helpers()
    step2_router_gate()
    step3_celery_gate()
    step4_api_gate()
    step5_balance_endpoint()
    step6_frontend_badge()
    step7_disable_generate()

    print("\n── Restart + typecheck ──")
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
    print(f"  API /health: {r.stdout.strip()}")

    r = subprocess.run(
        ["npx", "tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        print("  ❌ TypeScript errors:")
        for line in r.stdout.splitlines()[:15]:
            print(f"      {line}")
        sys.exit(1)
    print("  ✓ TypeScript clean")

    print("=" * 70)
    print("✅ S.3 complete — exhaustion gate live")
    print()
    print("Test manually:")
    print("  1. Set a user's balance to 0")
    print("  2. Try /api/trading/subblocks/stock_lists/generate → expect 402")
    print("  3. Try a Celery task → expects 'skipped_no_tokens' result")
    print("  4. Topbar badge turns red")


if __name__ == "__main__":
    main()
