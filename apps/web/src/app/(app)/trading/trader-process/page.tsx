"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
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
  { key: "trade_history_summary", label: "Trade history summary", hint: "Your aggregate performance — trades, win rate, average win, average loss, max drawdown, Sharpe. The register quotes this for any performance claim." },
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

export default function TraderProcessPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const search = useSearchParams();
  const editMode = search.get("edit") === "1";
  const [form, setForm] = useState<ProcessDocument>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Read saved state — used only to show a "N sections saved" badge,
  // never to pre-fill the composer.
  const saved = useQuery({
    queryKey: ["trader-process"],
    queryFn: () => api.traderProcess.get(),
  });

  useEffect(() => {
    if (!editMode || !saved.data) return;
    setForm({
      daily_scan:           saved.data.daily_scan || "",
      entry_logic:          saved.data.entry_logic || "",
      position_sizing:      saved.data.position_sizing || "",
      exit_logic:           saved.data.exit_logic || "",
      holding_period:       saved.data.holding_period || "",
      research_to_rules:    saved.data.research_to_rules || "",
      chart_overlay:        saved.data.chart_overlay || "",
      no_backtest_rule:     saved.data.no_backtest_rule || "",
      multi_strategy_blend: saved.data.multi_strategy_blend || "",
      rolling:              saved.data.rolling || "",
      stocks_vs_options:    saved.data.stocks_vs_options || "",
      universes:            saved.data.universes || "",
      emotion_removal:      saved.data.emotion_removal || "",
      fundamental_metrics:  saved.data.fundamental_metrics || "",
      trade_history_summary: saved.data.trade_history_summary || "",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editMode, saved.data]);

  const drafts = useQuery({
    queryKey: ["trader-process-drafts"],
    queryFn: () => api.traderProcess.listDrafts(),
  });

  const save = useMutation({
    mutationFn: () => api.traderProcess.save(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trader-process"] });
      setForm(EMPTY);                         // clear the composer
      setSuccess("Saved. Your process is on the Saved page.");
      setError(null);
      setTimeout(() => setSuccess(null), 3000);
      // If we came in via ?edit=1, drop the flag so the prefill
      // effect does not re-populate the composer with what we
      // just saved.
      if (editMode) {
        router.replace("/trading/trader-process");
      }
    },
    onError: (e) => {
      setError(getErrorMessage(e, "Save failed"));
      setSuccess(null);
    },
  });

  const generate = useMutation({
    mutationFn: () => api.traderProcess.generate(),
    onSuccess: (d) => {
      setForm({
        daily_scan:           d.daily_scan || "",
        entry_logic:          d.entry_logic || "",
        position_sizing:      d.position_sizing || "",
        exit_logic:           d.exit_logic || "",
        holding_period:       d.holding_period || "",
        research_to_rules:    d.research_to_rules || "",
        chart_overlay:        d.chart_overlay || "",
        no_backtest_rule:     d.no_backtest_rule || "",
        multi_strategy_blend: d.multi_strategy_blend || "",
        rolling:              d.rolling || "",
        stocks_vs_options:    d.stocks_vs_options || "",
        universes:            d.universes || "",
        emotion_removal:      d.emotion_removal || "",
        fundamental_metrics:  d.fundamental_metrics || "",
        trade_history_summary: d.trade_history_summary || "",
      });
      qc.invalidateQueries({ queryKey: ["trader-process-drafts"] });
      setSuccess("Generated. Review the sections, then Save to keep them.");
      setError(null);
      setTimeout(() => setSuccess(null), 5000);
    },
    onError: (e) => {
      setError(getErrorMessage(e, "Generate failed"));
      setSuccess(null);
    },
  });

  const filled = SECTIONS.filter((s) => (form[s.key] || "").trim().length > 0).length;
  const draftCount = drafts.data?.length ?? 0;

  // How many sections the trader has already saved (read-only).
  const savedFilled = saved.data
    ? SECTIONS.filter((s) => (saved.data![s.key] || "").trim().length > 0).length
    : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Trader Process</h1>
          <p className="text-sm text-muted-foreground">
            Compose your process here. {filled} of {SECTIONS.length} sections
            written in this composer. Saving clears the composer — the saved
            document lives on the{" "}
            <Link
              href="/trading/trader-process/saved"
              className="underline hover:text-foreground"
            >
              Saved process
            </Link>{" "}
            page.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Link
            href="/trading/trader-process/saved"
            className="rounded-md border border-border px-3 py-2 text-sm text-muted-foreground hover:bg-muted/30 whitespace-nowrap"
          >
            Saved process ({savedFilled}/{SECTIONS.length}) →
          </Link>
          <Link
            href="/trading/trader-process/drafts"
            className="rounded-md border border-border px-3 py-2 text-sm text-muted-foreground hover:bg-muted/30 whitespace-nowrap"
          >
            Process drafts →
          </Link>
          {draftCount > 0 && (
            <Link
              href="/trading/trader-process/drafts"
              className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-300 hover:bg-amber-500/20 whitespace-nowrap"
            >
              {draftCount} {draftCount === 1 ? "draft" : "drafts"} to review →
            </Link>
          )}
        </div>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">How this page works</CardTitle>
          <CardDescription>
            This composer is blank on load. Write the sections, click
            Save. The document is stored and the composer clears. To edit
            what you saved, open the{" "}
            <Link
              href="/trading/trader-process/saved"
              className="underline hover:text-foreground"
            >
              Saved process
            </Link>{" "}
            page and click Edit — or paste it here and save again (each
            save replaces the whole document).
          </CardDescription>
        </CardHeader>
      </Card>

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
              placeholder="Write this section. Be specific — name the instrument, the threshold, the reason."
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
      {success && (
        <p className="text-sm text-green-400 border border-green-500/30 rounded-md px-3 py-2 bg-green-500/5">
          {success}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <Button
          variant="outline"
          onClick={() => generate.mutate()}
          disabled={generate.isPending || save.isPending}
        >
          {generate.isPending ? "Generating… (up to 30s)" : "Generate"}
        </Button>
        <Button
          variant="outline"
          onClick={() => setForm(EMPTY)}
          disabled={save.isPending || generate.isPending || filled === 0}
        >
          Clear
        </Button>
        <Button
          onClick={() => save.mutate()}
          disabled={save.isPending || generate.isPending || filled === 0}
        >
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
