"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type HealthComponent, type HealthStatus } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

function statusBadge(status: string): string {
  switch (status) {
    case "ok":
    case "healthy":
      return "bg-green-500/15 text-green-400 border border-green-500/30";
    case "degraded":
      return "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30";
    case "down":
      return "bg-red-500/15 text-red-400 border border-red-500/30";
    default:
      return "bg-muted text-muted-foreground border border-border";
  }
}

function Component({ name, c }: { name: string; c: HealthComponent }) {
  return (
    <div className="flex items-center justify-between rounded-md border border-border p-3">
      <div>
        <p className="text-sm font-medium capitalize">{name}</p>
        {typeof c.latency_ms === "number" && (
          <p className="text-xs text-muted-foreground">{c.latency_ms.toFixed(2)}ms</p>
        )}
        {typeof c.error === "string" && (
          <p className="text-xs text-destructive line-clamp-1">{c.error}</p>
        )}
        {typeof c.workers === "number" && (
          <p className="text-xs text-muted-foreground">
            {c.workers} worker{c.workers === 1 ? "" : "s"} ·{" "}
            {typeof c.active_tasks === "number" ? c.active_tasks : 0} active ·{" "}
            {typeof c.queue_depth === "number" ? c.queue_depth : 0} queued
          </p>
        )}
      </div>
      <span className={`rounded-md px-2 py-0.5 text-xs ${statusBadge(c.status)}`}>
        {c.status}
      </span>
    </div>
  );
}

export default function AdminHealthPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["health-full"],
    queryFn: () => api.fullHealth(),
    refetchInterval: 15_000,
  });

  if (isLoading || !data) {
    return <p className="text-sm text-muted-foreground">Loading health…</p>;
  }
  if (error) {
    return <p className="text-sm text-destructive">Health check failed</p>;
  }

  const h = data as HealthStatus;
  const activity = h.activity_1h;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">System health</h1>
          <p className="text-sm text-muted-foreground">
            Live snapshot · auto-refreshes every 15s · last checked{" "}
            {new Date(h.checked_at).toLocaleTimeString()}
          </p>
        </div>
        <span className={`rounded-md px-3 py-1 text-sm ${statusBadge(h.status)}`}>
          {h.status}
        </span>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Core infrastructure</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Component name="Postgres" c={h.components.postgres} />
            <Component name="Redis" c={h.components.redis} />
            <Component name="Celery" c={h.components.celery} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">AI providers</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {Object.entries(h.components.providers).map(([name, c]) => (
              <Component key={name} name={name} c={c} />
            ))}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Activity — last hour</CardTitle>
          <CardDescription>AI calls processed by the router</CardDescription>
        </CardHeader>
        <CardContent>
          {activity.error ? (
            <p className="text-sm text-destructive">{activity.error}</p>
          ) : (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <div>
                <p className="text-xs text-muted-foreground">Total calls</p>
                <p className="text-2xl font-semibold">{activity.total_calls_1h ?? 0}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Succeeded</p>
                <p className="text-2xl font-semibold text-green-400">
                  {activity.ok_calls_1h ?? 0}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Errors</p>
                <p className="text-2xl font-semibold text-destructive">
                  {activity.error_calls_1h ?? 0}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Success rate</p>
                <p className="text-2xl font-semibold">
                  {activity.success_rate != null
                    ? `${(activity.success_rate * 100).toFixed(2)}%`
                    : "—"}
                </p>
              </div>
            </div>
          )}
          {activity.avg_latency_ms != null && (
            <p className="mt-3 text-xs text-muted-foreground">
              Average latency: {activity.avg_latency_ms}ms
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
