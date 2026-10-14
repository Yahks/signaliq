"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EnrichmentCard } from "@/components/enrichment/enrichment-card";

export default function RecordDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const rec = useQuery({ queryKey: ["record", params.id], queryFn: () => api.getRecord(params.id) });

  if (rec.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (rec.isError || !rec.data) return <p className="text-sm text-destructive">Record not found</p>;

  const { raw, normalized } = rec.data;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Record detail</h1>
          <p className="font-mono text-xs text-muted-foreground">{raw.id}</p>
        </div>
        <Button variant="outline" onClick={() => router.back()}>Back</Button>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Raw</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="whitespace-pre-wrap">{raw.text}</p>
            <div className="border-t border-border pt-2 text-xs text-muted-foreground">
              {raw.author_identifier && <div>Author: {raw.author_identifier}</div>}
              {raw.original_timestamp && <div>Timestamp: {raw.original_timestamp}</div>}
              <div>Hash: {raw.content_hash.slice(0, 16)}…</div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Normalized</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            {normalized ? (
              <>
                <p className="whitespace-pre-wrap">{normalized.normalized_text}</p>
                <div className="border-t border-border pt-2 text-xs text-muted-foreground space-y-1">
                  <div>Language: {normalized.detected_language} ({normalized.language_confidence?.toFixed(2) ?? "n/a"})</div>
                  <div>Tokens: {normalized.tokens_count}</div>
                  {normalized.urls.length > 0 && <div>URLs: {normalized.urls.length}</div>}
                  {normalized.hashtags.length > 0 && <div>Hashtags: #{normalized.hashtags.join(" #")}</div>}
                  {normalized.mentions.length > 0 && <div>Mentions: @{normalized.mentions.join(" @")}</div>}
                </div>
              </>
            ) : (
              <p className="text-muted-foreground">Not yet normalized.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <EnrichmentCard recordId={raw.id} />
    </div>
  );
}
