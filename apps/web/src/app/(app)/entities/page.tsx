"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type EntityResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function EntitiesPage() {
  const entities = useQuery({ queryKey: ["entities"], queryFn: () => api.listEntities({ limit: 200 }) });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Entities</h1>
        <p className="text-sm text-muted-foreground">
          {entities.data?.length ?? 0} entities discovered from your records
        </p>
      </div>

      <Card>
        <CardHeader><CardTitle>Ranked by mentions</CardTitle></CardHeader>
        <CardContent>
          {entities.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : entities.data?.length === 0 ? (
            <p className="text-sm text-muted-foreground">No entities yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {entities.data?.map((e: EntityResponse) => (
                <li key={e.id}>
                  <Link href={`/entities/${e.id}`} className="flex items-center justify-between py-3 hover:bg-muted -mx-3 px-3 rounded">
                    <div>
                      <p className="font-medium">{e.canonical_name}</p>
                      <p className="text-xs text-muted-foreground capitalize">{e.type}</p>
                    </div>
                    <span className="text-sm text-muted-foreground">{e.mention_count} mentions</span>
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
