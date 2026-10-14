"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type DailyBriefResponse, getErrorMessage } from "@/lib/api";
import { useGate } from "@/hooks/useGate";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

export default function BriefsPage() {
  const qc = useQueryClient();
  const briefs = useQuery({
    queryKey: ["briefs"],
    queryFn: () => api.listBriefs(30),
    refetchInterval: 30_000,
  });

  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);
  const gate = useGate();

  const generate = useMutation({
    mutationFn: () => api.generateBrief(24),
    onSuccess: () => {
      setQueued(true);
      setError(null);
      // Poll shortly after to see the result
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["briefs"] });
        setQueued(false);
      }, 30_000);
    },
    onError: (e) => setError(getErrorMessage(e, "Failed")),
  });

  // S.7.6d.2 — move a brief to trash. Reversible; no confirm.
  const deleteBrief = useMutation({
    mutationFn: (id: string) => api.deleteBrief(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["briefs"] });
    },
    onError: (e) => setError(getErrorMessage(e, "Failed to delete brief")),
  });

  const items = briefs.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Daily briefs</h1>
          <p className="text-sm text-muted-foreground">
            {briefs.data?.total ?? 0} briefs · aggregates the last 24h across your chains
          </p>
        </div>
        <Button
          onClick={() => generate.mutate()}
          disabled={generate.isPending || queued || !gate.canGenerate}
          title={gate.tooltip}
        >
          {generate.isPending ? "Generating…" : queued ? "Queued — refreshing in 30s" : "Generate now"}
        </Button>
      </div>

      {gate.outOfTokens && (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">
          You are out of tokens. Generation is paused. Contact your admin to top up.
        </div>
      )}
      {!gate.outOfTokens && gate.noCredentials && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-400">
          You haven&apos;t added an API key yet.{" "}
          <a href="/settings/credentials" className="underline">Add one</a>{" "}
          to enable generation.
        </div>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {queued && (
        <div className="rounded-md border border-green-500/30 bg-green-500/5 p-3 text-sm">
          <p className="text-green-400">✓ Brief generation queued. Will appear below within ~30s.</p>
        </div>
      )}

      {briefs.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No briefs yet. The scheduled job creates one per day at 12:00 UTC.
              You can also generate one now with the button above.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Recent briefs</CardTitle>
            <CardDescription>Newest first</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              {items.map((b: DailyBriefResponse) => (
                <li key={b.id} className="flex items-center">
                  <Link
                    href={`/briefs/${b.id}`}
                    className="block flex-1 py-3 -mx-3 px-3 hover:bg-muted rounded"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-medium">{b.title}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {b.brief_date} · {b.word_count ?? "?"} words · {b.status}
                        </p>
                      </div>
                      <span className="text-xs text-muted-foreground whitespace-nowrap">
                        →
                      </span>
                    </div>
                  </Link>
                  <Button
                    variant="ghost"
                    size="sm"
                    title="Move to trash"
                    disabled={deleteBrief.isPending && deleteBrief.variables === b.id}
                    onClick={() => deleteBrief.mutate(b.id)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
