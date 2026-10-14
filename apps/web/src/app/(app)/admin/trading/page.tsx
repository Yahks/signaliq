"use client";

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
