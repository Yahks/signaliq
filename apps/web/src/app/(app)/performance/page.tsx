"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SourceReliabilityPanel } from "@/components/prediction/source-reliability-panel";

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function num(v: number | null | undefined, decimals: number = 4): string {
  if (v == null) return "—";
  return v.toFixed(decimals);
}

export default function PerformancePage() {
  const perf = useQuery({
    queryKey: ["performance"],
    queryFn: api.performanceSummary,
    refetchInterval: 60_000,
  });

  if (perf.isLoading || !perf.data) {
    return <p className="text-sm text-muted-foreground">Loading performance…</p>;
  }

  const p = perf.data;
  const accuracy = p.result_accuracy;
  const alwaysHome = p.baseline_always_home;
  const baselineRandom = p.baseline_random;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Model performance</h1>
        <p className="text-sm text-muted-foreground">
          Public, honest accuracy tracking · updated hourly · model: {p.model_version}
        </p>
      </div>

      {/* Headline accuracy */}
      <Card>
        <CardHeader>
          <CardTitle>W/D/L accuracy</CardTitle>
          <CardDescription>
            Compared to three reference baselines
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-4 gap-4 text-center">
            <div>
              <p className="text-xs text-muted-foreground">Our model</p>
              <p className="text-3xl font-semibold text-green-400">{pct(accuracy)}</p>
              <p className="text-xs text-muted-foreground mt-1">
                {p.predictions_scored} scored
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Random guess</p>
              <p className="text-3xl font-semibold">{pct(baselineRandom)}</p>
              <p className="text-xs text-muted-foreground mt-1">theoretical</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Always home</p>
              <p className="text-3xl font-semibold">{pct(alwaysHome)}</p>
              <p className="text-xs text-muted-foreground mt-1">learned baseline</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Bookmaker range</p>
              <p className="text-3xl font-semibold">52-55%</p>
              <p className="text-xs text-muted-foreground mt-1">industry</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Secondary metrics */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Calibration</CardTitle>
            <CardDescription>How well probabilities match reality</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Avg Brier score</span>
              <span className="font-mono">{num(p.avg_brier_score)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Avg log loss</span>
              <span className="font-mono">{num(p.avg_log_loss)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Over/Under 2.5</span>
              <span className="font-mono">{pct(p.over_under_accuracy)}</span>
            </div>
            <p className="pt-2 text-xs text-muted-foreground">
              Brier score 0 = perfect. 2.0 = worst possible (opposite of truth).
              Typical bookmaker range 0.55-0.60.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Rolling windows</CardTitle>
            <CardDescription>Recent form of the model</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div>
              <p className="text-xs text-muted-foreground mb-1">Last 7 days</p>
              {p.last_7_days ? (
                <p>
                  <span className="font-mono">{p.last_7_days.scored}</span> matches ·{" "}
                  <span className="font-mono">{pct(p.last_7_days.result_accuracy)}</span> accuracy
                </p>
              ) : (
                <p className="text-muted-foreground">No data yet</p>
              )}
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Last 30 days</p>
              {p.last_30_days ? (
                <p>
                  <span className="font-mono">{p.last_30_days.scored}</span> matches ·{" "}
                  <span className="font-mono">{pct(p.last_30_days.result_accuracy)}</span> accuracy
                </p>
              ) : (
                <p className="text-muted-foreground">No data yet</p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Source reliability leaderboard (Phase 10.9b) */}
      <SourceReliabilityPanel />

      {/* Methodology + limitations */}
      <Card>
        <CardHeader>
          <CardTitle>Methodology & limitations</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            <strong className="text-foreground">Model:</strong> Dixon-Coles Poisson
            with time-decay weighting and low-score correction. Fit via maximum
            likelihood on historical match results from football-data.org.
          </p>
          <p>
            <strong className="text-foreground">Training data:</strong> All completed
            matches in the training window. Model refits periodically as new results
            arrive.
          </p>
          <p>
            <strong className="text-foreground">Scoring:</strong> Every published
            prediction is scored against the actual result. Predictions are never
            edited or removed. Failed predictions count against the accuracy.
          </p>
          <p>
            <strong className="text-foreground">Known limitations:</strong>
          </p>
          <ul className="list-disc pl-6 space-y-1">
            <li>Does not account for injuries, suspensions, or lineup changes</li>
            <li>Does not account for managerial changes mid-season</li>
            <li>
              Over/Under 2.5 accuracy has been below expectation — the model's
              goal-distribution assumptions may need calibration
            </li>
            <li>
              Requires both teams to be in the training set; promoted teams
              with no historical data cannot be predicted
            </li>
            <li>All metrics are statistical; individual match outcomes are uncertain</li>
          </ul>
          <p className="pt-2">
            <strong className="text-foreground">This is statistical analysis, not
            betting advice.</strong> Past accuracy does not guarantee future results.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
