"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage, type MarketConditions } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from "@/components/ui/card";

const SECTIONS: Array<{
  key: keyof MarketConditions;
  label: string;
  hint: string;
  rows: number;
}> = [
  { key: "what_i_track", label: "What I track", hint: "The list of things you actually measure.", rows: 5 },
  { key: "baselines",    label: "Baselines",    hint: "What 'normal' looks like.", rows: 4 },
  { key: "regimes",      label: "Regimes I recognize", hint: "The named states you recognize.", rows: 6 },
  { key: "thresholds",   label: "Thresholds",   hint: "The numbers that trigger a regime change.", rows: 4 },
  { key: "adjustments",  label: "Process adjustments", hint: "What you do when each regime arrives.", rows: 6 },
  { key: "mechanisms",   label: "Mechanisms I understand", hint: "Why the state changes.", rows: 5 },
];

const EMPTY: MarketConditions = {
  what_i_track: "",
  baselines: "",
  regimes: "",
  thresholds: "",
  adjustments: "",
  mechanisms: "",
};

export default function MarketConditionsDraftEditPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const [form, setForm] = useState<MarketConditions>(EMPTY);
  const [error, setError] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["market-conditions-draft", params.id],
    queryFn: () => api.marketConditions.getDraft(params.id),
  });

  useEffect(() => {
    if (!q.data) return;
    setForm({
      what_i_track: q.data.what_i_track || "",
      baselines:    q.data.baselines    || "",
      regimes:      q.data.regimes      || "",
      thresholds:   q.data.thresholds   || "",
      adjustments:  q.data.adjustments  || "",
      mechanisms:   q.data.mechanisms   || "",
    });
  }, [q.data]);

  const saveAsMine = useMutation({
    mutationFn: async () => {
      await api.marketConditions.save(form);
      await api.marketConditions.acceptDraft(params.id);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["market-conditions"] });
      qc.invalidateQueries({ queryKey: ["market-conditions-drafts"] });
      router.push("/trading/market-conditions");
    },
    onError: (e) => setError(getErrorMessage(e, "Save failed")),
  });

  const deleteDraft = useMutation({
    mutationFn: () => api.marketConditions.deleteDraft(params.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["market-conditions-drafts"] });
      router.push("/trading/market-conditions/drafts");
    },
    onError: (e) => setError(getErrorMessage(e, "Delete failed")),
  });

  if (q.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (!q.data) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">Draft not found.</p>
        <Button onClick={() => router.push("/trading/market-conditions/drafts")}>
          Back to drafts
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs text-muted-foreground">
          <a href="/trading/market-conditions/drafts" className="hover:underline">
            ← All market-condition drafts
          </a>
        </p>
        <h1 className="text-2xl font-semibold mt-2">Market conditions draft</h1>
        <p className="text-sm text-muted-foreground">
          Edit any section. <strong>Save as mine</strong> writes your version
          and marks this draft accepted. <strong>Delete</strong> discards it
          without saving.
        </p>
      </div>

      {SECTIONS.map((s) => (
        <Card key={s.key}>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">{s.label}</CardTitle>
            <CardDescription>{s.hint}</CardDescription>
          </CardHeader>
          <CardContent>
            <textarea
              value={form[s.key]}
              onChange={(e) =>
                setForm({ ...form, [s.key]: e.target.value })
              }
              rows={s.rows}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm leading-relaxed"
            />
          </CardContent>
        </Card>
      ))}

      {error && (
        <p className="text-sm text-red-400 border border-red-500/30 rounded-md px-3 py-2 bg-red-500/5">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <Button
          variant="outline"
          onClick={() => router.push("/trading/market-conditions/drafts")}
        >
          Cancel
        </Button>
        <Button
          variant="outline"
          onClick={() => deleteDraft.mutate()}
          disabled={deleteDraft.isPending}
        >
          {deleteDraft.isPending ? "Deleting…" : "Delete draft"}
        </Button>
        <Button
          onClick={() => saveAsMine.mutate()}
          disabled={saveAsMine.isPending}
        >
          {saveAsMine.isPending ? "Saving…" : "Save as mine"}
        </Button>
      </div>
    </div>
  );
}
