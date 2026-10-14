"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function PainPointDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const pp = useQuery({ queryKey: ["pain-point", params.id], queryFn: () => api.getPainPoint(params.id) });

  if (pp.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (pp.isError || !pp.data) return <p className="text-sm text-destructive">Not found</p>;

  const p = pp.data;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold capitalize">{p.title}</h1>
          {p.severity != null && (
            <p className="text-sm text-muted-foreground mt-1">Severity: {p.severity.toFixed(3)}</p>
          )}
        </div>
        <Button variant="outline" onClick={() => router.back()}>Back</Button>
      </div>

      <Card>
        <CardHeader><CardTitle>Sample records ({p.sample_records.length})</CardTitle></CardHeader>
        <CardContent>
          <ul className="divide-y divide-border">
            {p.sample_records.map((r) => (
              <li key={r.record_id} className="py-3">
                <Link href={`/records/${r.record_id}`} className="block hover:bg-muted -mx-3 px-3 py-1 rounded">
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
