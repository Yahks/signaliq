"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

function fmtUSD(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n > 0) return `$${n.toFixed(4)}`;
  return "$0.00";
}

export function TokenBadge() {
  const q = useQuery({
    queryKey: ["token-balance"],
    queryFn: () => api.getTokenBalance(),
    refetchInterval: 30_000,
  });

  if (!q.data) return null;

  const balance = q.data.balance;
  // Fallback: if the API hasn't been restarted since the schema change,
  // balance_usd may be undefined. Compute from the constant.
  const balanceUsd = q.data.balance_usd ?? (balance / 1_000_000);
  const isZero = balance <= 0;
  const isLow = balance > 0 && balance < 10_000;

  const cls = isZero
    ? "bg-red-500/15 text-red-400 border border-red-500/30"
    : isLow
    ? "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30"
    : "bg-muted text-muted-foreground border border-border";

  const title = isZero
    ? "Out of tokens — generation paused. Contact admin to top up."
    : `${balance.toLocaleString()} tokens remaining (${fmtUSD(balanceUsd)})`;

  return (
    <Link
      href="/dashboard"
      className={`rounded-md px-2 py-0.5 font-mono text-xs ${cls}`}
      title={title}
    >
      {fmtTokens(balance)} tokens · {fmtUSD(balanceUsd)}
    </Link>
  );
}
