"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type EnrichmentResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function sentimentColor(label: string | null): string {
  switch (label) {
    case "positive": return "bg-green-500/20 text-green-400 border-green-500/30";
    case "negative": return "bg-red-500/20 text-red-400 border-red-500/30";
    case "mixed":    return "bg-yellow-500/20 text-yellow-400 border-yellow-500/30";
    default:         return "bg-muted text-muted-foreground border-border";
  }
}

function EmotionBar({ name, value }: { name: string; value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-20 capitalize text-muted-foreground">{name}</span>
      <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
        <div className="h-full bg-foreground/60" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right text-muted-foreground">{pct}%</span>
    </div>
  );
}

export function EnrichmentCard({ recordId }: { recordId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["enrichment", recordId],
    queryFn: () => api.getEnrichment(recordId),
    retry: false,
  });

  if (isLoading) return <Card><CardHeader><CardTitle>AI Analysis</CardTitle></CardHeader><CardContent><p className="text-sm text-muted-foreground">Loading…</p></CardContent></Card>;

  if (error || !data) {
    return (
      <Card>
        <CardHeader><CardTitle>AI Analysis</CardTitle></CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Not yet analyzed.</p>
        </CardContent>
      </Card>
    );
  }

  const enr = data as EnrichmentResponse;

  return (
    <Card>
      <CardHeader><CardTitle>AI Analysis</CardTitle></CardHeader>
      <CardContent className="space-y-4 text-sm">
        {/* Sentiment */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">Sentiment</span>
          <span className={`rounded-md border px-2 py-0.5 text-xs capitalize ${sentimentColor(enr.sentiment_label)}`}>
            {enr.sentiment_label ?? "unknown"}
          </span>
          {enr.sentiment_confidence != null && (
            <span className="text-xs text-muted-foreground">
              conf {Math.round(enr.sentiment_confidence * 100)}%
            </span>
          )}
        </div>

        {/* Intent */}
        {enr.intent && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Intent</span>
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs">{enr.intent}</span>
          </div>
        )}

        {/* Emotions */}
        {Object.keys(enr.emotions).length > 0 && (
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground">Emotions</p>
            {Object.entries(enr.emotions)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 5)
              .map(([k, v]) => <EmotionBar key={k} name={k} value={v} />)}
          </div>
        )}

        {/* Entities */}
        {enr.raw_entities.length > 0 && (
          <div>
            <p className="mb-1 text-xs text-muted-foreground">Entities</p>
            <div className="flex flex-wrap gap-1">
              {enr.raw_entities.map((e, i) => (
                <span key={i} className="rounded-md border border-border bg-muted/50 px-2 py-0.5 text-xs">
                  {e.text} <span className="text-muted-foreground">· {e.type}</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Topics */}
        {enr.raw_topics.length > 0 && (
          <div>
            <p className="mb-1 text-xs text-muted-foreground">Topics</p>
            <div className="flex flex-wrap gap-1">
              {enr.raw_topics.map((t, i) => (
                <span key={i} className="rounded-md bg-muted px-2 py-0.5 text-xs">{t}</span>
              ))}
            </div>
          </div>
        )}

        {/* Pain points */}
        {enr.pain_point_signals.length > 0 && (
          <div>
            <p className="mb-1 text-xs text-muted-foreground">Pain points</p>
            <ul className="space-y-0.5 text-xs">
              {enr.pain_point_signals.map((p, i) => (
                <li key={i} className="rounded-md bg-red-500/10 px-2 py-1 text-red-300">{p}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Feature requests */}
        {enr.feature_request_signals.length > 0 && (
          <div>
            <p className="mb-1 text-xs text-muted-foreground">Feature requests</p>
            <ul className="space-y-0.5 text-xs">
              {enr.feature_request_signals.map((f, i) => (
                <li key={i} className="rounded-md bg-blue-500/10 px-2 py-1 text-blue-300">{f}</li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
