"""
SignalIQ — Phase 10.9b Stage 2.

Add Extended Markets + Corners panels to the predictions page UI.

  1. apps/web/src/lib/api.ts — extend PredictionResponse type with 13 fields
  2. apps/web/src/app/(app)/predictions/page.tsx — add two new components
     and render them inside PredictionCard

Idempotent. Fails loudly on pattern mismatch.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_TS = ROOT / "apps" / "web" / "src" / "lib" / "api.ts"
PAGE = ROOT / "apps" / "web" / "src" / "app" / "(app)" / "predictions" / "page.tsx"


def _patch(path: Path, old: str, new: str, label: str) -> None:
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
# 1. lib/api.ts — extend PredictionResponse type
# ─────────────────────────────────────────────────────────────────────

def step1_patch_api_ts() -> None:
    # Find the PredictionResponse type and extend it.
    src = API_TS.read_text()

    if "prob_over_1_5" in src:
        print(f"  ↷ {API_TS.name}: PredictionResponse already has extended fields")
        return

    # Locate the type block. We assume it declares prob_over_2_5 / prob_under_2_5
    # and we insert after them.
    if "prob_under_2_5:" not in src:
        raise SystemExit(
            f"\n❌ {API_TS.name}: cannot find 'prob_under_2_5' to anchor the patch.\n"
            f"   Paste the PredictionResponse type block back."
        )

    # Insertion point: after the line containing prob_under_2_5
    lines = src.split("\n")
    out = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and "prob_under_2_5:" in line:
            indent = line[: len(line) - len(line.lstrip())]
            out.extend([
                f"{indent}// Extended markets (Phase 10.9a)",
                f"{indent}prob_over_1_5: number | null;",
                f"{indent}prob_under_1_5: number | null;",
                f"{indent}prob_over_3_5: number | null;",
                f"{indent}prob_under_3_5: number | null;",
                f"{indent}prob_btts_yes: number | null;",
                f"{indent}prob_btts_no: number | null;",
                f"{indent}home_goals_dist: number[] | null;",
                f"{indent}away_goals_dist: number[] | null;",
                f"{indent}top_scorelines: Array<[number, number, number]> | null;",
                f"{indent}// Corners (may be low-confidence; see corners_confidence)",
                f"{indent}expected_home_corners: number | null;",
                f"{indent}expected_away_corners: number | null;",
                f"{indent}expected_total_corners: number | null;",
                f"{indent}corners_confidence: string | null;",
            ])
            inserted = True

    if not inserted:
        raise SystemExit(f"\n❌ {API_TS.name}: insertion anchor not found after scan")

    API_TS.write_text("\n".join(out))
    print(f"  ✓ {API_TS.name}: PredictionResponse extended (13 fields)")


# ─────────────────────────────────────────────────────────────────────
# 2. page.tsx — add ExtendedMarkets + CornersPanel components
# ─────────────────────────────────────────────────────────────────────

EXTENDED_MARKETS_COMPONENT = '''
function MarketBar({
  label, yes, no, yesLabel = "Over", noLabel = "Under",
}: { label: string; yes: number; no: number; yesLabel?: string; noLabel?: string }) {
  const y = Math.round(yes * 100);
  const n = Math.round(no * 100);
  return (
    <div>
      <div className="mb-0.5 flex items-center justify-between text-[10px]">
        <span className="text-muted-foreground uppercase tracking-wider">{label}</span>
        <span className="font-mono text-muted-foreground">
          <span className="text-foreground">{yesLabel} {y}%</span>
          <span className="mx-1 opacity-50">·</span>
          <span className="opacity-70">{noLabel} {n}%</span>
        </span>
      </div>
      <div className="flex h-1 rounded-full overflow-hidden bg-muted/40">
        <div className="bg-emerald-500/70" style={{ width: `${y}%` }} />
        <div className="bg-muted-foreground/30" style={{ width: `${n}%` }} />
      </div>
    </div>
  );
}

function ExtendedMarkets({ p }: { p: PredictionResponse }) {
  const hasAny =
    p.prob_over_1_5 != null ||
    p.prob_over_3_5 != null ||
    p.prob_btts_yes != null;
  if (!hasAny) return null;

  return (
    <div className="rounded-md border border-border/60 bg-muted/10 p-3 space-y-2">
      <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        Extended markets
      </p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2">
        {p.prob_over_1_5 != null && p.prob_under_1_5 != null && (
          <MarketBar label="O/U 1.5" yes={p.prob_over_1_5} no={p.prob_under_1_5} />
        )}
        {p.prob_over_2_5 != null && p.prob_under_2_5 != null && (
          <MarketBar label="O/U 2.5" yes={p.prob_over_2_5} no={p.prob_under_2_5} />
        )}
        {p.prob_over_3_5 != null && p.prob_under_3_5 != null && (
          <MarketBar label="O/U 3.5" yes={p.prob_over_3_5} no={p.prob_under_3_5} />
        )}
        {p.prob_btts_yes != null && p.prob_btts_no != null && (
          <MarketBar
            label="BTTS"
            yes={p.prob_btts_yes}
            no={p.prob_btts_no}
            yesLabel="Yes"
            noLabel="No"
          />
        )}
      </div>
      {p.top_scorelines && p.top_scorelines.length > 0 && (
        <div className="pt-1">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
            Top scorelines
          </p>
          <div className="flex flex-wrap gap-1.5">
            {p.top_scorelines.slice(0, 4).map(([h, a, prob], i) => (
              <span
                key={i}
                className="rounded-md border border-border/60 bg-background/40 px-2 py-0.5 font-mono text-[11px]"
                title={`${(prob * 100).toFixed(1)}% probability`}
              >
                {h}–{a}
                <span className="ml-1 text-muted-foreground">
                  {(prob * 100).toFixed(0)}%
                </span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CornersPanel({ p }: { p: PredictionResponse }) {
  if (p.expected_total_corners == null) return null;
  const low = p.corners_confidence === "low_insufficient_history";
  const prefix = low ? "~" : "";
  return (
    <div className="rounded-md border border-border/60 bg-muted/10 p-3">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          Corners (expected)
        </p>
        {low && (
          <span
            className="text-[10px] text-amber-400/80 cursor-help"
            title="One or both teams have no Premier League corner history — using league average instead."
          >
            ⓘ low confidence
          </span>
        )}
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
        <div>
          <p className="text-muted-foreground text-[10px] uppercase">Home</p>
          <p className="font-mono">{prefix}{p.expected_home_corners?.toFixed(1)}</p>
        </div>
        <div>
          <p className="text-muted-foreground text-[10px] uppercase">Away</p>
          <p className="font-mono">{prefix}{p.expected_away_corners?.toFixed(1)}</p>
        </div>
        <div>
          <p className="text-muted-foreground text-[10px] uppercase">Total</p>
          <p className="font-mono font-semibold">{prefix}{p.expected_total_corners.toFixed(1)}</p>
        </div>
      </div>
    </div>
  );
}

'''


def step2_patch_page() -> None:
    src = PAGE.read_text()

    # 2a. Insert the two new components before PredictionCard
    if "function ExtendedMarkets(" not in src:
        anchor = "function PredictionCard({ p }: { p: PredictionResponse }) {"
        if anchor not in src:
            raise SystemExit(
                f"\n❌ {PAGE.name}: 'PredictionCard' anchor not found"
            )
        src = src.replace(anchor, EXTENDED_MARKETS_COMPONENT + anchor, 1)
        PAGE.write_text(src)
        print(f"  ✓ {PAGE.name}: ExtendedMarkets + CornersPanel components inserted")
    else:
        print(f"  ↷ {PAGE.name}: components already present")
        src = PAGE.read_text()

    # 2b. Render them inside PredictionCard — after the model/market grid
    if "<ExtendedMarkets p={p} />" not in src:
        old = """      <div className="flex items-center justify-between text-xs text-muted-foreground pt-1">
        <BlendBadge mode={p.blend_mode} />"""
        new = """      {/* Phase 10.9b — extended markets + corners */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
        <ExtendedMarkets p={p} />
        <CornersPanel p={p} />
      </div>

      <div className="flex items-center justify-between text-xs text-muted-foreground pt-1">
        <BlendBadge mode={p.blend_mode} />"""
        if old not in src:
            raise SystemExit(
                f"\n❌ {PAGE.name}: render anchor not found "
                f"(BlendBadge row missing or moved)"
            )
        PAGE.write_text(src.replace(old, new, 1))
        print(f"  ✓ {PAGE.name}: ExtendedMarkets + CornersPanel rendered")
    else:
        print(f"  ↷ {PAGE.name}: render already present")


# ─────────────────────────────────────────────────────────────────────
# 3. TypeScript check
# ─────────────────────────────────────────────────────────────────────

def step3_typecheck() -> bool:
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
    print("SignalIQ — Stage 2: extended markets + corners UI")
    print("=" * 70)

    print("\n[1/3] Extend PredictionResponse in lib/api.ts")
    step1_patch_api_ts()

    print("\n[2/3] Patch predictions/page.tsx")
    step2_patch_page()

    print("\n[3/3] TypeScript check")
    ok = step3_typecheck()

    print("=" * 70)
    if ok:
        print("✅ Stage 2 complete — UI components rendered")
        print("   Reload http://localhost:3000/predictions to see them")
        print("=" * 70)
        sys.exit(0)
    else:
        print("❌ Stage 2 has TS errors — see above")
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    main()
