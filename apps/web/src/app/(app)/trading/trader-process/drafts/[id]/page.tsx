"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage, type ProcessDocument } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const SECTIONS: Array<{
  key: keyof ProcessDocument;
  label: string;
  hint: string;
}> = [
  { key: "daily_scan",           label: "The daily scan",       hint: "What you screen for. The conditions. The universe it produces." },
  { key: "entry_logic",          label: "Entry logic",          hint: "The effects you harvest. The thresholds. The research behind each rule." },
  { key: "position_sizing",      label: "Position sizing",      hint: "Your martingale logic. The caps. When the size scales." },
  { key: "exit_logic",           label: "Exit logic",           hint: "When you close. When you roll. The rules." },
  { key: "holding_period",       label: "Holding period",       hint: "The range and how it's chosen for a given setup." },
  { key: "research_to_rules",    label: "Research-to-rules",    hint: "The papers you've built from. How each became a condition." },
  { key: "chart_overlay",        label: "The chart overlay",    hint: "What you still check by hand. Why you don't automate it." },
  { key: "no_backtest_rule",     label: "The no-backtest rule", hint: "Why you don't backtest. What you do instead." },
  { key: "multi_strategy_blend", label: "Multi-strategy blend", hint: "How momentum, mean reversion, and martingale run together." },
  { key: "rolling",              label: "Rolling",              hint: "How you roll options or futures positions. When and why." },
  { key: "stocks_vs_options",    label: "Stocks vs options",    hint: "Why you use one or the other. What each is for." },
  { key: "universes",            label: "Universes",            hint: "How your scan criteria decide the tradable set." },
  { key: "emotion_removal",      label: "Emotion removal",      hint: "What's systematized. What isn't." },
  { key: "fundamental_metrics",  label: "Fundamental metrics",  hint: "Which fundamental checks you run before entering. Why. What you do with them." },
  { key: "trade_history_summary", label: "Trade history summary", hint: "Your aggregate performance — trades, win rate, average win, average loss, max drawdown, Sharpe." },
];

const EMPTY: ProcessDocument = {
  daily_scan: "",
  entry_logic: "",
  position_sizing: "",
  exit_logic: "",
  holding_period: "",
  research_to_rules: "",
  chart_overlay: "",
  no_backtest_rule: "",
  multi_strategy_blend: "",
  rolling: "",
  stocks_vs_options: "",
  universes: "",
  emotion_removal: "",
  fundamental_metrics: "",
  trade_history_summary: "",
};

export default function DraftEditPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const [form, setForm] = useState<ProcessDocument>(EMPTY);
  const [error, setError] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["trader-process-draft", params.id],
    queryFn: () => api.traderProcess.getDraft(params.id),
  });

  useEffect(() => {
    if (q.data) {
      setForm({
        daily_scan: q.data.daily_scan || "",
        entry_logic: q.data.entry_logic || "",
        position_sizing: q.data.position_sizing || "",
        exit_logic: q.data.exit_logic || "",
        holding_period: q.data.holding_period || "",
        research_to_rules: q.data.research_to_rules || "",
        chart_overlay: q.data.chart_overlay || "",
        no_backtest_rule: q.data.no_backtest_rule || "",
        multi_strategy_blend: q.data.multi_strategy_blend || "",
        rolling:              q.data.rolling || "",
        stocks_vs_options:    q.data.stocks_vs_options || "",
        universes:            q.data.universes || "",
        emotion_removal:      q.data.emotion_removal || "",
        fundamental_metrics:  q.data.fundamental_metrics || "",
        trade_history_summary: q.data.trade_history_summary || "",
      });
    }
  }, [q.data]);

  const saveAsMine = useMutation({
    mutationFn: async () => {
      // First, save the trader's edited document as their process.
      await api.traderProcess.save(form);
      // Then mark the draft reviewed (accepts it in the backend).
      await api.traderProcess.acceptDraft(params.id);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trader-process"] });
      qc.invalidateQueries({ queryKey: ["trader-process-drafts"] });
      router.push("/trading/trader-process");
    },
    onError: (e) => setError(getErrorMessage(e, "Save failed")),
  });

  const deleteDraft = useMutation({
    mutationFn: () => api.traderProcess.deleteDraft(params.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trader-process-drafts"] });
      router.push("/trading/trader-process/drafts");
    },
    onError: (e) => setError(getErrorMessage(e, "Delete failed")),
  });

  if (q.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  if (!q.data) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Draft not found. It may have been deleted.
        </p>
        <Button onClick={() => router.push("/trading/trader-process/drafts")}>
          Back to drafts
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs text-muted-foreground">
          <a href="/trading/trader-process/drafts" className="hover:underline">
            ← All drafts
          </a>
        </p>
        <h1 className="text-2xl font-semibold mt-2">Process draft</h1>
        <p className="text-sm text-muted-foreground">
          Edit any section. <strong>Save as my process</strong> writes your
          version and clears the draft. <strong>Delete</strong> discards the
          draft without saving.
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
              value={form[s.key] as string}
              onChange={(e) =>
                setForm({ ...form, [s.key]: e.target.value })
              }
              rows={6}
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
          onClick={() => router.push("/trading/trader-process/drafts")}
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
          {saveAsMine.isPending ? "Saving…" : "Save as my process"}
        </Button>
      </div>
    </div>
  );
}
