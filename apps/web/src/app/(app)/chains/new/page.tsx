"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type ChainBuildResponse, type BlockResponse, getErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

const SAMPLE: Record<string, string> = {
  news: `The Federal Reserve signalled on Tuesday that it will hold interest rates higher for longer, with Chair Jerome Powell telling reporters the committee sees no path to cutting rates before the second half of next year.

The decision sent Treasury yields sharply higher. The 10-year note jumped 18 basis points to 5.02%, its highest level since 2007.

Equity markets sold off broadly in response. The S&P 500 fell 2.1%, its worst single-day performance since August.

Currency markets also reacted. The dollar index rose 0.7% to 106.8, its strongest level since November, with the greenback gaining 1.2% against the yen.

Analysts were divided on the implications, with Morgan Stanley and Goldman Sachs offering conflicting interpretations of whether the Fed's stance will weigh on growth into 2025.`,
  trading: `NVIDIA reported Q3 earnings on Wednesday, beating expectations with revenue of $35.1 billion, up 94% year over year. Data center revenue was $30.8 billion, up 112%.

The company guided Q4 revenue to approximately $37.5 billion, above analyst estimates of $37.0 billion.

CEO Jensen Huang said demand for the company's Blackwell platform is "insane" and that supply constraints remain the primary bottleneck.

Following the announcement, NVIDIA shares rose 5% in after-hours trading. Competitors AMD and Broadcom also rose modestly on sympathy.`,
};

type Phase = "idle" | "extracting" | "building" | "done" | "failed";

export default function NewChainPage() {
  const router = useRouter();
  const blocks = useQuery({ queryKey: ["blocks"], queryFn: api.listBlocks });

  const [blockSlug, setBlockSlug] = useState<string>("news");
  const [subtopicSlug, setSubtopicSlug] = useState<string | null>(null);
  const [content, setContent] = useState<string>(SAMPLE.news);
  const [error, setError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [reason, setReason] = useState<string | null>(null);

  const activeBlock: BlockResponse | undefined = blocks.data?.find(
    (b) => b.slug === blockSlug,
  );

  function onBlockChange(slug: string) {
    setBlockSlug(slug);
    setSubtopicSlug(null);
    if (SAMPLE[slug]) setContent(SAMPLE[slug]);
    setError(null);
    setTaskId(null);
    setPhase("idle");
    setReason(null);
  }

  const build = useMutation({
    mutationFn: () =>
      api.buildChainFromContent(content, {
        blockSlug,
        subtopicSlug,
        targetWords: activeBlock?.config?.default_target_words as number | undefined,
      }),
    onSuccess: (data: ChainBuildResponse) => {
      setTaskId(data.chain_id);   // the task id is returned as chain_id at enqueue
      setPhase("extracting");
      setError(null);
    },
    onError: (e) => setError(getErrorMessage(e, "Build failed")),
  });

  // Poll the status endpoint while the task is running
  const status = useQuery({
    queryKey: ["chain-build-status", taskId],
    queryFn: () => api.getChainBuildStatus(taskId as string),
    enabled: !!taskId && (phase === "extracting" || phase === "building"),
    refetchInterval: (query) => {
      const data = query.state.data as { status?: string } | undefined;
      if (!data) return 2000;
      if (data.status === "success" || data.status === "failure") return false;
      return 2000;
    },
  });

  useEffect(() => {
    const s = status.data?.status;
    if (!s) return;
    if (s === "started" && phase === "extracting") setPhase("building");
    if (s === "success") {
      setPhase("done");
      const chainId = status.data?.chain_id;
      if (chainId) {
        setTimeout(() => router.push(`/chains/${chainId}`), 1200);
      }
    }
    if (s === "failure") {
      setPhase("failed");
      setReason(status.data?.error || status.data?.reason || "Chain build failed");
    }
  }, [status.data, phase, router]);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setTaskId(null);
    setPhase("idle");
    setReason(null);
    build.mutate();
  }

  function onFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const c = ev.target?.result;
      if (typeof c === "string") setContent(c);
    };
    reader.readAsText(file);
  }

  const words = content.trim() ? content.trim().split(/\s+/).length : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Build a chain</h1>
        <p className="text-sm text-muted-foreground">
          Paste anything — an article, a tweet, a paragraph, JSON. The AI extracts
          events, finds related ones from your records, and writes a verified article.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Block</CardTitle>
          <CardDescription>
            Each block has its own voice and structure.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {blocks.isLoading ? (
              <p className="text-sm text-muted-foreground">Loading blocks…</p>
            ) : (
              blocks.data?.map((b) => (
                <button
                  key={b.slug}
                  type="button"
                  onClick={() => onBlockChange(b.slug)}
                  className={`rounded-md border px-3 py-1.5 text-sm transition ${
                    b.slug === blockSlug
                      ? "border-foreground bg-foreground text-background"
                      : "border-border hover:bg-muted"
                  }`}
                >
                  {b.name}
                </button>
              ))
            )}
          </div>

          {activeBlock && (
            <div className="rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
              <p>
                <strong className="text-foreground">{activeBlock.name}</strong>
                {" — "}
                {activeBlock.description}
              </p>
              <p className="mt-1">
                Target: {String(activeBlock.config?.default_target_words ?? "—")} words
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Content</CardTitle>
          <CardDescription>
            Paste or upload any text. The AI extracts the events.
            Charged 1 token per 100 words.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="flex items-center gap-3">
              <label className="inline-flex items-center gap-2 text-sm cursor-pointer rounded-md border border-border px-3 py-1.5 hover:bg-muted">
                Upload file
                <input type="file" accept=".txt,.md,.json" className="hidden" onChange={onFileUpload} />
              </label>
              <Button type="button" variant="outline" onClick={() => setContent(SAMPLE[blockSlug] ?? "")}>
                Load sample
              </Button>
              <span className="text-xs text-muted-foreground ml-auto">
                {words} words · {Math.max(1, Math.floor(words / 100))} tokens
              </span>
            </div>

            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Paste an article, a tweet, a paragraph, or any text about what you want the chain to be about…"
              className="w-full rounded-md border border-border bg-transparent p-3 text-sm leading-relaxed min-h-[280px] focus:outline-none focus:ring-2 focus:ring-ring"
            />

            {error && <p className="text-sm text-destructive">{error}</p>}

            {phase === "extracting" && (
              <div className="rounded-md border border-border bg-muted/30 p-3 text-sm">
                <p className="font-medium">Extracting events…</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Reading your text and identifying the discrete events.
                </p>
              </div>
            )}

            {phase === "building" && (
              <div className="rounded-md border border-border bg-muted/30 p-3 text-sm">
                <p className="font-medium">Building the chain…</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Finding related events, sequencing the story, writing, and verifying.
                  Takes ~60–90 seconds.
                </p>
              </div>
            )}

            {phase === "done" && (
              <div className="rounded-md border border-green-500/30 bg-green-500/5 p-3 text-sm">
                <p className="font-medium text-green-400">✓ Chain built</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Redirecting to the article…
                </p>
              </div>
            )}

            {phase === "failed" && (
              <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm">
                <p className="font-medium text-red-400">
                  Couldn't build a chain
                </p>
                {status.data?.error_message && (
                  <p className="mt-2 text-red-400">
                    {status.data.error_message}
                  </p>
                )}
                {status.data?.error_action && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {status.data.error_action}
                  </p>
                )}
                {/* S.6.2.3b — always surface the raw error beneath the
                    friendly message, especially when the classifier
                    returned 'unknown'. Collapsed so it doesn't dominate
                    the card, expanded on click. */}
                {(status.data?.error_kind === "unknown" || !status.data?.error_kind) && reason && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                      Technical details
                    </summary>
                    <p className="mt-1 text-xs text-muted-foreground break-all font-mono">
                      {reason.slice(0, 400)}
                      {reason.length > 400 ? "…" : ""}
                    </p>
                  </details>
                )}
              </div>
            )}

            <Button
              type="submit"
              disabled={build.isPending || phase === "extracting" || phase === "building" || phase === "done" || content.trim().length < 20}
            >
              {build.isPending || phase === "extracting" || phase === "building"
                ? "Working…"
                : `Build ${activeBlock?.name ?? ""} chain`}
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>How it works</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p><strong>1 — Extract:</strong> the AI reads your paste and pulls out the discrete events.</p>
          <p><strong>2 — Enrich:</strong> related events from your records are added automatically.</p>
          <p><strong>3 — Select:</strong> the AI picks the events that form one coherent story.</p>
          <p><strong>4 — Order, link, narrate:</strong> sequenced, linked, and written as a long-form article.</p>
          <p><strong>5 — Verify:</strong> every claim audited against the sources. Flagged claims trigger one regeneration.</p>
          <p className="pt-2">Takes ~60–90 seconds.</p>
        </CardContent>
      </Card>
    </div>
  );
}
