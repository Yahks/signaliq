"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type TradingSubblockResponse } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

function categoryBadge(cat: string | null): string {
  switch (cat) {
    case "market":    return "bg-blue-500/15 text-blue-400 border border-blue-500/30";
    case "education": return "bg-purple-500/15 text-purple-400 border border-purple-500/30";
    case "macro":     return "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30";
    case "tools":     return "bg-cyan-500/15 text-cyan-400 border border-cyan-500/30";
    default:          return "bg-muted text-muted-foreground border border-border";
  }
}

function SubblockCard({ s }: { s: TradingSubblockResponse }) {
  const accent = s.theme?.color || "#6366f1";
  const latest = s.latest_post_at
    ? new Date(s.latest_post_at).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      })
    : null;

  return (
    <Link href={`/trading/${s.slug}`}>
      <Card className="hover:bg-muted/30 transition-colors h-full">
        <div className="h-1 rounded-t-lg" style={{ background: accent }} />
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-2">
            <CardTitle className="text-base">{s.name}</CardTitle>
            {s.category && (
              <span className={`shrink-0 rounded-md px-2 py-0.5 text-[10px] uppercase tracking-wider ${categoryBadge(s.category)}`}>
                {s.category}
              </span>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {s.description && (
            <p className="text-xs text-muted-foreground line-clamp-2">
              {s.description}
            </p>
          )}
          <div className="flex items-center justify-between pt-1 text-xs text-muted-foreground">
            <span>
              <span className="font-mono text-foreground">{s.post_count}</span> post{s.post_count === 1 ? "" : "s"}
            </span>
            {latest ? (
              <span className="whitespace-nowrap">{latest}</span>
            ) : (
              <span className="italic opacity-70">no posts yet</span>
            )}
          </div>
          <div className="flex items-center justify-between text-[10px] text-muted-foreground pt-1">
            <span>{s.cadence.replace("_", " ")}</span>
            <span className="font-mono">{s.source_kind}</span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

export default function TradingHubPage() {
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
  const topSymbols = symbols.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Trading</h1>
        <p className="text-sm text-muted-foreground">
          {total} subblocks · {items.reduce((n, s) => n + s.post_count, 0)} published posts
        </p>
      </div>

      {topSymbols.length > 0 && (
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
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No subblocks configured.</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {items.map((s) => <SubblockCard key={s.id} s={s} />)}
        </div>
      )}
    </div>
  );
}
