"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function EntityDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const entity = useQuery({ queryKey: ["entity", params.id], queryFn: () => api.getEntity(params.id) });

  if (entity.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (entity.isError || !entity.data) return <p className="text-sm text-destructive">Entity not found</p>;

  const e = entity.data;
  const avg = e.avg_sentiment;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{e.canonical_name}</h1>
          <p className="text-sm text-muted-foreground capitalize">{e.type} · {e.mention_count} mentions</p>
        </div>
        <Button variant="outline" onClick={() => router.back()}>Back</Button>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Average sentiment</CardTitle></CardHeader>
          <CardContent>
            {avg == null ? (
              <p className="text-sm text-muted-foreground">No sentiment data</p>
            ) : (
              <p className="text-2xl font-semibold">
                {avg.toFixed(2)}
                <span className="ml-2 text-sm text-muted-foreground">
                  ({avg > 0.2 ? "positive" : avg < -0.2 ? "negative" : "neutral"})
                </span>
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Mentions</CardTitle></CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold">{e.mention_count}</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>Sample mentions</CardTitle></CardHeader>
        <CardContent>
          {e.sample_mentions.length === 0 ? (
            <p className="text-sm text-muted-foreground">No mentions.</p>
          ) : (
            <ul className="divide-y divide-border">
              {e.sample_mentions.map((m: { surface_form: string; confidence: number; sentiment_score: number | null; record_id: string }, i: number) => (
                <li key={i} className="py-2 text-sm">
                  <Link href={`/records/${m.record_id}`} className="hover:underline">
                    <span className="font-medium">"{m.surface_form}"</span>
                    <span className="ml-2 text-xs text-muted-foreground">
                      conf {Math.round(m.confidence * 100)}%
                      {m.sentiment_score != null && ` · sentiment ${m.sentiment_score.toFixed(2)}`}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
