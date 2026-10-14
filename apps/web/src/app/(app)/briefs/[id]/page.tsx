"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function BriefDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const brief = useQuery({
    queryKey: ["brief", params.id],
    queryFn: () => api.getBrief(params.id),
  });

  if (brief.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (brief.isError || !brief.data) return <p className="text-sm text-destructive">Brief not found</p>;

  const b = brief.data;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{b.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {b.brief_date} · {b.word_count ?? "?"} words · status: {b.status}
          </p>
        </div>
        <Button variant="outline" onClick={() => router.back()}>Back</Button>
      </div>

      <Card>
        <CardHeader><CardTitle>Brief</CardTitle></CardHeader>
        <CardContent>
          <div className="whitespace-pre-wrap text-sm leading-relaxed">
            {b.body}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
