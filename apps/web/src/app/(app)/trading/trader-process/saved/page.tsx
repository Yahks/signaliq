"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from "@/components/ui/card";

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export default function SavedProcessListPage() {
  const q = useQuery({
    queryKey: ["trader-process-history"],
    queryFn: () => api.traderProcess.listHistory(),
  });

  const docs = q.data ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-muted-foreground">
            <Link href="/trading/trader-process" className="hover:underline">
              ← Trader Process
            </Link>
          </p>
          <h1 className="text-2xl font-semibold mt-2">Saved process</h1>
          <p className="text-sm text-muted-foreground">
            {docs.length} {docs.length === 1 ? "document" : "documents"}. Click
            one to view. The composer writes here.
          </p>
        </div>
        <Link href="/trading/trader-process">
          <Button>Compose new</Button>
        </Link>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">What this is</CardTitle>
          <CardDescription>
            Your saved process documents. Accepting a draft or saving from
            the composer writes here. Only one live document per org — a
            new save replaces the previous.
          </CardDescription>
        </CardHeader>
      </Card>

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : docs.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing saved yet. Write one on the{" "}
          <Link
            href="/trading/trader-process"
            className="underline hover:text-foreground"
          >
            Trader Process
          </Link>{" "}
          page.
        </p>
      ) : (
        <div className="space-y-2">
          {docs.map((d) => (
            <Link
              key={d.id}
              href={`/trading/trader-process/saved/${d.id}`}
              className="block"
            >
              <Card className="hover:bg-muted/30 transition-colors">
                <CardContent className="py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium">
                          Saved process
                        </p>
                        <span className="text-[10px] uppercase tracking-wider text-green-400 border border-green-500/30 rounded px-1">
                          live
                        </span>
                      </div>
                      <p className="text-sm text-muted-foreground mt-1 line-clamp-2">
                        {d.preview || "(no preview)"}
                      </p>
                    </div>
                    <p className="text-[10px] text-muted-foreground whitespace-nowrap">
                      {fmtDate(d.updated_at)}
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
