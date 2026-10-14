"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function ClusterDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const cluster = useQuery({ queryKey: ["cluster", params.id], queryFn: () => api.getCluster(params.id) });

  if (cluster.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (cluster.isError || !cluster.data) return <p className="text-sm text-destructive">Cluster not found</p>;

  const c = cluster.data;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{c.title}</h1>
          {c.summary && <p className="text-sm text-muted-foreground mt-1">{c.summary}</p>}
          <div className="mt-2 flex items-center gap-3 text-xs">
            <span className="rounded bg-muted px-2 py-0.5">{c.volume} records</span>
            {c.sentiment_avg != null && (
              <span className="text-muted-foreground">sentiment {c.sentiment_avg.toFixed(2)}</span>
            )}
          </div>
        </div>
        <Button variant="outline" onClick={() => router.back()}>Back</Button>
      </div>

      <Card>
        <CardHeader><CardTitle>Sample records ({c.sample_records.length})</CardTitle></CardHeader>
        <CardContent>
          <ul className="divide-y divide-border">
            {c.sample_records.map((r) => (
              <li key={r.record_id} className="py-3">
                <Link
                  href={`/records/${r.record_id}`}
                  className="block hover:bg-muted -mx-3 px-3 py-1 rounded"
                >
                  <p className="text-sm line-clamp-3">{r.normalized_text}</p>
                </Link>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
