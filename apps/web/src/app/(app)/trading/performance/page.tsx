"use client";

import React, { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type TradeRow = {
  traded_at: string;
  direction: string;
  contracts: number;
  entry: number;
  stop: number | null;
  exit: number;
  result: number;
  equity: number;
};

type Summary = {
  trades: number;
  winners: number;
  losers: number;
  scratches: number;
  net: number;
  ending: number;
  peak: number;
  trough: number;
  max_dd: number;
  max_dd_pct: number;
  account_name: string;
  starting_balance: number;
};

type PerfResponse = {
  summary: Summary;
  trades: TradeRow[];
  monthly: {
    month: string;
    trades: number;
    winners: number;
    losers: number;
    net: number;
    ending_equity: number;
  }[];
};

function fmtUSD(n: number, signed = false): string {
  const sign = signed ? (n >= 0 ? "+" : "-") : "";
  return `${sign}$${Math.abs(Math.round(n)).toLocaleString()}`;
}

function svgToString(svg: SVGSVGElement, bg = "white"): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;

  // Read the viewBox — "0 0 900 320" — and set explicit
  // width/height so the rasterizer knows the real size.
  const vb = (clone.getAttribute("viewBox") || "0 0 900 320")
    .split(/\s+/)
    .map(Number);
  const vw = vb[2] || 900;
  const vh = vb[3] || 320;

  clone.setAttribute("width", String(vw));
  clone.setAttribute("height", String(vh));
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  // Remove the Tailwind class that would impose a different
  // aspect ratio when rasterized.
  clone.removeAttribute("class");
  clone.removeAttribute("style");

  // White background aligned to the viewBox origin.
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("x", String(vb[0] || 0));
  rect.setAttribute("y", String(vb[1] || 0));
  rect.setAttribute("width", String(vw));
  rect.setAttribute("height", String(vh));
  rect.setAttribute("fill", bg);
  clone.insertBefore(rect, clone.firstChild);

  return new XMLSerializer().serializeToString(clone);
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function ChartCard({ accountName }: { accountName: string }) {
  const qc = useQueryClient();
  const [url, setUrl] = React.useState<string | null>(null);

  const gen = useMutation({
    mutationFn: api.generatePerformanceChart,
    onSuccess: (r) => {
      setUrl(r.url);
      qc.invalidateQueries({ queryKey: ["trading-performance"] });
    },
  });

  // Auto-generate on first mount.
  React.useEffect(() => {
    if (!url && !gen.isPending) {
      gen.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>Equity curve</CardTitle>
            <CardDescription>Balance after each trade</CardDescription>
          </div>
          <div className="flex gap-3 text-xs">
            <button
              type="button"
              onClick={() => gen.mutate()}
              disabled={gen.isPending}
              className="text-muted-foreground hover:text-foreground underline disabled:opacity-40"
            >
              {gen.isPending ? "Generating…" : url ? "Regenerate chart" : "Generate chart"}
            </button>
            {url && (
              <a
                href={url}
                download={`${accountName.replace(/\s+/g, "-")}-equity.png`}
                className="text-muted-foreground hover:text-foreground underline"
              >
                Download PNG
              </a>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {gen.isPending && !url && (
          <p className="text-sm text-muted-foreground py-12 text-center">
            Rendering chart…
          </p>
        )}
        {url && (
          <img
            src={url}
            alt="Equity curve"
            className="w-full h-auto rounded-md border border-border"
          />
        )}
        {gen.isError && (
          <p className="text-sm text-red-500 py-4">
            Chart generation failed. Try again in a moment.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export default function TradingPerformancePage() {
  const perf = useQuery<PerfResponse>({
    queryKey: ["trading-performance"],
    queryFn: () => api.tradingPerformance(),
    refetchInterval: 60_000,
  });

  const svgRef = React.useRef<SVGSVGElement>(null);

  if (perf.isLoading || !perf.data) {
    return <p className="text-sm text-muted-foreground">Loading performance…</p>;
  }

  const { summary, trades, monthly } = perf.data;
  const first = trades.length ? trades[0].traded_at.slice(0, 10) : "—";
  const last = trades.length ? trades[trades.length - 1].traded_at.slice(0, 10) : "—";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Performance — {summary.account_name}</h1>
        <p className="text-sm text-muted-foreground">
          Period: {first} – {last} · Starting balance: {fmtUSD(summary.starting_balance)}
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
        <Card><CardContent className="pt-4">
          <p className="text-xs text-muted-foreground uppercase">Trades</p>
          <p className="text-xl font-semibold">{summary.trades}</p>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <p className="text-xs text-muted-foreground uppercase">Winners</p>
          <p className="text-xl font-semibold">{summary.winners}</p>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <p className="text-xs text-muted-foreground uppercase">Losers</p>
          <p className="text-xl font-semibold">{summary.losers}</p>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <p className="text-xs text-muted-foreground uppercase">Ending balance</p>
          <p className="text-xl font-semibold">{fmtUSD(summary.ending)}</p>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <p className="text-xs text-muted-foreground uppercase">Net P&amp;L</p>
          <p className={`text-xl font-semibold ${summary.net >= 0 ? "text-green-500" : "text-red-500"}`}>
            {fmtUSD(summary.net, true)}
          </p>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <p className="text-xs text-muted-foreground uppercase">Max drawdown</p>
          <p className="text-xl font-semibold">{summary.max_dd_pct.toFixed(2)}%</p>
        </CardContent></Card>
      </div>

      <ChartCard accountName={summary.account_name} />

      <Card>
        <CardHeader><CardTitle>Monthly breakdown</CardTitle></CardHeader>
        <CardContent>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs uppercase text-muted-foreground">
                <th className="text-left pb-2">Month</th>
                <th className="text-right pb-2">Trades</th>
                <th className="text-right pb-2">W</th>
                <th className="text-right pb-2">L</th>
                <th className="text-right pb-2">Net P&amp;L</th>
                <th className="text-right pb-2">Ending</th>
              </tr>
            </thead>
            <tbody>
              {monthly.map((m) => (
                <tr key={m.month} className="border-t border-border">
                  <td className="py-2">{m.month}</td>
                  <td className="text-right">{m.trades}</td>
                  <td className="text-right">{m.winners}</td>
                  <td className="text-right">{m.losers}</td>
                  <td className={`text-right ${m.net >= 0 ? "text-green-500" : "text-red-500"}`}>
                    {fmtUSD(m.net, true)}
                  </td>
                  <td className="text-right">{fmtUSD(m.ending_equity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Full trade log</CardTitle></CardHeader>
        <CardContent>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs uppercase text-muted-foreground">
                <th className="text-left pb-2">Date</th>
                <th className="text-left pb-2">Dir</th>
                <th className="text-right pb-2">Ct</th>
                <th className="text-right pb-2">Entry</th>
                <th className="text-right pb-2">Stop</th>
                <th className="text-right pb-2">Exit</th>
                <th className="text-right pb-2">Result</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1.5">{t.traded_at.slice(0, 10)}</td>
                  <td>{t.direction}</td>
                  <td className="text-right">{t.contracts}</td>
                  <td className="text-right">{t.entry}</td>
                  <td className="text-right">{t.stop ?? "—"}</td>
                  <td className="text-right">{t.exit}</td>
                  <td className={`text-right ${t.result >= 0 ? "text-green-500" : "text-red-500"}`}>
                    {fmtUSD(t.result, true)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
