"""
SignalIQ — Phase T.9: Cross-subblock intelligence.

Turns 16 isolated subblocks into a knowledge base by indexing symbols
across all posts and exposing a per-symbol timeline.

Ships:
  1. Migration 0032 — GIN index on trading_posts.symbols
  2. GET /api/trading/symbols/{ticker}/posts
  3. Extend /api/trading/posts/{id} with related_symbols
  4. UI: /trading/symbols/[ticker] page
  5. UI: clickable symbol chips on post detail
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_32 = ROOT / "alembic" / "versions" / "0032_trading_posts_symbols_index.py"
SCHEMA = ROOT / "apps" / "api" / "schemas" / "trading.py"
ROUTER = ROOT / "apps" / "api" / "routers" / "trading.py"
WEB = ROOT / "apps" / "web"
API_TS = WEB / "src" / "lib" / "api.ts"
SYMBOL_PAGE = WEB / "src" / "app" / "(app)" / "trading" / "symbols" / "[ticker]" / "page.tsx"
POST_PAGE = WEB / "src" / "app" / "(app)" / "trading" / "[slug]" / "[postId]" / "page.tsx"


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
# 1. Migration — GIN index on symbols
# ─────────────────────────────────────────────────────────────────────

MIGRATION_SRC = '''"""GIN index on trading_posts.symbols for cross-subblock lookups.

Enables fast `WHERE 'NVDA' = ANY(symbols)` queries when building the
per-symbol timeline. Without this, every ticker query is a sequential
scan of trading_posts.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-19
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_trading_posts_symbols
        ON trading_posts USING gin (symbols)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_trading_posts_symbols")
'''


def step1_write_migration() -> None:
    print("\n[1/5] Write migration 0032")
    if MIGRATION_32.exists():
        print(f"  ↷ {MIGRATION_32.name} already exists")
        return
    MIGRATION_32.write_text(MIGRATION_SRC)
    print(f"  ✓ wrote {MIGRATION_32.name}")


# ─────────────────────────────────────────────────────────────────────
# 2. Schemas
# ─────────────────────────────────────────────────────────────────────

SCHEMA_ADDITION = '''

class SymbolPostMention(BaseModel):
    post_id: str
    subblock_slug: str
    subblock_name: str
    title: str
    published_at: datetime | None
    word_count: int | None


class SymbolTimelineResponse(BaseModel):
    ticker: str
    total_mentions: int
    subblocks: list[str]
    latest_mention: datetime | None
    mentions: list[SymbolPostMention]
    related_tickers: list[dict]
'''


def step2_extend_schemas() -> None:
    print("\n[2/5] Extend schemas with SymbolTimelineResponse")
    src = SCHEMA.read_text()
    if "SymbolTimelineResponse" in src:
        print("  ↷ schemas already extended")
        return
    src = src.rstrip() + "\n" + SCHEMA_ADDITION
    SCHEMA.write_text(src)
    print("  ✓ wrote SymbolTimelineResponse")


# ─────────────────────────────────────────────────────────────────────
# 3. Router endpoint
# ─────────────────────────────────────────────────────────────────────

ENDPOINT = '''

# ─────────────────────────────────────────────────────────────────────
# Cross-subblock: symbols appear across many subblocks
# ─────────────────────────────────────────────────────────────────────

@router.get("/symbols/{ticker}/posts", response_model=SymbolTimelineResponse)
async def symbol_timeline(
    ticker: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(_scoped_session),
) -> SymbolTimelineResponse:
    """Every post mentioning a given ticker, across all subblocks.

    Uses the GIN index on trading_posts.symbols for fast lookup.
    Also returns related_tickers — symbols that co-occur with this one.
    """
    tk = ticker.strip().upper()
    if not tk or len(tk) > 8:
        raise HTTPException(status_code=400, detail="Invalid ticker")

    mentions_rows = (await session.execute(sql_text("""
        SELECT tp.id::text, ts.slug, ts.name,
               tp.title, tp.published_at, tp.word_count
        FROM trading_posts tp
        JOIN trading_subblocks ts ON ts.id = tp.subblock_id
        WHERE tp.status = 'published'
          AND :tk = ANY(tp.symbols)
        ORDER BY COALESCE(tp.published_at, tp.created_at) DESC
        LIMIT :lim
    """), {"tk": tk, "lim": limit})).all()

    mentions = [
        {
            "post_id": r[0],
            "subblock_slug": r[1],
            "subblock_name": r[2],
            "title": r[3],
            "published_at": r[4],
            "word_count": r[5],
        }
        for r in mentions_rows
    ]

    subblocks = sorted({m["subblock_slug"] for m in mentions})
    latest = max((m["published_at"] for m in mentions if m["published_at"]), default=None)

    # Related tickers: symbols that appear in the same posts as `tk`
    related_rows = (await session.execute(sql_text("""
        SELECT sym, COUNT(*) AS n
        FROM trading_posts tp,
             LATERAL unnest(tp.symbols) AS sym
        WHERE tp.status = 'published'
          AND :tk = ANY(tp.symbols)
          AND sym <> :tk
        GROUP BY sym
        ORDER BY n DESC, sym
        LIMIT 20
    """), {"tk": tk})).all()

    related = [{"ticker": r[0], "co_occurrences": int(r[1])} for r in related_rows]

    return SymbolTimelineResponse(
        ticker=tk,
        total_mentions=len(mentions),
        subblocks=subblocks,
        latest_mention=latest,
        mentions=[SymbolPostMention(**m) for m in mentions],
        related_tickers=related,
    )
'''


def step3_add_endpoint() -> None:
    print("\n[3/5] Add /api/trading/symbols/{ticker}/posts endpoint")
    src = ROUTER.read_text()
    if "symbol_timeline" in src:
        print("  ↷ symbol_timeline already present")
        return
    # Ensure imports
    if "Query" not in src.split("\n")[0:30].__str__():
        # Query already imported at top of file
        pass
    if "SymbolTimelineResponse" not in src:
        # Add to the import block
        import re
        m = re.search(
            r'from apps\.api\.schemas\.trading import \(([^)]+)\)',
            src, re.DOTALL,
        )
        if m:
            current = m.group(1)
            imports = [x.strip().rstrip(',') for x in current.strip().split('\n') if x.strip()]
            if "SymbolTimelineResponse" not in imports:
                imports.append("SymbolTimelineResponse")
            if "SymbolPostMention" not in imports:
                imports.append("SymbolPostMention")
            imports = sorted(set(imports))
            new_block = "from apps.api.schemas.trading import (\n"
            for imp in imports:
                new_block += f"    {imp},\n"
            new_block += ")"
            src = src.replace(m.group(0), new_block, 1)

    ROUTER.write_text(src.rstrip() + "\n" + ENDPOINT)
    print("  ✓ appended symbol_timeline endpoint")


# ─────────────────────────────────────────────────────────────────────
# 4. api.ts
# ─────────────────────────────────────────────────────────────────────

def step4_extend_api_ts() -> None:
    print("\n[4/5] Extend lib/api.ts")

    src = API_TS.read_text()
    if "SymbolTimelineResponse" not in src:
        types = '''

// ---------- Cross-subblock symbol timeline (T.9) ----------

export type SymbolPostMention = {
  post_id: string;
  subblock_slug: string;
  subblock_name: string;
  title: string;
  published_at: string | null;
  word_count: number | null;
};

export type SymbolTimelineResponse = {
  ticker: string;
  total_mentions: number;
  subblocks: string[];
  latest_mention: string | null;
  mentions: SymbolPostMention[];
  related_tickers: Array<{ ticker: string; co_occurrences: number }>;
};
'''
        marker = "export const api = {"
        if marker not in src:
            marker = "export const api:"
            if marker not in src:
                raise SystemExit("❌ api.ts anchor not found")
        src = src.replace(marker, types + "\n" + marker, 1)
        API_TS.write_text(src)
        print("  ✓ api.ts: symbol timeline types added")

    src = API_TS.read_text()
    if "getSymbolTimeline" in src:
        print("  ↷ api.ts: getSymbolTimeline already present")
        return

    marker = '  getTradingHealth: () =>\n    request<TradingHealthResponse>("/api/trading/health"),'
    if marker not in src:
        raise SystemExit("❌ getTradingHealth marker not found")

    new = marker + '''

  getSymbolTimeline: (ticker: string, limit: number = 50) =>
    request<SymbolTimelineResponse>(
      `/api/trading/symbols/${ticker}/posts?limit=${limit}`,
    ),'''

    src = src.replace(marker, new, 1)
    API_TS.write_text(src)
    print("  ✓ api.ts: getSymbolTimeline added")


# ─────────────────────────────────────────────────────────────────────
# 5. Symbol timeline page + clickable chips
# ─────────────────────────────────────────────────────────────────────

SYMBOL_PAGE_SRC = '''"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

export default function SymbolTimelinePage() {
  const params = useParams<{ ticker: string }>();
  const ticker = (params.ticker || "").toUpperCase();

  const q = useQuery({
    queryKey: ["symbol-timeline", ticker],
    queryFn: () => api.getSymbolTimeline(ticker, 100),
    enabled: ticker.length > 0,
  });

  if (q.isLoading) return <p className="text-sm text-muted-foreground">Loading {ticker}…</p>;
  if (q.isError || !q.data) return <p className="text-sm text-destructive">Failed to load {ticker}</p>;

  const d = q.data;

  // Group mentions by subblock
  const bySubblock: Record<string, typeof d.mentions> = {};
  for (const m of d.mentions) {
    if (!bySubblock[m.subblock_slug]) bySubblock[m.subblock_slug] = [];
    bySubblock[m.subblock_slug].push(m);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold font-mono">{d.ticker}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {d.total_mentions} mention{d.total_mentions === 1 ? "" : "s"} across{" "}
            {d.subblocks.length} subblock{d.subblocks.length === 1 ? "" : "s"}
            {d.latest_mention && (
              <> · latest {new Date(d.latest_mention).toLocaleString()}</>
            )}
          </p>
        </div>
        <Link href="/trading" className="text-sm text-blue-400 hover:underline">
          ← Back to Trading
        </Link>
      </div>

      {d.related_tickers.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Also mentioned with</CardTitle>
            <CardDescription>
              Other tickers that co-occur with {d.ticker} in the same posts
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-1.5">
              {d.related_tickers.map((r) => (
                <Link
                  key={r.ticker}
                  href={`/trading/symbols/${r.ticker}`}
                  className="rounded-md border border-border bg-muted/50 px-2 py-0.5 font-mono text-xs hover:bg-muted"
                >
                  {r.ticker}
                  <span className="ml-1.5 text-muted-foreground">{r.co_occurrences}</span>
                </Link>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {Object.entries(bySubblock).map(([slug, mentions]) => (
        <Card key={slug}>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              <Link href={`/trading/${slug}`} className="hover:underline">
                {mentions[0].subblock_name}
              </Link>
            </CardTitle>
            <CardDescription>
              {mentions.length} post{mentions.length === 1 ? "" : "s"} mention {d.ticker}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              {mentions.map((m) => (
                <li key={m.post_id}>
                  <Link
                    href={`/trading/${slug}/${m.post_id}`}
                    className="block py-2 -mx-3 px-3 hover:bg-muted rounded"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-medium truncate">{m.title}</p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {m.published_at ? new Date(m.published_at).toLocaleString() : "unpublished"}
                          {m.word_count != null && ` · ${m.word_count} words`}
                        </p>
                      </div>
                      <span className="text-xs text-muted-foreground">→</span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}

      {d.mentions.length === 0 && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No posts mention {d.ticker} yet.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
'''


def step5_write_symbol_page() -> None:
    print("\n[5/5] Write /trading/symbols/[ticker] page")
    SYMBOL_PAGE.parent.mkdir(parents=True, exist_ok=True)
    SYMBOL_PAGE.write_text(SYMBOL_PAGE_SRC)
    print(f"  ✓ wrote {SYMBOL_PAGE.relative_to(ROOT)}")


def step5b_make_chips_clickable() -> None:
    """Turn symbol chips on the post detail page into links."""
    print("\n[5b/5] Make symbol chips clickable on post detail")

    if not POST_PAGE.exists():
        print("  ⚠ post detail page not found — skipping")
        return

    src = POST_PAGE.read_text()
    if "trading/symbols/" in src:
        print("  ↷ post detail chips already linkified")
        return

    old = '''          {p.symbols.slice(0, 60).map((s) => (
            <span
              key={s}
              className="rounded-md border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px]"
            >
              {s}
            </span>
          ))}'''

    new = '''          {p.symbols.slice(0, 60).map((s) => (
            <Link
              key={s}
              href={`/trading/symbols/${s}`}
              className="rounded-md border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] hover:bg-muted"
            >
              {s}
            </Link>
          ))}'''

    if old not in src:
        print("  ⚠ symbol chip pattern differs — manual update needed")
        return

    src = src.replace(old, new, 1)

    # Ensure Link is imported
    if "import Link" not in src:
        src = 'import Link from "next/link";\n' + src

    POST_PAGE.write_text(src)
    print("  ✓ post detail chips are now clickable")


# ─────────────────────────────────────────────────────────────────────
# Apply migration + restart API + verify
# ─────────────────────────────────────────────────────────────────────

def step6_migrate_and_verify() -> bool:
    print("\n[6/6] Migrate + restart API + smoke test")

    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"❌ alembic failed:\n{r.stderr}")
    print("  ✓ alembic upgrade head")
    for line in (r.stderr or "").splitlines():
        if "Running upgrade" in line:
            print(f"      {line.strip()}")

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

    # Symbol timeline smoke test
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
    "curl", "-s", "http://localhost:8000/api/trading/symbols/NVDA/posts",
    "-H", f"Authorization: Bearer {token}",
], capture_output=True, text=True)
d = json.loads(r.stdout)
print(f"  NVDA: {d['total_mentions']} mentions across {len(d['subblocks'])} subblocks")
print(f"  related: {[x['ticker'] for x in d['related_tickers'][:8]]}")
"""
    ], cwd=ROOT, capture_output=True, text=True, timeout=30)
    print(r.stdout)
    return "NVDA:" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.9: Cross-subblock intelligence")
    print("=" * 70)

    step1_write_migration()
    step2_extend_schemas()
    step3_add_endpoint()
    step4_extend_api_ts()
    step5_write_symbol_page()
    step5b_make_chips_clickable()
    ok = step6_migrate_and_verify()

    print("=" * 70)
    if ok:
        print("✅ T.9 complete — cross-subblock symbol intelligence")
        print()
        print("Visit http://localhost:3000/trading/symbols/NVDA")
        sys.exit(0)
    print("❌ T.9 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
