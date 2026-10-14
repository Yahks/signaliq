"""
SignalIQ — Phase T.9.2: Entry points to the symbol timeline.

T.9 shipped /trading/symbols/{ticker} but there's no way to reach it
except by URL or by clicking a chip in a post. This adds:

  1. /trading/symbols index page — search + top tickers
  2. /trading hub "Browse by symbol" card
  3. Sidebar link
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "apps" / "api" / "schemas" / "trading.py"
ROUTER = ROOT / "apps" / "api" / "routers" / "trading.py"
WEB = ROOT / "apps" / "web"
API_TS = WEB / "src" / "lib" / "api.ts"
HUB = WEB / "src" / "app" / "(app)" / "trading" / "page.tsx"
SYMBOLS_INDEX = WEB / "src" / "app" / "(app)" / "trading" / "symbols" / "page.tsx"
SIDEBAR = WEB / "src" / "components" / "layout" / "sidebar.tsx"


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
# 1. New endpoint: top tickers by mention count
# ─────────────────────────────────────────────────────────────────────

ENDPOINT = '''

# ─────────────────────────────────────────────────────────────────────
# Ticker index — top tickers by mention count across all subblocks
# ─────────────────────────────────────────────────────────────────────

@router.get("/symbols", response_model=SymbolIndexResponse)
async def symbols_index(
    limit: int = Query(default=60, ge=1, le=200),
    session: AsyncSession = Depends(_scoped_session),
) -> SymbolIndexResponse:
    """Ranked list of every ticker mentioned, by number of posts.

    Powered by the GIN index on trading_posts.symbols. Used by the
    /trading/symbols index page and the hub's 'Browse by symbol' card.
    """
    rows = (await session.execute(sql_text("""
        SELECT sym, COUNT(*)::int AS mentions
        FROM trading_posts tp,
             LATERAL unnest(tp.symbols) AS sym
        WHERE tp.status = 'published'
        GROUP BY sym
        ORDER BY mentions DESC, sym ASC
        LIMIT :lim
    """), {"lim": limit})).all()

    items = [
        {"ticker": r[0], "mentions": int(r[1])}
        for r in rows
    ]
    return SymbolIndexResponse(items=items, total=len(items))
'''


def step1_add_endpoint() -> None:
    print("\n[1/5] Add /api/trading/symbols index endpoint")

    # Add schema
    src = SCHEMA.read_text()
    if "SymbolIndexResponse" not in src:
        src = src.rstrip() + '''


class SymbolIndexItem(BaseModel):
    ticker: str
    mentions: int


class SymbolIndexResponse(BaseModel):
    items: list[SymbolIndexItem]
    total: int
'''
        SCHEMA.write_text(src)
        print("  ✓ schemas: SymbolIndexResponse added")

    # Add endpoint
    src = ROUTER.read_text()
    if "symbols_index" in src:
        print("  ↷ symbols_index already present")
        return

    # Add schemas to import block
    import re
    m = re.search(
        r'from apps\.api\.schemas\.trading import \(([^)]+)\)',
        src, re.DOTALL,
    )
    if m:
        current = m.group(1)
        imports = [x.strip().rstrip(',') for x in current.strip().split('\n') if x.strip()]
        for cls in ("SymbolIndexItem", "SymbolIndexResponse"):
            if cls not in imports:
                imports.append(cls)
        imports = sorted(set(imports))
        new_block = "from apps.api.schemas.trading import (\n"
        for imp in imports:
            new_block += f"    {imp},\n"
        new_block += ")"
        src = src.replace(m.group(0), new_block, 1)

    ROUTER.write_text(src.rstrip() + "\n" + ENDPOINT)
    print("  ✓ endpoint appended")


# ─────────────────────────────────────────────────────────────────────
# 2. api.ts
# ─────────────────────────────────────────────────────────────────────

def step2_extend_api_ts() -> None:
    print("\n[2/5] Extend lib/api.ts")
    src = API_TS.read_text()

    if "SymbolIndexResponse" not in src:
        types = '''

// ---------- Symbol index (T.9.2) ----------

export type SymbolIndexItem = {
  ticker: string;
  mentions: number;
};

export type SymbolIndexResponse = {
  items: SymbolIndexItem[];
  total: number;
};
'''
        marker = "export const api = {"
        if marker not in src:
            marker = "export const api:"
        if marker not in src:
            raise SystemExit("❌ api.ts anchor not found")
        src = src.replace(marker, types + "\n" + marker, 1)
        API_TS.write_text(src)
        print("  ✓ api.ts: types added")

    src = API_TS.read_text()
    if "listSymbols" in src:
        print("  ↷ api.ts: listSymbols already present")
        return

    marker = '  getSymbolTimeline: (ticker: string, limit: number = 50) =>'
    if marker not in src:
        raise SystemExit("❌ getSymbolTimeline marker not found")

    # Insert new method before the timeline method
    new = '''  listSymbols: (limit: number = 60) =>
    request<SymbolIndexResponse>(`/api/trading/symbols?limit=${limit}`),

''' + marker

    src = src.replace(marker, new, 1)
    API_TS.write_text(src)
    print("  ✓ api.ts: listSymbols added")


# ─────────────────────────────────────────────────────────────────────
# 3. /trading/symbols index page
# ─────────────────────────────────────────────────────────────────────

SYMBOLS_INDEX_SRC = '''"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function SymbolsIndexPage() {
  const router = useRouter();
  const [query, setQuery] = useState("");

  const q = useQuery({
    queryKey: ["symbols-index"],
    queryFn: () => api.listSymbols(120),
    refetchInterval: 60_000,
  });

  const items = q.data?.items ?? [];

  function search(e: React.FormEvent) {
    e.preventDefault();
    const t = query.trim().toUpperCase();
    if (t) router.push(`/trading/symbols/${t}`);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Symbols</h1>
          <p className="text-sm text-muted-foreground">
            {items.length} tickers mentioned across the trading block
          </p>
        </div>
        <Link href="/trading" className="text-sm text-blue-400 hover:underline">
          ← Back to Trading
        </Link>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Look up a ticker</CardTitle>
          <CardDescription>
            Type any ticker to see every post mentioning it
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={search} className="flex gap-2 max-w-md">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. NVDA"
              className="flex-1 rounded-md border border-border bg-transparent px-3 py-2 text-sm font-mono uppercase"
              autoFocus
            />
            <Button type="submit" disabled={!query.trim()}>Open</Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Most mentioned tickers</CardTitle>
          <CardDescription>By number of posts across all subblocks</CardDescription>
        </CardHeader>
        <CardContent>
          {q.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No tickers indexed yet.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {items.map((s) => (
                <Link
                  key={s.ticker}
                  href={`/trading/symbols/${s.ticker}`}
                  className="rounded-md border border-border bg-muted/50 px-2 py-1 font-mono text-xs hover:bg-muted"
                >
                  {s.ticker}
                  <span className="ml-1.5 text-muted-foreground">{s.mentions}</span>
                </Link>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
'''


def step3_write_symbols_index() -> None:
    print("\n[3/5] Write /trading/symbols index page")
    SYMBOLS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    SYMBOLS_INDEX.write_text(SYMBOLS_INDEX_SRC)
    print(f"  ✓ wrote {SYMBOLS_INDEX.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 4. Hub page: "Browse by symbol" card
# ─────────────────────────────────────────────────────────────────────

def step4_add_hub_card() -> None:
    print("\n[4/5] Add 'Browse by symbol' card to hub")

    src = HUB.read_text()
    if "Browse by symbol" in src:
        print("  ↷ hub already has symbols section")
        return

    # Add the query for symbols
    _patch_and_verify(
        HUB,
        '''export default function TradingHubPage() {
  const q = useQuery({
    queryKey: ["trading-subblocks"],
    queryFn: () => api.listTradingSubblocks(),
    refetchInterval: 60_000,
  });

  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;''',
        '''export default function TradingHubPage() {
  const q = useQuery({
    queryKey: ["trading-subblocks"],
    queryFn: () => api.listTradingSubblocks(),
    refetchInterval: 60_000,
  });

  const symbols = useQuery({
    queryKey: ["symbols-index"],
    queryFn: () => api.listSymbols(20),
    refetchInterval: 60_000,
  });

  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const topSymbols = symbols.data?.items ?? [];''',
        "add symbols query",
    )

    # Insert the "Browse by symbol" card before the subblocks grid
    _patch_and_verify(
        HUB,
        '''      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading subblocks…</p>
      ) : items.length === 0 ? (''',
        '''      {topSymbols.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-start justify-between gap-3">
              <div>
                <CardTitle className="text-base">Browse by symbol</CardTitle>
                <CardDescription>
                  Top tickers mentioned across all subblocks
                </CardDescription>
              </div>
              <Link href="/trading/symbols" className="text-xs text-blue-400 hover:underline whitespace-nowrap">
                See all →
              </Link>
            </div>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-1.5">
              {topSymbols.map((s) => (
                <Link
                  key={s.ticker}
                  href={`/trading/symbols/${s.ticker}`}
                  className="rounded-md border border-border bg-muted/50 px-2 py-1 font-mono text-xs hover:bg-muted"
                >
                  {s.ticker}
                  <span className="ml-1.5 text-muted-foreground">{s.mentions}</span>
                </Link>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading subblocks…</p>
      ) : items.length === 0 ? (''',
        "add Browse-by-symbol card",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. Sidebar: Symbols link + TS check
# ─────────────────────────────────────────────────────────────────────

def step5_sidebar_and_typecheck() -> bool:
    print("\n[5/5] Sidebar link + TypeScript check")

    src = SIDEBAR.read_text()
    if '"/trading/symbols"' not in src:
        anchor = '  { href: "/admin/trading", label: "Gen health", icon: Activity },'
        if anchor in src:
            _patch_and_verify(
                SIDEBAR,
                anchor,
                anchor + '\n  { href: "/trading/symbols", label: "Symbols", icon: Hash },',
                "add Symbols link",
            )
            src2 = SIDEBAR.read_text()
            import re
            m = re.search(r'import \{([^}]+)\} from "lucide-react"', src2)
            if m and "Hash" not in m.group(1):
                new_imports = m.group(1).rstrip() + ", Hash"
                src2 = src2.replace(m.group(0), f'import {{{new_imports}}} from "lucide-react"')
                SIDEBAR.write_text(src2)
                print("  ✓ sidebar.tsx: Hash import added")
        else:
            print("  ⚠ sidebar.tsx: /admin/trading anchor not found")

    print("\n  running tsc --noEmit...")
    r = subprocess.run(
        ["npx", "tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        print("  ❌ TypeScript errors:")
        for line in r.stdout.splitlines()[:20]:
            print(f"      {line}")
        return False
    print("  ✓ TypeScript clean")
    return True


# ─────────────────────────────────────────────────────────────────────
# 6. Restart API + smoke test
# ─────────────────────────────────────────────────────────────────────

def step6_restart_and_verify() -> bool:
    print("\n[6/6] Restart API + smoke test")

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

    # Smoke test the new endpoint
    r = subprocess.run([
        "python", "-c", """
import subprocess, json
r = subprocess.run([
    "curl", "-s", "-X", "POST", "http://localhost:8000/api/auth/login",
    "-H", "Content-Type: application/json",
    "-d", '{"email":"elearningyarhaya@gmail.com","password":"12345678"}',
], capture_output=True, text=True)
token = json.loads(r.stdout)["access_token"]

r = subprocess.run([
    "curl", "-s", "http://localhost:8000/api/trading/symbols?limit=10",
    "-H", f"Authorization: Bearer {token}",
], capture_output=True, text=True)
d = json.loads(r.stdout)
print(f"  top tickers: {[x['ticker'] for x in d['items'][:8]]}")
print(f"  total: {d['total']}")
"""
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    print(r.stdout)
    return "top tickers:" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.9.2: Symbol entry points")
    print("=" * 70)

    step1_add_endpoint()
    step2_extend_api_ts()
    step3_write_symbols_index()
    step4_add_hub_card()
    ok_ts = step5_sidebar_and_typecheck()
    ok_api = step6_restart_and_verify()

    print("=" * 70)
    if ok_ts and ok_api:
        print("✅ T.9.2 complete — 3 ways to reach /trading/symbols")
        print()
        print("  Sidebar → Symbols")
        print("  Hub → 'Browse by symbol' card → See all")
        print("  /trading/symbols → search any ticker")
        sys.exit(0)
    print("❌ T.9.2 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
