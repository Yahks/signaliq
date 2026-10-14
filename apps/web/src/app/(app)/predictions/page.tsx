"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type PredictionResponse } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";

function confidenceBadge(c: string): string {
  switch (c) {
    case "high":   return "bg-green-500/15 text-green-400 border border-green-500/30";
    case "medium": return "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30";
    case "low":    return "bg-muted text-muted-foreground border border-border";
    default:       return "bg-muted text-muted-foreground border border-border";
  }
}

function BlendBadge({ mode }: { mode: string | null }) {
  if (mode === "50_50") {
    return (
      <span className="rounded-md bg-blue-500/15 text-blue-400 border border-blue-500/30 px-2 py-0.5 text-xs">
        50/50 model + market
      </span>
    );
  }
  if (mode === "model_only") {
    return (
      <span className="rounded-md bg-muted text-muted-foreground border border-border px-2 py-0.5 text-xs">
        model only
      </span>
    );
  }
  return null;
}

function ProbBar({ label, home, draw, away, muted }: {
  label: string;
  home: number;
  draw: number;
  away: number;
  muted?: boolean;
}) {
  const h = Math.round(home * 100);
  const d = Math.round(draw * 100);
  const a = Math.round(away * 100);
  const op = muted ? "opacity-60" : "";
  return (
    <div className={op}>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-muted-foreground uppercase tracking-wider">{label}</span>
        <span className="font-mono">
          <span className="text-green-400">{h}</span>
          <span className="text-muted-foreground"> / </span>
          <span className="text-yellow-400">{d}</span>
          <span className="text-muted-foreground"> / </span>
          <span className="text-red-400">{a}</span>
        </span>
      </div>
      <div className="flex h-1.5 rounded-full overflow-hidden">
        <div className="bg-green-500/70" style={{ width: `${h}%` }} />
        <div className="bg-yellow-500/70" style={{ width: `${d}%` }} />
        <div className="bg-red-500/70" style={{ width: `${a}%` }} />
      </div>
    </div>
  );
}


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

function PredictionCard({ p }: { p: PredictionResponse }) {
  const kickoff = new Date(p.kickoff_at);
  const isFinished = p.match_status === "finished";

  return (
    <div className="rounded-md border border-border p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-muted-foreground">
            {p.competition} · {kickoff.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}{" "}
            {kickoff.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
          </p>
          <p className="mt-0.5 text-sm font-medium">
            {p.home_team} <span className="text-muted-foreground">vs</span> {p.away_team}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <span className={`rounded-md px-2 py-0.5 text-xs ${confidenceBadge(p.confidence)}`}>
            {p.confidence}
          </span>
          {isFinished && p.actual_home_goals != null && (
            <span className="text-xs text-muted-foreground">
              FT: {p.actual_home_goals}–{p.actual_away_goals}
              {p.result_correct === true && " ✅"}
              {p.result_correct === false && " ❌"}
            </span>
          )}
        </div>
      </div>

      {/* The blend — the main output */}
      <ProbBar
        label="Blend (our pick)"
        home={p.prob_home_win}
        draw={p.prob_draw}
        away={p.prob_away_win}
      />

      {/* Model + Market side-by-side */}
      <div className="grid grid-cols-2 gap-3 pt-1">
        {p.model_probs && (
          <ProbBar
            label="Model"
            home={p.model_probs.home ?? 0}
            draw={p.model_probs.draw ?? 0}
            away={p.model_probs.away ?? 0}
            muted
          />
        )}
        {p.market_probs && (
          <ProbBar
            label={`Market (${p.market_probs.bookmakers ?? 0} books)`}
            home={p.market_probs.home ?? 0}
            draw={p.market_probs.draw ?? 0}
            away={p.market_probs.away ?? 0}
            muted
          />
        )}
      </div>

      {/* Phase 10.9b — extended markets + corners */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
        <ExtendedMarkets p={p} />
        <CornersPanel p={p} />
      </div>

      <div className="flex items-center justify-between text-xs text-muted-foreground pt-1">
        <BlendBadge mode={p.blend_mode} />
        {p.prob_over_2_5 != null && (
          <span>
            O2.5: <span className="font-mono">{(p.prob_over_2_5 * 100).toFixed(0)}%</span>
          </span>
        )}
        {p.fair_odds_home != null && (
          <span title="Fair odds = 1 / probability">
            odds: <span className="font-mono">{p.fair_odds_home.toFixed(2)}</span>
            {" / "}
            <span className="font-mono">{p.fair_odds_draw?.toFixed(2)}</span>
            {" / "}
            <span className="font-mono">{p.fair_odds_away?.toFixed(2)}</span>
          </span>
        )}
      </div>
    </div>
  );
}

export default function PredictionsPage() {
  const [competition, setCompetition] = useState<string>("");

  const query = useQuery({
    queryKey: ["predictions-upcoming", competition],
    queryFn: () => api.listUpcomingPredictions(competition || undefined, 50),
    refetchInterval: 60_000,
  });

  const items = query.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="rounded-md border border-yellow-500/30 bg-yellow-500/5 p-3 text-xs text-yellow-400/90">
        <strong>Statistical analysis, not betting advice.</strong>{" "}
        Probabilities blend our Dixon-Coles model (50%) with the bookmaker consensus (50%),
        empirically validated at +1.05pp above market alone.{" "}
        <a href="/performance" className="underline">See public accuracy →</a>
      </div>

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Upcoming predictions</h1>
          <p className="text-sm text-muted-foreground">
            {items.length} fixtures · blended model + market
          </p>
        </div>
        <select
          value={competition}
          onChange={(e) => setCompetition(e.target.value)}
          className="rounded-md border border-border bg-transparent px-3 py-2 text-sm"
        >
          <option value="">All competitions</option>
          <option value="EPL">Premier League</option>
        </select>
      </div>

      {query.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No published predictions yet.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {items.map((p) => <PredictionCard key={p.id} p={p} />)}
        </div>
      )}
    </div>
  );
}
