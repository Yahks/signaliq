"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function SymbolsIndexPage() {
  const router = useRouter();
  const [query, setQuery] = useState("");

  const q = useQuery({
    queryKey: ["symbols-index"],
    queryFn: () => api.listSymbols(120),
    refetchInterval: 60_000,
  });

  const items = q.data?.items ?? [];

  function search(e: React.FormEvent) {
    e.preventDefault();
    const t = query.trim().toUpperCase();
    if (t) router.push(`/trading/symbols/${t}`);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Symbols</h1>
          <p className="text-sm text-muted-foreground">
            {items.length} tickers mentioned across the trading block
          </p>
        </div>
        <Link href="/trading" className="text-sm text-blue-400 hover:underline">
          ← Back to Trading
        </Link>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Look up a ticker</CardTitle>
          <CardDescription>
            Type any ticker to see every post mentioning it
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={search} className="flex gap-2 max-w-md">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. NVDA"
              className="flex-1 rounded-md border border-border bg-transparent px-3 py-2 text-sm font-mono uppercase"
              autoFocus
            />
            <Button type="submit" disabled={!query.trim()}>Open</Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Most mentioned tickers</CardTitle>
          <CardDescription>By number of posts across all subblocks</CardDescription>
        </CardHeader>
        <CardContent>
          {q.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No tickers indexed yet.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {items.map((s) => (
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
          )}
        </CardContent>
      </Card>
    </div>
  );
}
