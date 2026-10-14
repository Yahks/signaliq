"use client";

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
