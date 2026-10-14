"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type ClusterResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function sentimentColor(s: number | null): string {
  if (s == null) return "text-muted-foreground";
  if (s > 0.2) return "text-green-400";
  if (s < -0.2) return "text-red-400";
  return "text-yellow-400";
}

export default function ClustersPage() {
  const clusters = useQuery({ queryKey: ["clusters"], queryFn: () => api.listClusters(100) });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Clusters</h1>
        <p className="text-sm text-muted-foreground">
          {clusters.data?.length ?? 0} semantic groups discovered from your records
        </p>
      </div>

      {clusters.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : clusters.data?.length === 0 ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No clusters yet. Clustering runs after enough records are enriched and embedded.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {clusters.data?.map((c: ClusterResponse) => (
            <Link key={c.id} href={`/clusters/${c.id}`}>
              <Card className="hover:bg-muted/30 transition cursor-pointer h-full">
                <CardHeader>
                  <CardTitle className="text-base line-clamp-2">{c.title}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {c.summary && (
                    <p className="text-muted-foreground line-clamp-2 text-xs">{c.summary}</p>
                  )}
                  <div className="flex items-center gap-3 text-xs">
                    <span className="rounded bg-muted px-2 py-0.5">{c.volume} records</span>
                    {c.sentiment_avg != null && (
                      <span className={sentimentColor(c.sentiment_avg)}>
                        sentiment {c.sentiment_avg.toFixed(2)}
                      </span>
                    )}
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
