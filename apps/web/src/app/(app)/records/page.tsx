"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const PAGE_SIZE = 25;

export default function RecordsPage() {
  const [offset, setOffset] = useState(0);
  const records = useQuery({
    queryKey: ["records", offset],
    queryFn: () => api.listRecords({ limit: PAGE_SIZE, offset }),
  });

  const total = records.data?.total ?? 0;
  const items = records.data?.items ?? [];
  const pageEnd = Math.min(offset + PAGE_SIZE, total);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Records</h1>
        <p className="text-sm text-muted-foreground">
          {total === 0 ? "No records yet." : `Showing ${offset + 1}–${pageEnd} of ${total}`}
        </p>
      </div>

      <Card>
        <CardHeader><CardTitle>Normalized records</CardTitle></CardHeader>
        <CardContent>
          {records.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No records. <Link className="underline" href="/ingest/new">Upload a file</Link>.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((r) => (
                <li key={r.id} className="py-3">
                  <Link href={`/records/${r.id}`} className="block hover:bg-muted -mx-3 px-3 py-1 rounded">
                    <p className="line-clamp-2 text-sm">{r.normalized_text}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {r.detected_language} · {r.tokens_count} tokens
                      {r.urls.length > 0 && ` · ${r.urls.length} urls`}
                      {r.hashtags.length > 0 && ` · #${r.hashtags.slice(0, 3).join(" #")}`}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {total > PAGE_SIZE && (
            <div className="mt-4 flex justify-between">
              <Button variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</Button>
              <Button variant="outline" disabled={pageEnd >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
