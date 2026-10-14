"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type NotificationResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

function timeAgo(iso: string): string {
  const d = new Date(iso);
  const seconds = Math.floor((Date.now() - d.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function kindBadge(kind: string): string {
  switch (kind) {
    case "chain_update": return "bg-blue-500/15 text-blue-400";
    case "chain_ready":  return "bg-green-500/15 text-green-400";
    case "chain_failed": return "bg-red-500/15 text-red-400";
    default:             return "bg-muted text-muted-foreground";
  }
}

export default function NotificationsPage() {
  const qc = useQueryClient();
  const notifications = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.listNotifications(false),
    refetchInterval: 30_000,
  });

  const markAll = useMutation({
    mutationFn: () => api.markAllNotificationsRead(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const items = notifications.data?.items ?? [];
  const unread = notifications.data?.unread ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Notifications</h1>
          <p className="text-sm text-muted-foreground">
            {notifications.data?.total ?? 0} total · {unread} unread
          </p>
        </div>
        {unread > 0 && (
          <Button
            variant="outline"
            onClick={() => markAll.mutate()}
            disabled={markAll.isPending}
          >
            {markAll.isPending ? "Marking…" : "Mark all read"}
          </Button>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Your updates</CardTitle>
          <CardDescription>
            Events that joined chains you follow appear here.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {notifications.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : items.length === 0 ? (
            <div className="space-y-2 text-sm text-muted-foreground">
              <p>No notifications yet.</p>
              <p>
                Follow a chain (<Link href="/chains" className="underline">browse chains</Link>) and
                you&apos;ll see updates here when new events attach.
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((n: NotificationResponse) => (
                <li key={n.id} className={`py-3 ${n.read_at ? "opacity-60" : ""}`}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={`rounded px-2 py-0.5 text-xs ${kindBadge(n.kind)}`}>
                          {n.kind.replace(/_/g, " ")}
                        </span>
                        {!n.read_at && (
                          <span className="h-2 w-2 rounded-full bg-blue-500" title="Unread" />
                        )}
                      </div>
                      <p className="mt-1.5 font-medium text-sm line-clamp-1">
                        {n.chain_id ? (
                          <Link href={`/chains/${n.chain_id}`} className="hover:underline">
                            {n.title}
                          </Link>
                        ) : (
                          n.title
                        )}
                      </p>
                      {n.body && (
                        <p className="mt-1 text-xs text-muted-foreground whitespace-pre-line line-clamp-3">
                          {n.body}
                        </p>
                      )}
                    </div>
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      {timeAgo(n.created_at)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
