"""
SignalIQ — Phase T.8: Trading block observability.

Adds:
  - GET /api/admin/trading/health  (per-subblock generation stats)
  - /admin/trading UI page          (health dashboard)
  - Sidebar link under "Settings"
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
ADMIN_PAGE = WEB / "src" / "app" / "(app)" / "admin" / "trading" / "page.tsx"
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
# 1. Schema
# ─────────────────────────────────────────────────────────────────────

SCHEMA_ADDITION = '''

class TradingSubblockHealth(BaseModel):
    slug: str
    name: str
    cadence: str
    source_kind: str | None
    # 24h stats
    posts_24h: int
    drafts_24h: int
    last_published_at: datetime | None
    last_created_at: datetime | None
    avg_word_count_24h: float | None
    # Health verdict
    minutes_since_last: int | None
    expected_cadence_minutes: int
    status: str   # "healthy" | "delayed" | "stale" | "never_run"


class TradingHealthResponse(BaseModel):
    items: list[TradingSubblockHealth]
    summary: dict
    generated_at: datetime
'''


def step1_extend_schemas() -> None:
    print("\n[1/5] Extend API schemas")
    _patch_and_verify(
        SCHEMA,
        'class TradingGenerateResponse(BaseModel):\n    task_id: str\n    status: str\n    slug: str',
        'class TradingGenerateResponse(BaseModel):\n    task_id: str\n    status: str\n    slug: str\n' + SCHEMA_ADDITION,
        "add TradingSubblockHealth schemas",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Router endpoint
# ─────────────────────────────────────────────────────────────────────

HEALTH_ENDPOINT = '''

# ─────────────────────────────────────────────────────────────────────
# Observability — health of the automated generation
# ─────────────────────────────────────────────────────────────────────

# Expected cadence in minutes, per cadence string
_CADENCE_MINUTES = {
    "hourly_market": 60,
    "daily": 24 * 60,
    "event_driven": 24 * 60,   # treat event-driven as daily minimum
    "manual": 24 * 60 * 7,     # weekly, low bar
}


@router.get("/health", response_model=TradingHealthResponse)
async def trading_health(
    session: AsyncSession = Depends(_scoped_session),
) -> TradingHealthResponse:
    """Health snapshot of the automated trading generation pipeline.

    Per subblock: how many posts in the last 24h, when the last one
    landed, and a health verdict comparing observed vs expected cadence.
    """
    from datetime import UTC, datetime, timedelta
    from apps.api.schemas.trading import (
        TradingHealthResponse,
        TradingSubblockHealth,
    )

    now = datetime.now(UTC)
    since = now - timedelta(hours=24)

    rows = (await session.execute(sql_text("""
        SELECT
            ts.slug, ts.name, ts.cadence, ts.source_kind,
            COALESCE(tp_pub.n, 0) AS posts_24h,
            COALESCE(tp_dft.n, 0) AS drafts_24h,
            tp_pub.last_pub AS last_published_at,
            GREATEST(tp_pub.last_pub, tp_dft.last_any) AS last_created_at,
            tp_pub.avg_words AS avg_word_count_24h
        FROM trading_subblocks ts
        LEFT JOIN (
            SELECT subblock_id,
                   COUNT(*) AS n,
                   MAX(published_at) AS last_pub,
                   AVG(word_count)::float AS avg_words
            FROM trading_posts
            WHERE status = 'published' AND created_at >= :since
            GROUP BY subblock_id
        ) tp_pub ON tp_pub.subblock_id = ts.id
        LEFT JOIN (
            SELECT subblock_id, COUNT(*) AS n, MAX(created_at) AS last_any
            FROM trading_posts
            WHERE status = 'draft' AND created_at >= :since
            GROUP BY subblock_id
        ) tp_dft ON tp_dft.subblock_id = ts.id
        WHERE ts.enabled = true
        ORDER BY ts.sort_order
    """), {"since": since})).all()

    items: list[TradingSubblockHealth] = []
    healthy = delayed = stale = never = 0

    for r in rows:
        slug, name, cadence, source_kind = r[0], r[1], r[2], r[3]
        posts_24h = int(r[4] or 0)
        drafts_24h = int(r[5] or 0)
        last_pub = r[6]
        last_created = r[7]
        avg_words = float(r[8]) if r[8] is not None else None

        expected = _CADENCE_MINUTES.get(cadence, 24 * 60)

        if last_created is None:
            mins_since = None
            status = "never_run"
            never += 1
        else:
            delta = now - last_created
            mins_since = int(delta.total_seconds() / 60)
            if mins_since <= expected * 1.5:
                status = "healthy"
                healthy += 1
            elif mins_since <= expected * 3:
                status = "delayed"
                delayed += 1
            else:
                status = "stale"
                stale += 1

        items.append(TradingSubblockHealth(
            slug=slug, name=name, cadence=cadence,
            source_kind=source_kind,
            posts_24h=posts_24h, drafts_24h=drafts_24h,
            last_published_at=last_pub, last_created_at=last_created,
            avg_word_count_24h=avg_words,
            minutes_since_last=mins_since,
            expected_cadence_minutes=expected,
            status=status,
        ))

    return TradingHealthResponse(
        items=items,
        summary={
            "total": len(items),
            "healthy": healthy,
            "delayed": delayed,
            "stale": stale,
            "never_run": never,
        },
        generated_at=now,
    )
'''


def step2_add_endpoint() -> None:
    print("\n[2/5] Add /api/trading/health endpoint")
    src = ROUTER.read_text()
    if "trading_health" in src:
        print("  ↷ health endpoint already present")
        return
    ROUTER.write_text(src.rstrip() + "\n" + HEALTH_ENDPOINT)
    print("  ✓ appended trading_health endpoint")


# ─────────────────────────────────────────────────────────────────────
# 3. api.ts additions
# ─────────────────────────────────────────────────────────────────────

def step3_extend_api_ts() -> None:
    print("\n[3/5] Extend lib/api.ts")

    src = API_TS.read_text()

    if "TradingSubblockHealth" in src:
        print("  ↷ api.ts types already present")
    else:
        types_block = '''

// ---------- Trading health (T.8) ----------

export type TradingSubblockHealth = {
  slug: string;
  name: string;
  cadence: string;
  source_kind: string | null;
  posts_24h: number;
  drafts_24h: number;
  last_published_at: string | null;
  last_created_at: string | null;
  avg_word_count_24h: number | null;
  minutes_since_last: number | null;
  expected_cadence_minutes: number;
  status: "healthy" | "delayed" | "stale" | "never_run";
};

export type TradingHealthResponse = {
  items: TradingSubblockHealth[];
  summary: {
    total: number;
    healthy: number;
    delayed: number;
    stale: number;
    never_run: number;
  };
  generated_at: string;
};
'''
        marker = "export const api = {"
        if marker not in src:
            marker = "export const api:"
            if marker not in src:
                raise SystemExit("❌ api.ts: cannot find 'export const api'")
        src = src.replace(marker, types_block + "\n" + marker, 1)
        API_TS.write_text(src)
        print("  ✓ api.ts: health types added")

    src = API_TS.read_text()
    if "getTradingHealth" in src:
        print("  ↷ api.ts: getTradingHealth already present")
        return

    marker = '  getTradingPost: (postId: string) =>\n    request<TradingPostResponse>(`/api/trading/posts/${postId}`),'
    if marker not in src:
        raise SystemExit("❌ api.ts: getTradingPost marker not found")

    new = marker + '''

  getTradingHealth: () =>
    request<TradingHealthResponse>("/api/trading/health"),'''

    src = src.replace(marker, new, 1)
    API_TS.write_text(src)
    print("  ✓ api.ts: getTradingHealth method added")


# ─────────────────────────────────────────────────────────────────────
# 4. Admin page
# ─────────────────────────────────────────────────────────────────────

ADMIN_PAGE_SRC = '''"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type TradingSubblockHealth } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

function statusBadge(status: TradingSubblockHealth["status"]): string {
  switch (status) {
    case "healthy":    return "bg-green-500/15 text-green-400 border border-green-500/30";
    case "delayed":    return "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30";
    case "stale":      return "bg-red-500/15 text-red-400 border border-red-500/30";
    case "never_run":  return "bg-muted text-muted-foreground border border-border";
    default:           return "bg-muted text-muted-foreground border border-border";
  }
}

function fmtMinutes(mins: number | null): string {
  if (mins == null) return "—";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ${mins % 60}m ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h ago`;
}

function fmtCadence(mins: number): string {
  if (mins < 60) return `${mins} min`;
  if (mins < 60 * 24) return `${mins / 60} hours`;
  return `${mins / (60 * 24)} days`;
}

export default function AdminTradingPage() {
  const q = useQuery({
    queryKey: ["trading-health"],
    queryFn: () => api.getTradingHealth(),
    refetchInterval: 30_000,
  });

  if (q.isLoading) return <p className="text-sm text-muted-foreground">Loading health…</p>;
  if (q.isError || !q.data) return <p className="text-sm text-destructive">Failed to load trading health</p>;

  const { items, summary, generated_at } = q.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Trading — generation health</h1>
        <p className="text-sm text-muted-foreground">
          Snapshot at {new Date(generated_at).toLocaleString()} · refreshes every 30s
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid gap-4 md:grid-cols-5">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Total</CardDescription>
            <CardTitle className="text-3xl">{summary.total}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-green-400">Healthy</CardDescription>
            <CardTitle className="text-3xl text-green-400">{summary.healthy}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-yellow-400">Delayed</CardDescription>
            <CardTitle className="text-3xl text-yellow-400">{summary.delayed}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="text-red-400">Stale</CardDescription>
            <CardTitle className="text-3xl text-red-400">{summary.stale}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Never run</CardDescription>
            <CardTitle className="text-3xl">{summary.never_run}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      {/* Per-subblock table */}
      <Card>
        <CardHeader>
          <CardTitle>Subblocks</CardTitle>
          <CardDescription>
            Healthy = last run within 1.5× cadence · Delayed = 1.5-3× · Stale = &gt;3×
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                  <th className="text-left py-2 pr-3 font-medium">Subblock</th>
                  <th className="text-left py-2 pr-3 font-medium">Cadence</th>
                  <th className="text-right py-2 pr-3 font-medium">24h posts</th>
                  <th className="text-right py-2 pr-3 font-medium">24h drafts</th>
                  <th className="text-right py-2 pr-3 font-medium">Avg words</th>
                  <th className="text-right py-2 pr-3 font-medium">Last run</th>
                  <th className="text-right py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {items.map((s) => (
                  <tr key={s.slug} className="border-b border-border/40 last:border-0">
                    <td className="py-2 pr-3">
                      <Link href={`/trading/${s.slug}`} className="font-medium hover:underline">
                        {s.name}
                      </Link>
                      <div className="text-xs text-muted-foreground font-mono">{s.slug}</div>
                    </td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground">
                      {fmtCadence(s.expected_cadence_minutes)}
                    </td>
                    <td className="py-2 pr-3 text-right font-mono">{s.posts_24h}</td>
                    <td className={`py-2 pr-3 text-right font-mono ${s.drafts_24h > 0 ? "text-amber-400" : ""}`}>
                      {s.drafts_24h}
                    </td>
                    <td className="py-2 pr-3 text-right font-mono">
                      {s.avg_word_count_24h != null ? Math.round(s.avg_word_count_24h) : "—"}
                    </td>
                    <td className="py-2 pr-3 text-right text-xs text-muted-foreground">
                      {fmtMinutes(s.minutes_since_last)}
                    </td>
                    <td className="py-2 text-right">
                      <span className={`rounded-md px-2 py-0.5 text-[11px] ${statusBadge(s.status)}`}>
                        {s.status.replace("_", " ")}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
'''


def step4_write_admin_page() -> None:
    print("\n[4/5] Write /admin/trading page")
    ADMIN_PAGE.parent.mkdir(parents=True, exist_ok=True)
    ADMIN_PAGE.write_text(ADMIN_PAGE_SRC)
    print(f"  ✓ wrote {ADMIN_PAGE.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 5. Sidebar link + TS check
# ─────────────────────────────────────────────────────────────────────

def step5_sidebar_and_typecheck() -> bool:
    print("\n[5/5] Add sidebar link + TypeScript check")

    src = SIDEBAR.read_text()
    if '"/admin/trading"' not in src:
        anchor = '  { href: "/trading", label: "Trading", icon: LineChart },'
        if anchor in src:
            _patch_and_verify(
                SIDEBAR,
                anchor,
                anchor + '\n  { href: "/admin/trading", label: "Gen health", icon: Activity },',
                "add Gen health link",
            )
            # Add Activity to imports if missing
            src2 = SIDEBAR.read_text()
            import re
            m = re.search(r'import \{([^}]+)\} from "lucide-react"', src2)
            if m and "Activity" not in m.group(1):
                new_imports = m.group(1).rstrip() + ", Activity"
                src2 = src2.replace(m.group(0), f'import {{{new_imports}}} from "lucide-react"')
                SIDEBAR.write_text(src2)
                print("  ✓ sidebar.tsx: Activity import added")
        else:
            print("  ⚠ sidebar.tsx: /trading anchor not found")

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

    # Health of API
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout}")
        return False
    print("  ✓ API /health 200")

    # Endpoint registered?
    r = subprocess.run(
        ["curl", "-s", "http://localhost:8000/openapi.json"],
        capture_output=True, text=True,
    )
    if "/api/trading/health" not in r.stdout:
        print("  ❌ /api/trading/health not in OpenAPI")
        return False
    print("  ✓ /api/trading/health registered")
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.8: Trading block observability")
    print("=" * 70)

    step1_extend_schemas()
    step2_add_endpoint()
    step3_extend_api_ts()
    step4_write_admin_page()
    ok_ts = step5_sidebar_and_typecheck()
    ok_api = step6_restart_and_verify()

    print("=" * 70)
    if ok_ts and ok_api:
        print("✅ T.8 complete — trading health dashboard live")
        print()
        print("Open http://localhost:3000/admin/trading")
        sys.exit(0)
    print("❌ T.8 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
