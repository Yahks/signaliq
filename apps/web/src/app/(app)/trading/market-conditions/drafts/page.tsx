"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from "@/components/ui/card";

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export default function MarketConditionsDraftsPage() {
  const q = useQuery({
    queryKey: ["market-conditions-drafts"],
    queryFn: () => api.marketConditions.listDrafts(),
  });

  const drafts = q.data ?? [];

  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs text-muted-foreground">
          <Link href="/trading/market-conditions" className="hover:underline">
            ← Market conditions
          </Link>
        </p>
        <h1 className="text-2xl font-semibold mt-2">Market-condition drafts</h1>
        <p className="text-sm text-muted-foreground">
          {drafts.length} {drafts.length === 1 ? "draft" : "drafts"}. Each is
          a complete market-conditions block the model drafted from your
          trader process. Open one, edit the specifics, save it as yours.
        </p>
      </div>

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : drafts.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No drafts yet. Click Generate on the Market conditions page to
          draft one.
        </p>
      ) : (
        <div className="space-y-2">
          {drafts.map((d) => (
            <Link
              key={d.id}
              href={`/trading/market-conditions/drafts/${d.id}`}
              className="block"
            >
              <Card className="hover:bg-muted/30 transition-colors">
                <CardContent className="py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium">Market conditions draft</p>
                        {d.reviewed_at ? (
                          <span className="text-[10px] uppercase tracking-wider text-green-400 border border-green-500/30 rounded px-1">
                            accepted
                          </span>
                        ) : (
                          <span className="text-[10px] uppercase tracking-wider text-amber-400 border border-amber-500/30 rounded px-1">
                            draft
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground mt-1 line-clamp-2">
                        {d.preview || "(no preview)"}
                      </p>
                    </div>
                    <p className="text-[10px] text-muted-foreground whitespace-nowrap">
                      {fmtDate(d.created_at)}
                    </p>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
