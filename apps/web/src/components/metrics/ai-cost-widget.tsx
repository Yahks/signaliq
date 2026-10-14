"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type AiCostSummary } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

function barColor(pct: number, alertAt: number): string {
  if (pct >= 100) return "bg-red-500";
  if (pct >= alertAt) return "bg-yellow-500";
  return "bg-green-500";
}

export function AiCostWidget() {
  const { data, isLoading } = useQuery({
    queryKey: ["ai-cost"],
    queryFn: () => api.aiCostSummary(),
    refetchInterval: 30_000,
  });

  if (isLoading || !data) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">AI usage today</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">Loading…</p></CardContent>
      </Card>
    );
  }

  const c = data as AiCostSummary;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">AI usage today</CardTitle>
        <CardDescription>
          {c.calls} call{c.calls === 1 ? "" : "s"} · resets midnight UTC
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Cost meter */}
        <div>
          <div className="mb-1 flex items-center justify-between text-xs">
            <span>Cost</span>
            <span className="text-muted-foreground">
              ${c.cost_usd.toFixed(4)} / ${c.cost_cap_usd.toFixed(2)}
            </span>
          </div>
          <div className="h-2 rounded-full bg-muted overflow-hidden">
            <div
              className={`h-full ${barColor(c.cost_pct, c.alert_at_percent)}`}
              style={{ width: `${Math.min(c.cost_pct, 100)}%` }}
            />
          </div>
        </div>

        {/* Token meter */}
        <div>
          <div className="mb-1 flex items-center justify-between text-xs">
            <span>Tokens</span>
            <span className="text-muted-foreground">
              {c.tokens_total.toLocaleString()} / {c.token_cap.toLocaleString()}
            </span>
          </div>
          <div className="h-2 rounded-full bg-muted overflow-hidden">
            <div
              className={`h-full ${barColor(c.token_pct, c.alert_at_percent)}`}
              style={{ width: `${Math.min(c.token_pct, 100)}%` }}
            />
          </div>
        </div>

        {!c.enabled && (
          <p className="rounded-md border border-yellow-500/30 bg-yellow-500/5 p-2 text-xs text-yellow-400">
            Budget tracking disabled for this org
          </p>
        )}
      </CardContent>
    </Card>
  );
}
