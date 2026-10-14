"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type PainPointResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function severityBar(s: number | null) {
  const pct = Math.round((s ?? 0) * 100);
  const color = pct > 60 ? "bg-red-500" : pct > 30 ? "bg-yellow-500" : "bg-muted-foreground/60";
  return { pct, color };
}

export default function PainPointsPage() {
  const pp = useQuery({ queryKey: ["pain-points"], queryFn: () => api.listPainPoints(50) });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Pain points</h1>
        <p className="text-sm text-muted-foreground">
          Recurring problems extracted from your records, ranked by severity
        </p>
      </div>

      <Card>
        <CardHeader><CardTitle>Ranked by severity</CardTitle></CardHeader>
        <CardContent>
          {pp.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : pp.data?.length === 0 ? (
            <p className="text-sm text-muted-foreground">No pain points yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {pp.data?.map((p: PainPointResponse) => {
                const { pct, color } = severityBar(p.severity);
                return (
                  <li key={p.id}>
                    <Link href={`/pain-points/${p.id}`} className="block py-3 hover:bg-muted -mx-3 px-3 rounded">
                      <div className="flex items-center justify-between gap-4">
                        <p className="font-medium">{p.title}</p>
                        <span className="text-xs font-mono text-muted-foreground">
                          {p.severity?.toFixed(3)}
                        </span>
                      </div>
                      <div className="mt-2 h-1.5 rounded-full bg-muted overflow-hidden">
                        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
                      </div>
                      {p.description && (
                        <p className="mt-1 text-xs text-muted-foreground">{p.description}</p>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
