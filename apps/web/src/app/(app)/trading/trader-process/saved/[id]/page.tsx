"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage, type ProcessDocument } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
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
];

export default function SavedProcessDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["trader-process", params.id],
    queryFn: () => api.traderProcess.getHistory(params.id),
  });

  const remove = useMutation({
    mutationFn: () => api.traderProcess.deleteHistory(params.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trader-process-list"] });
      router.push("/trading/trader-process/saved");
    },
  });

  if (q.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (!q.data) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Saved document not found.
        </p>
        <Link href="/trading/trader-process/saved">
          <Button variant="outline">Back to saved list</Button>
        </Link>
      </div>
    );
  }

  const doc = q.data;
  const filled = SECTIONS.filter(
    (s) => (doc[s.key] || "").trim().length > 0,
  ).length;

  const onDelete = () => {
    if (
      window.confirm(
        "Move this saved process to trash? You can save a new one anytime.",
      )
    ) {
      remove.mutate();
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <p className="text-xs text-muted-foreground">
            <Link
              href="/trading/trader-process/saved"
              className="hover:underline"
            >
              ← All saved processes
            </Link>
          </p>
          <h1 className="text-2xl font-semibold mt-2">Saved process</h1>
          <p className="text-sm text-muted-foreground">
            Read-only. {filled} of {SECTIONS.length} sections filled.{" "}
            {doc.updated_at
              ? `Last saved ${new Date(doc.updated_at).toLocaleString()}.`
              : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => router.push("/trading/trader-process?edit=1")}
            disabled={filled === 0}
          >
            Edit
          </Button>
          <Button
            variant="outline"
            onClick={onDelete}
            disabled={filled === 0 || remove.isPending}
            className="text-red-400 border-red-500/30 hover:bg-red-500/10"
          >
            {remove.isPending ? "Moving…" : "Move to trash"}
          </Button>
          <Link href="/trading/trader-process">
            <Button>Compose new</Button>
          </Link>
        </div>
      </div>

      {remove.isError && (
        <p className="text-sm text-red-400 border border-red-500/30 rounded-md px-3 py-2 bg-red-500/5">
          {getErrorMessage(remove.error, "Delete failed")}
        </p>
      )}

      {SECTIONS.map((s) => {
        const value = (doc[s.key] as string) || "";
        const isEmpty = value.trim().length === 0;
        return (
          <Card key={s.key}>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{s.label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm whitespace-pre-wrap leading-relaxed">
                {isEmpty ? (
                  <span className="text-muted-foreground italic">(empty)</span>
                ) : (
                  value
                )}
              </p>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
