"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { api, type SemanticSearchResponse, getErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SemanticSearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const search = useMutation({
    mutationFn: (q: string) => api.semanticSearch(q, 20),
    onSuccess: (data) => {
      setResult(data);
      setError(null);
    },
    onError: (e) => setError(getErrorMessage(e, "Search failed")),
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (query.trim().length === 0) return;
    search.mutate(query.trim());
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Semantic search</h1>
        <p className="text-sm text-muted-foreground">
          Search by meaning, not just keywords. Powered by local embeddings.
        </p>
      </div>

      <Card>
        <CardHeader><CardTitle>Query</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="flex gap-2">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. Iran economic pressure and oil exports"
              className="flex-1"
            />
            <Button type="submit" disabled={search.isPending}>
              {search.isPending ? "Searching…" : "Search"}
            </Button>
          </form>
          {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
        </CardContent>
      </Card>

      {result && (
        <Card>
          <CardHeader>
            <CardTitle>
              {result.total} results for “{result.query}”
            </CardTitle>
          </CardHeader>
          <CardContent>
            {result.hits.length === 0 ? (
              <p className="text-sm text-muted-foreground">No results.</p>
            ) : (
              <ul className="divide-y divide-border">
                {result.hits.map((h, i) => (
                  <li key={`${h.record_id}-${i}`} className="py-3">
                    <Link
                      href={`/records/${h.record_id}`}
                      className="block hover:bg-muted -mx-3 px-3 py-1 rounded"
                    >
                      <div className="flex items-center gap-2 text-xs">
                        <span className="rounded-md bg-foreground/10 px-2 py-0.5 font-mono">
                          {h.similarity.toFixed(3)}
                        </span>
                        <span className="text-muted-foreground">{h.detected_language}</span>
                      </div>
                      <p className="mt-1.5 text-sm line-clamp-3">{h.normalized_text}</p>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
