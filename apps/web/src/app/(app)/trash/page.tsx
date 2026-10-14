"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage, type TrashItem } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

const KIND_LABEL: Record<TrashItem["kind"], string> = {
  trading: "Trading",
  news: "News",
  brief: "Brief",
};

const KIND_BADGE: Record<TrashItem["kind"], string> = {
  trading: "bg-blue-500/15 text-blue-400 border border-blue-500/30",
  news: "bg-purple-500/15 text-purple-400 border border-purple-500/30",
  brief: "bg-teal-500/15 text-teal-400 border border-teal-500/30",
};

function timeUntil(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "purging now";
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  if (hours >= 1) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function timeSince(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  if (hours >= 24) return `${Math.floor(hours / 24)}d ago`;
  if (hours >= 1) return `${hours}h ago`;
  return `${minutes}m ago`;
}

export default function TrashPage() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [pendingPurgeId, setPendingPurgeId] = useState<string | null>(null);
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const trash = useQuery({
    queryKey: ["trash"],
    queryFn: () => api.listTrash(),
    refetchInterval: 30_000,
  });

  function armPurge(id: string) {
    setPendingPurgeId(id);
    if (resetTimer.current) clearTimeout(resetTimer.current);
    resetTimer.current = setTimeout(() => setPendingPurgeId(null), 5_000);
  }

  useEffect(() => {
    return () => {
      if (resetTimer.current) clearTimeout(resetTimer.current);
    };
  }, []);

  const restore = useMutation({
    mutationFn: (item: TrashItem) => {
      if (item.kind === "trading") return api.restoreTradingPost(item.id);
      if (item.kind === "news") return api.restoreNewsPost(item.id);
      return api.restoreBrief(item.id);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trash"] });
      // The originating lists will refetch on their own refetchInterval,
      // but invalidate the obvious keys so a browser that is showing
      // them updates immediately.
      qc.invalidateQueries({ queryKey: ["trading-posts"] });
      qc.invalidateQueries({ queryKey: ["briefs"] });
      qc.invalidateQueries({ queryKey: ["chain"] });
      setError(null);
    },
    onError: (e) => setError(getErrorMessage(e, "Restore failed")),
  });

  const purge = useMutation({
    mutationFn: (item: TrashItem) => {
      if (item.kind === "trading") return api.purgeTradingPost(item.id);
      if (item.kind === "news") return api.purgeNewsPost(item.id);
      return api.purgeBrief(item.id);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trash"] });
      setPendingPurgeId(null);
      setError(null);
    },
    onError: (e) => setError(getErrorMessage(e, "Purge failed")),
  });

  const items = trash.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Trash</h1>
        <p className="text-sm text-muted-foreground">
          Deleted items stay here for 2 days, then are removed automatically.
          Restore anything before then.
        </p>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {trash.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              Trash is empty. Items you delete will appear here for 2 days.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>
              {items.length} item{items.length === 1 ? "" : "s"} in trash
            </CardTitle>
            <CardDescription>
              Restore puts an item back where it was. Delete permanently cannot be undone.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              {items.map((item) => {
                const isArmed = pendingPurgeId === item.id;
                return (
                  <li key={`${item.kind}:${item.id}`} className="py-3 space-y-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className={`rounded-md px-2 py-0.5 text-xs ${KIND_BADGE[item.kind]}`}>
                            {KIND_LABEL[item.kind]}
                          </span>
                          <span className="font-medium truncate">{item.title}</span>
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {item.word_count != null && `${item.word_count} words · `}
                          deleted {timeSince(item.deleted_at)} ·
                          auto-purges in {timeUntil(item.purge_at)}
                        </p>
                      </div>
                      <div className="flex gap-2 shrink-0">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => restore.mutate(item)}
                          disabled={restore.isPending}
                        >
                          Restore
                        </Button>
                        {isArmed ? (
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => purge.mutate(item)}
                            disabled={purge.isPending}
                          >
                            {purge.isPending ? "Deleting…" : "Confirm delete"}
                          </Button>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => armPurge(item.id)}
                          >
                            Delete permanently
                          </Button>
                        )}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
