"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type NewsChainResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function ChainsPage() {
  const chains = useQuery({ queryKey: ["chains"], queryFn: () => api.listChains(50) });

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">News chains</h1>
          <p className="text-sm text-muted-foreground">
            Long-form articles built from causal chains of news events
          </p>
        </div>
        <Link href="/chains/new">
          <Button>Build new chain</Button>
        </Link>
      </div>

      {chains.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : chains.data?.length === 0 ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No chains yet. <strong>Paste an article</strong> on the Build page to create one.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {chains.data?.map((c: NewsChainResponse) => (
            <Link key={c.id} href={`/chains/${c.id}`}>
              <Card className="hover:bg-muted/30 transition cursor-pointer h-full">
                <CardHeader>
                  <CardTitle className="text-base line-clamp-2">{c.title}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {c.thesis && (
                    <p className="text-muted-foreground line-clamp-3 text-xs">{c.thesis}</p>
                  )}
                  <div className="flex items-center gap-2 text-xs">
                    <span className="rounded bg-muted px-2 py-0.5">{c.status}</span>
                    {c.confidence != null && (
                      <span className="text-muted-foreground">
                        conf {c.confidence.toFixed(2)}
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
