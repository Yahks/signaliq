"use client";

import Link from "next/link";
import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage } from "@/lib/api";
import { useGate } from "@/hooks/useGate";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

// S.8.9.20 — live-status helpers for the movers subblock.

/**
 * Compute the next scheduled movers fire time.
 * Beat schedule: minute="20", hour="14-20", day_of_week="1-5" (UTC).
 * Returns null when we're past Friday's last fire and the next
 * weekday is unknown (Sat/Sun).
 */
function nextMoversRun(now = new Date()): Date | null {
  const y = now.getUTCFullYear();
  const mo = now.getUTCMonth();
  const d = now.getUTCDate();
  const h = now.getUTCHours();
  const mi = now.getUTCMinutes();
  const dow = now.getUTCDay();

  // Advance one day at a time until we land on Mon-Fri and a fire
  // minute that's still ahead of `now`.
  let cursor = new Date(Date.UTC(y, mo, d, 14, 20, 0));
  // If the cursor is already past, move to the next hour's :20
  // within the window, or to the next day's 14:20.
  for (let i = 0; i < 8; i++) {
    const curDow = cursor.getUTCDay();
    if (curDow >= 1 && curDow <= 5 && cursor > now) {
      return cursor;
    }
    // Move to the next :20 in the window.
    const curHour = cursor.getUTCHours();
    if (curHour < 20) {
      cursor = new Date(Date.UTC(
        cursor.getUTCFullYear(),
        cursor.getUTCMonth(),
        cursor.getUTCDate(),
        curHour + 1,
        20,
        0,
      ));
    } else {
      // Advance a day, back to 14:20.
      cursor = new Date(Date.UTC(
        cursor.getUTCFullYear(),
        cursor.getUTCMonth(),
        cursor.getUTCDate() + 1,
        14,
        20,
        0,
      ));
    }
  }
  return null;
}

function timeUntil(d: Date): string {
  const ms = d.getTime() - Date.now();
  if (ms <= 0) return "now";
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `in ${hours}h ${minutes % 60}m`;
}

function formatUtc(d: Date): string {
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm} UTC`;
}

function RecentRunsPanel({ slug }: { slug: string }) {
  const runs = useQuery({
    queryKey: ["generation-runs", slug],
    queryFn: () => api.listGenerationRuns(10, slug),
    refetchInterval: 10_000,
  });

  const items = runs.data?.items ?? [];
  if (!items.length) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Recent generations</CardTitle>
        <CardDescription>Last {items.length} attempts</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2 text-sm font-mono">
          {items.map((r) => {
            const icon =
              r.status === "published" ? "\u2713" :
              r.status === "draft" ? "\u26a0" :
              r.status === "running" ? "\u2026" :
              r.status === "duplicate" ? "\u21bb" :
              "\u2717";
            const cls =
              r.status === "published" ? "text-green-500" :
              r.status === "draft" ? "text-yellow-500" :
              r.status === "running" ? "text-muted-foreground" :
              r.status === "duplicate" ? "text-blue-400" :
              "text-red-500";
            const when = new Date(r.started_at).toLocaleTimeString();
            return (
              <li key={r.id} className="flex gap-3 items-baseline">
                <span className={`${cls} w-4 text-center`}>{icon}</span>
                <span className="text-muted-foreground w-20">{when}</span>
                <span className="font-medium w-32">{r.status}</span>
                {r.error && (
                  <span className="text-muted-foreground truncate flex-1">
                    {r.error.slice(0, 160)}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

export default function TradingSubblockPage() {
  const params = useParams<{ slug: string }>();
  const slug = params.slug;
  const qc = useQueryClient();

  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);

  const subblock = useQuery({
    queryKey: ["trading-subblock", slug],
    queryFn: () => api.getTradingSubblock(slug),
  });

  // S.8.9.17 — for source_kind='studio' subblocks (Studio, Movers),
  // show all statuses (drafts + published) since those articles
  // only publish after human approval. For every other subblock,
  // keep the default (published only).
  const posts = useQuery({
    queryKey: ["trading-posts", slug, subblock.data?.source_kind],
    queryFn: () =>
      api.listTradingPosts(
        slug,
        30,
        0,
        subblock.data?.source_kind === "studio" ? null : undefined,
      ),
    enabled: !!subblock.data,
    refetchInterval: 30_000,
  });

  const gate = useGate();

  const generate = useMutation({
    mutationFn: () => api.generateTradingPost(slug),
    onSuccess: () => {
      setQueued(true);
      setError(null);
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["trading-posts", slug] });
        qc.invalidateQueries({ queryKey: ["trading-subblock", slug] });
        setQueued(false);
      }, 30_000);
    },
    onError: (e) => setError(getErrorMessage(e, "Failed")),
  });

  // S.7.6d.2 — move a post to trash. Reversible; no confirm.
  const deletePost = useMutation({
    mutationFn: (id: string) => api.deleteTradingPost(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trading-posts", slug] });
      qc.invalidateQueries({ queryKey: ["trading-subblock", slug] });
    },
    onError: (e) => setError(getErrorMessage(e, "Failed to delete post")),
  });

  const items = posts.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{subblock.data?.name ?? slug}</h1>
          {subblock.data?.description && (
            <p className="mt-1 text-sm text-muted-foreground max-w-2xl">
              {subblock.data.description}
            </p>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            {posts.data?.total ?? 0} posts · cadence: {subblock.data?.cadence ?? "?"} ·{" "}
            source: {subblock.data?.source_kind ?? "?"}
          </p>

          {/* S.8.9.20 — live indicator, only on the movers subblock. */}
          {slug === "movers" && (() => {
            const next = nextMoversRun();
            const todayUtc = new Date().toISOString().slice(0, 10);
            const todays = (posts.data?.items ?? []).filter(
              (p) => (p.created_at ?? "").slice(0, 10) === todayUtc,
            ).length;
            return (
              <div className="mt-3 flex items-center gap-2 text-xs">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-500 opacity-60" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
                </span>
                <span className="text-green-500 font-medium">Live</span>
                <span className="text-muted-foreground">
                  · {todays} generated today
                  {next && (
                    <> · next at {formatUtc(next)} ({timeUntil(next)})</>
                  )}
                </span>
              </div>
            );
          })()}
        </div>
        {subblock.data?.source_kind === "studio" ? (
          <Link href="/trading/chat">
            <Button variant="outline">Back</Button>
          </Link>
        ) : (
          <Button
            onClick={() => generate.mutate()}
            disabled={generate.isPending || queued || !gate.canGenerate}
            title={gate.tooltip}
          >
            {generate.isPending
              ? "Queuing…"
              : queued
              ? "Queued — refreshing in 30s"
              : "Generate now"}
          </Button>
        )}
      </div>

      {gate.outOfTokens && (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">
          You are out of tokens. Automated and manual generation is paused. Contact your admin to top up.
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
        <div className="rounded-md border border-green-500/30 bg-green-500/5 p-3 text-sm text-green-400">
          ✓ Generation queued. Will appear below within ~30s.
        </div>
      )}

      {posts.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading posts…</p>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No posts yet. Use "Generate now" above, or wait for the scheduled cadence.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Recent posts</CardTitle>
            <CardDescription>Newest first</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              {items.map((p) => (
                <li key={p.id} className="flex items-center">
                  <Link
                    href={`/trading/${slug}/${p.id}`}
                    className="block flex-1 min-w-0 py-3 pr-3 hover:bg-muted rounded"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-medium truncate">{p.title}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {new Date(p.published_at ?? p.created_at).toLocaleString()} ·{" "}
                          {p.word_count ?? "?"} words · {p.symbols.length} symbols
                        </p>
                      </div>
                      <span className="text-xs text-muted-foreground whitespace-nowrap">→</span>
                    </div>
                  </Link>
                  <Button
                    variant="ghost"
                    size="sm"
                    title="Move to trash"
                    disabled={deletePost.isPending && deletePost.variables === p.id}
                    onClick={() => deletePost.mutate(p.id)}
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
