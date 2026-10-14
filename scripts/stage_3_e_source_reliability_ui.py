"""
SignalIQ — Phase 10.9b Stage 3.E.

UI panel: source reliability leaderboard on /performance.

  1. apps/web/src/lib/api.ts — add types + listSourceReliability()
  2. apps/web/src/components/prediction/source-reliability-panel.tsx — new
  3. apps/web/src/app/(app)/performance/page.tsx — render the panel

Idempotent. Fails loudly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_TS = ROOT / "apps" / "web" / "src" / "lib" / "api.ts"
PANEL = ROOT / "apps" / "web" / "src" / "components" / "prediction" / "source-reliability-panel.tsx"
PAGE = ROOT / "apps" / "web" / "src" / "app" / "(app)" / "performance" / "page.tsx"


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


# ─────────────────────────────────────────────────────────────────────
# 1. lib/api.ts — types + method
# ─────────────────────────────────────────────────────────────────────

def step1_patch_api_ts() -> None:
    src = API_TS.read_text()

    # 1a. Add types after the last existing type — anchor on PredictionResponse closing
    if "SourceReliabilityItem" not in src:
        # Find a stable insertion point: append after the whole file's type section.
        # Simplest: insert right before the `export const api =` (or `export const api:`)
        m_anchor = None
        for candidate in ["export const api = {", "export const api:"]:
            idx = src.find(candidate)
            if idx != -1:
                m_anchor = candidate
                break
        if m_anchor is None:
            raise SystemExit(
                f"\n❌ {API_TS.name}: could not find 'export const api' anchor"
            )

        types_block = '''
// ---------- Source reliability (Phase 10.9b) ----------

export type SourceReliabilityItem = {
  slug: string;
  name: string;
  kind: string;
  enabled: boolean;
  predictions_scored: number | null;
  result_accuracy: number | null;
  avg_brier_score: number | null;
  reliability_weight: number | null;
  computed_at: string | null;
  low_sample: boolean;
};

export type SourceReliabilityList = {
  window_days: number;
  items: SourceReliabilityItem[];
};

'''
        src = src.replace(m_anchor, types_block + m_anchor, 1)
        API_TS.write_text(src)
        print(f"  ✓ {API_TS.name}: SourceReliability types added")
    else:
        print(f"  ↷ {API_TS.name}: types already present")

    # 1b. Add api.listSourceReliability method. Anchor on dashboardSummary.
    src = API_TS.read_text()
    if "listSourceReliability" in src:
        print(f"  ↷ {API_TS.name}: listSourceReliability already present")
        return

    marker = '  dashboardSummary: () => request<DashboardSummary>("/api/dashboard/summary"),'
    if marker not in src:
        raise SystemExit(
            f"\n❌ {API_TS.name}: dashboardSummary method not found — "
            f"paste the api object block back."
        )
    new = marker + '''

  // source reliability (Phase 10.9b)
  listSourceReliability: (windowDays: number = 30) =>
    request<SourceReliabilityList>(
      `/api/predictions/sources?window_days=${windowDays}`,
    ),'''
    src = src.replace(marker, new, 1)
    API_TS.write_text(src)
    print(f"  ✓ {API_TS.name}: listSourceReliability method added")


# ─────────────────────────────────────────────────────────────────────
# 2. New component
# ─────────────────────────────────────────────────────────────────────

PANEL_SRC = '''"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type SourceReliabilityItem } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function fmtNum(v: number | null | undefined, d: number = 4): string {
  if (v == null) return "—";
  return v.toFixed(d);
}

function weightBadge(w: number | null): string {
  if (w == null) return "bg-muted text-muted-foreground border border-border";
  if (w >= 0.7) return "bg-green-500/15 text-green-400 border border-green-500/30";
  if (w >= 0.5) return "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30";
  return "bg-red-500/15 text-red-400 border border-red-500/30";
}

function kindLabel(k: string): string {
  switch (k) {
    case "bookmaker":       return "Bookmaker";
    case "internal_model":  return "Internal model";
    case "prediction_site": return "Prediction site";
    case "rss":             return "RSS";
    case "manual":          return "Manual";
    default:                return k;
  }
}

function SourceRow({ s }: { s: SourceReliabilityItem }) {
  const awaiting = s.predictions_scored == null;
  return (
    <tr className="border-b border-border/40 last:border-0">
      <td className="py-2 pr-3">
        <div className="text-sm font-medium">{s.name}</div>
        <div className="text-xs text-muted-foreground font-mono">{s.slug}</div>
      </td>
      <td className="py-2 pr-3 text-xs text-muted-foreground">
        {kindLabel(s.kind)}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-sm">
        {awaiting ? <span className="text-muted-foreground text-xs">awaiting data</span> : s.predictions_scored}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-sm">
        {fmtPct(s.result_accuracy)}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-sm">
        {fmtNum(s.avg_brier_score, 4)}
      </td>
      <td className="py-2 text-right">
        {s.reliability_weight == null ? (
          <span className="text-muted-foreground text-xs">—</span>
        ) : (
          <span className={`inline-block rounded-md px-2 py-0.5 text-xs font-mono ${weightBadge(s.reliability_weight)}`}>
            {s.reliability_weight.toFixed(3)}
            {s.low_sample && <span className="ml-1 text-[10px] opacity-70">low n</span>}
          </span>
        )}
      </td>
    </tr>
  );
}

export function SourceReliabilityPanel() {
  const [windowDays, setWindowDays] = useState<number>(30);

  const q = useQuery({
    queryKey: ["source-reliability", windowDays],
    queryFn: () => api.listSourceReliability(windowDays),
    refetchInterval: 5 * 60_000,  // every 5 min
  });

  const items = q.data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">Source reliability</CardTitle>
            <CardDescription>
              How each external source has actually performed on finished matches.
              Weighted 0.7·accuracy + 0.3·(1 − normalised Brier).
            </CardDescription>
          </div>
          <select
            value={windowDays}
            onChange={(e) => setWindowDays(Number(e.target.value))}
            className="rounded-md border border-border bg-transparent px-2 py-1 text-xs"
          >
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
          </select>
        </div>
      </CardHeader>
      <CardContent>
        {q.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading sources…</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No sources registered yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                  <th className="text-left py-2 pr-3 font-medium">Source</th>
                  <th className="text-left py-2 pr-3 font-medium">Kind</th>
                  <th className="text-right py-2 pr-3 font-medium">Scored</th>
                  <th className="text-right py-2 pr-3 font-medium">Accuracy</th>
                  <th className="text-right py-2 pr-3 font-medium">Brier</th>
                  <th className="text-right py-2 font-medium">Weight</th>
                </tr>
              </thead>
              <tbody>
                {items.map((s) => <SourceRow key={s.slug} s={s} />)}
              </tbody>
            </table>
            {items.every((s) => s.predictions_scored == null) && (
              <p className="mt-3 text-xs text-muted-foreground">
                Reliability will populate once finished matches have external predictions.
                The daily scorer runs at 03:00 UTC.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
'''


def step2_write_panel() -> None:
    PANEL.parent.mkdir(parents=True, exist_ok=True)
    if PANEL.exists():
        print(f"  ↷ {PANEL.name} already exists")
        return
    PANEL.write_text(PANEL_SRC)
    print(f"  ✓ wrote {PANEL.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 3. performance/page.tsx — render panel
# ─────────────────────────────────────────────────────────────────────

def step3_patch_page() -> None:
    src = PAGE.read_text()

    # 3a. Add import
    if "SourceReliabilityPanel" not in src:
        old = 'import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";'
        new = (
            old
            + '\nimport { SourceReliabilityPanel } from "@/components/prediction/source-reliability-panel";'
        )
        if old not in src:
            raise SystemExit(
                f"\n❌ {PAGE.name}: card import anchor not found"
            )
        src = src.replace(old, new, 1)
        PAGE.write_text(src)
        print(f"  ✓ {PAGE.name}: import added")
    else:
        print(f"  ↷ {PAGE.name}: import already present")
        src = PAGE.read_text()

    # 3b. Render panel above the Methodology card
    if "<SourceReliabilityPanel />" in src:
        print(f"  ↷ {PAGE.name}: panel already rendered")
        return

    old = """      {/* Methodology + limitations */}
      <Card>"""
    new = """      {/* Source reliability leaderboard (Phase 10.9b) */}
      <SourceReliabilityPanel />

      {/* Methodology + limitations */}
      <Card>"""
    if old not in src:
        raise SystemExit(
            f"\n❌ {PAGE.name}: methodology anchor not found"
        )
    PAGE.write_text(src.replace(old, new, 1))
    print(f"  ✓ {PAGE.name}: panel rendered")


# ─────────────────────────────────────────────────────────────────────
# 4. TypeScript check
# ─────────────────────────────────────────────────────────────────────

def step4_typecheck() -> bool:
    print("  running tsc --noEmit in apps/web...")
    r = subprocess.run(
        ["npx", "tsc", "--noEmit"],
        cwd=ROOT / "apps" / "web", capture_output=True, text=True,
    )
    if r.returncode != 0:
        print("  ❌ TypeScript errors:")
        for line in r.stdout.splitlines()[:30]:
            print(f"      {line}")
        return False
    print("  ✓ TypeScript clean")
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Stage 3.E: source reliability UI panel")
    print("=" * 70)

    print("\n[1/4] Extend lib/api.ts")
    step1_patch_api_ts()

    print("\n[2/4] Write SourceReliabilityPanel component")
    step2_write_panel()

    print("\n[3/4] Render on /performance")
    step3_patch_page()

    print("\n[4/4] TypeScript check")
    ok = step4_typecheck()

    print("=" * 70)
    if ok:
        print("✅ Stage 3.E complete — open http://localhost:3000/performance")
        print("=" * 70)
        sys.exit(0)
    print("❌ Stage 3.E has TS errors — see above")
    print("=" * 70)
    sys.exit(1)


if __name__ == "__main__":
    main()
