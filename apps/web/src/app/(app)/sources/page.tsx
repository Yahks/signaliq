"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {api, ApiError, getErrorMessage} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

const DEFAULT_TICKERS = "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,ORCL";

export default function SourcesPage() {
  const qc = useQueryClient();
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.listSources });

  // File upload source
  const [name, setName] = useState("");
  const [type, setType] = useState("csv");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => api.createSource({ name, source_type: type }),
    onSuccess: () => {
      setName("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (e) => setError(getErrorMessage(e, "Failed")),
  });

  // Stock news fetcher state
  const [tickersInput, setTickersInput] = useState(DEFAULT_TICKERS);
  const [autoBuild, setAutoBuild] = useState(true);
  const [fetchResult, setFetchResult] = useState<{ taskId: string; message: string } | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const fetchStock = useMutation({
    mutationFn: () => {
      const tickers = tickersInput
        .split(",")
        .map((t) => t.trim().toUpperCase())
        .filter((t) => t.length > 0);
      if (tickers.length === 0) {
        throw new ApiError(400, "no_tickers", "Enter at least one ticker");
      }
      return api.fetchStockNews({
        tickers,
        autoBuild,
        subtopicSlug: "us_stocks",
        maxEvents: 30,
      });
    },
    onSuccess: (data) => {
      setFetchResult({
        taskId: data.task_id,
        message: autoBuild
          ? `Fetching live news for ${tickersInput.split(",").length} tickers. Events will be extracted and a chain built automatically. Check /chains in ~2 minutes.`
          : `Fetching live news. Events will be extracted and stored. Check /records or /chains to see them.`,
      });
      setFetchError(null);
    },
    onError: (e) => {
      setFetchError(getErrorMessage(e, "Fetch failed"));
      setFetchResult(null);
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Sources</h1>
        <p className="text-sm text-muted-foreground">
          Ingest data into SignalIQ — from files or live external sources
        </p>
      </div>

      {/* Live stock news fetcher */}
      <Card>
        <CardHeader>
          <CardTitle>Fetch live stock news</CardTitle>
          <CardDescription>
            Pulls real-time headlines from SEC EDGAR, CNBC, Nasdaq, and Investing.com.
            Events are extracted by AI and stored in the <strong>trading</strong> block.
            Robots.txt is respected; rates are limited to be a good citizen.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Tickers (comma-separated)</label>
            <Input
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value)}
              placeholder={DEFAULT_TICKERS}
              disabled={fetchStock.isPending}
            />
          </div>

          <div className="flex items-center gap-2">
            <input
              id="auto-build"
              type="checkbox"
              checked={autoBuild}
              onChange={(e) => setAutoBuild(e.target.checked)}
              disabled={fetchStock.isPending}
              className="h-4 w-4 rounded border-border"
            />
            <label htmlFor="auto-build" className="text-sm">
              Automatically build a trading chain from the extracted events
            </label>
          </div>

          {fetchError && <p className="text-sm text-destructive">{fetchError}</p>}

          {fetchResult && (
            <div className="rounded-md border border-green-500/30 bg-green-500/5 p-3 text-sm">
              <p className="font-medium text-green-400">✓ Fetch started</p>
              <p className="mt-1 text-xs text-muted-foreground">{fetchResult.message}</p>
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                task: {fetchResult.taskId.slice(0, 8)}…
              </p>
            </div>
          )}

          <Button
            type="button"
            onClick={() => fetchStock.mutate()}
            disabled={fetchStock.isPending}
          >
            {fetchStock.isPending ? "Fetching…" : "Fetch live stock news"}
          </Button>
        </CardContent>
      </Card>

      {/* File upload source */}
      <Card>
        <CardHeader>
          <CardTitle>Create file source</CardTitle>
          <CardDescription>
            For manual uploads — CSV, JSON, or JSONL. Used for curated news events,
            Facebook exports, or any structured data.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-3">
            <Input
              placeholder="Source name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <select
              value={type}
              onChange={(e) => setType(e.target.value)}
              className="rounded-md border border-border bg-transparent px-3 text-sm"
            >
              <option value="csv">CSV</option>
              <option value="json">JSON</option>
              <option value="jsonl">JSONL</option>
            </select>
            <Button disabled={!name || create.isPending} onClick={() => create.mutate()}>
              {create.isPending ? "Creating…" : "Create"}
            </Button>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </CardContent>
      </Card>

      {/* Existing sources list */}
      <Card>
        <CardHeader><CardTitle>Your sources ({sources.data?.length ?? 0})</CardTitle></CardHeader>
        <CardContent>
          {sources.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : sources.data?.length === 0 ? (
            <p className="text-sm text-muted-foreground">No sources yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {sources.data?.map((s) => (
                <li key={s.id} className="flex items-center justify-between py-3">
                  <div>
                    <p className="font-medium">{s.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {s.source_type} · {s.id.slice(0, 8)}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
