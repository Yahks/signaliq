"use client";

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
