"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type TopicResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function TopicsPage() {
  const topics = useQuery({ queryKey: ["topics"], queryFn: () => api.listTopics({ limit: 200 }) });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Topics</h1>
        <p className="text-sm text-muted-foreground">
          {topics.data?.length ?? 0} topics discovered
        </p>
      </div>

      <Card>
        <CardHeader><CardTitle>Ranked by mentions</CardTitle></CardHeader>
        <CardContent>
          {topics.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : topics.data?.length === 0 ? (
            <p className="text-sm text-muted-foreground">No topics yet.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {topics.data?.map((t: TopicResponse) => (
                <span
                  key={t.id}
                  className="rounded-md border border-border bg-muted/50 px-3 py-1 text-sm"
                  title={`${t.record_count} records`}
                >
                  {t.name}
                  <span className="ml-2 text-xs text-muted-foreground">{t.record_count}</span>
                </span>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
