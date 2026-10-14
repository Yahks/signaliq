"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useGate } from "@/hooks/useGate";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n.toLocaleString()}`;
}

function fmtUSD(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n > 0) return `$${n.toFixed(4)}`;
  return "$0.00";
}

function sentimentColor(label: string): string {
  switch (label) {
    case "positive": return "bg-green-500/70";
    case "negative": return "bg-red-500/70";
    case "mixed":    return "bg-yellow-500/70";
    default:         return "bg-muted-foreground/40";
  }
}

function severityColor(s: number | null): string {
  if (s == null) return "bg-muted-foreground/40";
  if (s > 0.6) return "bg-red-500";
  if (s > 0.3) return "bg-yellow-500";
  return "bg-muted-foreground/60";
}

export default function DashboardPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: () => api.dashboardSummary(),
    refetchInterval: 30_000,
  });

  const gate = useGate();
  const tokens = useQuery({
    queryKey: ["token-balance"],
    queryFn: () => api.getTokenBalance(),
    refetchInterval: 30_000,
  });

  const blocks = useQuery({
    queryKey: ["block-settings"],
    queryFn: () => api.listBlockSettings(),
    refetchInterval: 60_000,
  });

  const toggleBlock = useMutation({
    mutationFn: ({ slug, enabled }: { slug: string; enabled: boolean }) =>
      api.updateBlockSettings(slug, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["block-settings"] }),
  });

  if (isLoading || !data) {
    return <p className="text-sm text-muted-foreground">Loading dashboard…</p>;
  }

  const c = data.counts;
  const totalSent = data.sentiment.reduce((s, x) => s + x.count, 0) || 1;

  return (
    <div className="space-y-6">
      {gate.noCredentials && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-400">
          Welcome to SignalIQ. <a href="/settings/credentials" className="underline">Add your first API key</a> to enable generation.
        </div>
      )}
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Overview of your intelligence pipeline</p>
      </div>

      {/* Empty state for brand-new orgs */}
      {c.records === 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Getting started</CardTitle>
            <CardDescription>
              Your workspace is empty. Here's how to see content appear here.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              <Link href="/ingest/new" className="text-blue-400 hover:underline">
                Upload a CSV or JSON file →
              </Link>{" "}
              to populate records, entities, and topics.
            </p>
            <p>
              <Link href="/trading" className="text-blue-400 hover:underline">
                Visit the Trading block →
              </Link>{" "}
              to generate market analysis, fundamentals, and signals.
            </p>
            <p className="text-xs text-muted-foreground pt-2">
              You have{" "}
              <span className="text-foreground font-mono">
                {fmtUSD(tokens.data?.balance_usd ?? ((tokens.data?.balance ?? 0) / 1_000_000))}
              </span>{" "}
              in usage credit (
              <span className="font-mono">
                {(tokens.data?.balance ?? 0).toLocaleString()}
              </span>{" "}
              tokens).
            </p>
          </CardContent>
        </Card>
      )}

      {/* Counts */}
      <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-6">
        <Stat label="Records" value={c.records} />
        <Stat label="Enriched" value={c.enriched} />
        <Stat label="Entities" value={c.entities} />
        <Stat label="Topics" value={c.topics} />
        <Stat label="Clusters" value={c.clusters} />
        <Stat label="Pain points" value={c.pain_points} />
      </div>

      {/* Your tokens + Sentiment */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Your tokens</CardTitle>
          </CardHeader>
          <CardContent>
            {tokens.isLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : tokens.data ? (
              <div className="space-y-3">
                <div>
                  <p className="text-xs text-muted-foreground">Balance</p>
                  <p className="text-3xl font-semibold text-green-400">
                    {fmtUSD(tokens.data.balance_usd)}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1 font-mono">
                    {tokens.data.balance.toLocaleString()} tokens
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-3 pt-2 border-t border-border">
                  <div>
                    <p className="text-xs text-muted-foreground">Consumed today</p>
                    <p className="text-lg font-semibold">
                      {fmtUSD(tokens.data.consumed_today_usd)}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {tokens.data.calls_today} call{tokens.data.calls_today === 1 ? "" : "s"}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Lifetime</p>
                    <p className="text-lg font-semibold">
                      {fmtUSD(tokens.data.balance_usd + tokens.data.consumed_today_usd)}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {fmtTokens(tokens.data.lifetime_granted)} granted
                    </p>
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No data.</p>
            )}
          </CardContent>
        </Card>
        <div className="lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle>Sentiment breakdown</CardTitle>
              <CardDescription>{totalSent} enriched records</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {data.sentiment.map((s) => {
                const pct = Math.round((s.count / totalSent) * 100);
                return (
                  <div key={s.label}>
                    <div className="mb-1 flex items-center justify-between text-xs">
                      <span className="capitalize">{s.label}</span>
                      <span className="text-muted-foreground">{s.count} ({pct}%)</span>
                    </div>
                    <div className="h-2 rounded-full bg-muted overflow-hidden">
                      <div className={`h-full ${sentimentColor(s.label)}`} style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Block automation */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Block automation</CardTitle>
          <CardDescription>
            Turn off auto-generation to freeze a block. Existing content stays visible.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {blocks.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (blocks.data?.items ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">No blocks configured.</p>
          ) : (
            <div className="space-y-3">
              {(blocks.data?.items ?? []).map((b) => (
                <div key={b.slug} className="flex items-center justify-between gap-3">
                  <div>
                    <p className="font-medium">{b.name}</p>
                    <p className="text-xs text-muted-foreground font-mono">{b.slug}</p>
                  </div>
                  <label className="inline-flex items-center gap-2 cursor-pointer">
                    <span className={`text-xs ${b.auto_enabled ? "text-green-400" : "text-muted-foreground"}`}>
                      {b.auto_enabled ? "auto on" : "auto off"}
                    </span>
                    <input
                      type="checkbox"
                      checked={b.auto_enabled}
                      onChange={(e) => toggleBlock.mutate({ slug: b.slug, enabled: e.target.checked })}
                      className="h-4 w-8 cursor-pointer rounded-full appearance-none bg-muted checked:bg-green-500/70 transition-colors relative before:content-[''] before:absolute before:top-0.5 before:left-0.5 before:h-3 before:w-3 before:rounded-full before:bg-white before:transition-transform checked:before:translate-x-4"
                    />
                  </label>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Entities + Topics */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Top entities</CardTitle>
            <CardDescription>Ranked by mentions</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5 text-sm">
              {data.top_entities.slice(0, 8).map((e) => (
                <li key={e.name} className="flex items-center justify-between">
                  <span className="truncate">
                    {e.name}
                    <span className="ml-2 text-xs text-muted-foreground">{e.type}</span>
                  </span>
                  <span className="text-xs text-muted-foreground">{e.mentions}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Top topics</CardTitle>
            <CardDescription>Across {c.topics} discovered topics</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {data.top_topics.slice(0, 12).map((t) => (
                <span key={t.name} className="rounded-md border border-border bg-muted/50 px-2 py-1 text-xs">
                  {t.name}
                  <span className="ml-2 text-muted-foreground">{t.records}</span>
                </span>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Pain points */}
      {data.top_pain_points.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Top pain points</CardTitle>
            <CardDescription>Ranked by severity</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.top_pain_points.map((p) => (
              <Link key={p.id} href={`/pain-points/${p.id}`} className="block">
                <div className="flex items-center justify-between text-sm">
                  <span className="capitalize">{p.title}</span>
                  <span className="font-mono text-xs text-muted-foreground">{p.severity?.toFixed(3)}</span>
                </div>
                <div className="mt-1 h-1.5 rounded-full bg-muted overflow-hidden">
                  <div className={`h-full ${severityColor(p.severity)}`} style={{ width: `${Math.round((p.severity ?? 0) * 100)}%` }} />
                </div>
              </Link>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Recent jobs */}
      <Card>
        <CardHeader><CardTitle>Recent ingestion jobs</CardTitle></CardHeader>
        <CardContent>
          <ul className="space-y-2 text-sm">
            {data.recent_jobs.map((j) => (
              <li key={j.id} className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                <Link href={`/ingest/${j.id}`} className="font-mono text-xs hover:underline">
                  {j.id.slice(0, 8)}
                </Link>
                <span className={
                  j.status === "succeeded" ? "text-green-500" :
                  j.status === "failed" ? "text-destructive" : "text-muted-foreground"
                }>{j.status}</span>
                <span className="text-muted-foreground">{j.processed} records</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-3xl">{value}</CardTitle>
      </CardHeader>
    </Card>
  );
}
