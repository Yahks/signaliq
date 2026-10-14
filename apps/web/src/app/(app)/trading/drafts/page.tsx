"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type DraftPostResponse } from "@/lib/api";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

function DraftRow({ d }: { d: DraftPostResponse }) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [showReject, setShowReject] = useState(false);

  const approve = useMutation({
    mutationFn: () => api.approveDraft(d.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trading-drafts"] }),
  });

  const reject = useMutation({
    mutationFn: () => api.rejectDraft(d.id, rejectReason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trading-drafts"] });
      setShowReject(false);
    },
  });

  // S.7.6d.2 — move to trash. Reversible; no confirm.
  const deleteDraft = useMutation({
    mutationFn: () => api.deleteTradingPost(d.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trading-drafts"] });
    },
  });

  return (
    <div className="rounded-md border border-border p-3 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">
            <Link href={`/trading/${d.subblock_slug}`} className="hover:underline">
              {d.subblock_name}
            </Link>
            {" · "}
            {new Date(d.created_at).toLocaleString()}
            {d.word_count != null && ` · ${d.word_count} words`}
          </p>
          <p className="mt-0.5 font-medium truncate">{d.title}</p>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? "Collapse" : "Preview"}
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={() => approve.mutate()}
            disabled={approve.isPending}
          >
            {approve.isPending ? "…" : "Approve"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowReject(!showReject)}
            disabled={reject.isPending}
          >
            Reject
          </Button>
          <Button
            variant="ghost"
            size="sm"
            title="Move to trash"
            onClick={() => deleteDraft.mutate()}
            disabled={deleteDraft.isPending}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {d.unsupported_claims.length > 0 && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs space-y-1">
          <p className="text-amber-400 font-medium">
            ⓘ Verifier flagged {d.unsupported_claims.length} unsupported claim{d.unsupported_claims.length === 1 ? "" : "s"}:
          </p>
          <ul className="list-disc pl-5 text-amber-200/80 space-y-0.5">
            {d.unsupported_claims.slice(0, 5).map((c, i) => (
              <li key={i} className="line-clamp-2">{c}</li>
            ))}
            {d.unsupported_claims.length > 5 && (
              <li className="italic">+{d.unsupported_claims.length - 5} more</li>
            )}
          </ul>
        </div>
      )}
      {d.unsupported_claims.length === 0 && d.regenerated && (
        <p className="text-xs text-muted-foreground">
          ⓘ Regenerated once, but still had unsupported claims.
        </p>
      )}

      {showReject && (
        <div className="rounded-md border border-border bg-muted/30 p-2 space-y-2">
          <input
            type="text"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            placeholder="Optional reason (recorded in post_feedback)"
            className="w-full rounded-md border border-border bg-transparent px-2 py-1 text-xs"
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={() => reject.mutate()} disabled={reject.isPending}>
              {reject.isPending ? "Rejecting…" : "Confirm reject"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShowReject(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {expanded && (
        <div className="rounded-md border border-border bg-muted/20 p-3 max-h-96 overflow-y-auto">
          <pre className="whitespace-pre-wrap text-xs font-mono">{d.body}</pre>
        </div>
      )}
    </div>
  );
}

export default function DraftsPage() {
  const q = useQuery({
    queryKey: ["trading-drafts"],
    queryFn: () => api.listDrafts(200),
    refetchInterval: 60_000,
  });

  const items = q.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Drafts awaiting review</h1>
          <p className="text-sm text-muted-foreground">
            {items.length} draft{items.length === 1 ? "" : "s"} flagged by the verifier
          </p>
        </div>
        <Link href="/trading" className="text-sm text-blue-400 hover:underline">
          ← Back to Trading
        </Link>
      </div>

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading drafts…</p>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No drafts. Every generated post passed verification.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Pending</CardTitle>
            <CardDescription>
              Approve to publish · Reject to mark failed · Both are recorded in post_feedback
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {items.map((d) => <DraftRow key={d.id} d={d} />)}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
