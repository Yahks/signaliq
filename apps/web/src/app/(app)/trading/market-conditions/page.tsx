"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
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
  {
    key: "what_i_track",
    label: "What I track",
    hint: "The list of things you actually measure. Candidate count, realized volatility, median spread, average slippage, average daily range, pairwise correlation of positions.",
    rows: 5,
  },
  {
    key: "baselines",
    label: "Baselines",
    hint: "What 'normal' looks like. Candidate count 5-7/day. Realized vol mid-range. Spread ~1 bp. Range ~1.5%. Correlation ~0.3.",
    rows: 4,
  },
  {
    key: "regimes",
    label: "Regimes I recognize",
    hint: "Compressed volatility: bottom quartile of realized vol, ranges down 20%+. Expanded volatility: top quartile, ranges up. Thin liquidity: spreads 2x+. High correlation: pairwise >0.5. Narrow breadth: <40% above the 50-day.",
    rows: 6,
  },
  {
    key: "thresholds",
    label: "Thresholds",
    hint: "The specific numbers that trigger a regime change. Realized vol bottom quartile = compressed. Pairwise correlation >0.5 = high. Spread widening past 2 bp = thin.",
    rows: 4,
  },
  {
    key: "adjustments",
    label: "Process adjustments",
    hint: "What you do when each regime arrives. Compressed vol: no filter loosening, reduce size. High correlation: fewer positions, cap factor exposure. Thin liquidity: smaller orders, limit orders.",
    rows: 6,
  },
  {
    key: "mechanisms",
    label: "Mechanisms I understand",
    hint: "Why the state changes. Volatility clusters. Liquidity is a function of participation. Correlation rises in drawdowns. Breadth narrows before declines. Range compression precedes expansion.",
    rows: 5,
  },
];

const EMPTY: MarketConditions = {
  what_i_track: "",
  baselines: "",
  regimes: "",
  thresholds: "",
  adjustments: "",
  mechanisms: "",
};

export default function MarketConditionsPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState<MarketConditions>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["market-conditions"],
    queryFn: () => api.marketConditions.get(),
  });

  const save = useMutation({
    mutationFn: () => api.marketConditions.save(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["market-conditions"] });
      qc.invalidateQueries({ queryKey: ["market-conditions-list"] });
      setForm(EMPTY);                    // ← clear the composer
      setSuccess("Saved. Your market conditions are on the Saved page.");
      setError(null);
      setTimeout(() => setSuccess(null), 3000);
    },
    onError: (e) => {
      setError(getErrorMessage(e, "Save failed"));
      setSuccess(null);
    },
  });

  const generate = useMutation({
    mutationFn: () => api.marketConditions.generate(),
    onSuccess: (d) => {
      setForm({
        what_i_track: d.what_i_track || "",
        baselines:    d.baselines    || "",
        regimes:      d.regimes      || "",
        thresholds:   d.thresholds   || "",
        adjustments:  d.adjustments  || "",
        mechanisms:   d.mechanisms   || "",
      });
      qc.invalidateQueries({ queryKey: ["market-conditions-drafts"] });
      setSuccess("Generated. Review the sections, then Save to keep them.");
      setError(null);
      setTimeout(() => setSuccess(null), 5000);
    },
    onError: (e) => {
      setError(getErrorMessage(e, "Generate failed"));
      setSuccess(null);
    },
  });

  const filled = SECTIONS.filter(
    (s) => (form[s.key] || "").trim().length > 0,
  ).length;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Market Conditions</h1>
          <p className="text-sm text-muted-foreground">
            What you track, your baselines, the regimes you recognize.
            {" "}
            {filled} of {SECTIONS.length} sections filled. This is the
            source for every market-regime article.
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            <Link
              href="/trading/market-conditions/saved"
              className="underline hover:text-foreground"
            >
              Saved market conditions →
            </Link>
            {" · "}
            <Link
              href="/trading/market-conditions/drafts"
              className="underline hover:text-foreground"
            >
              Drafts →
            </Link>
          </p>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">How this is used</CardTitle>
          <CardDescription>
            The four market subblocks (market_analysis, market_condition,
            market_signals) read this block as the source of every number
            and every process adjustment in their articles. Click Generate
            to draft a version from your trader process, or write it
            yourself.
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
              value={form[s.key]}
              onChange={(e) =>
                setForm({ ...form, [s.key]: e.target.value })
              }
              placeholder={s.hint}
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
