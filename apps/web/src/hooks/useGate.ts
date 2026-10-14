"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Shared generation gate.
 *
 * Answers: "can the current user generate content right now?"
 *
 *   outOfTokens    — site token balance is 0 (must top up)
 *   noCredentials  — no BYOK API key configured (must add one)
 *   canGenerate    — both false; safe to enable a Generate button
 *   reason         — the blocking reason, in priority order
 *   tooltip        — user-facing string for a disabled button
 *
 * TanStack Query dedupes by key, so calling this on multiple pages
 * costs one balance fetch + one credentials fetch per 30s.
 */
export type GateState = {
  outOfTokens: boolean;
  noCredentials: boolean;
  canGenerate: boolean;
  reason: "out_of_tokens" | "no_credentials" | null;
  tooltip: string | undefined;
};

export function useGate(): GateState {
  const balance = useQuery({
    queryKey: ["token-balance"],
    queryFn: () => api.getTokenBalance(),
    refetchInterval: 30_000,
  });

  const creds = useQuery({
    queryKey: ["credentials"],
    queryFn: () => api.listCredentials(),
    refetchInterval: 30_000,
  });

  // While loading, assume OK — avoids a flash of "no key" for users
  // who do have one.
  const outOfTokens = (balance.data?.balance ?? 1) <= 0;
  const noCredentials = (creds.data?.items.length ?? 1) === 0;

  const reason: GateState["reason"] = outOfTokens
    ? "out_of_tokens"
    : noCredentials
    ? "no_credentials"
    : null;

  return {
    outOfTokens,
    noCredentials,
    canGenerate: reason === null,
    reason,
    tooltip:
      reason === "out_of_tokens"
        ? "Out of tokens — top up to continue"
        : reason === "no_credentials"
        ? "Add an API key first — go to /settings/credentials"
        : undefined,
  };
}
