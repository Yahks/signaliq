"use client";

import {useState, useEffect} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const PROVIDERS = [
  { value: "groq",       label: "Groq" },
  { value: "gemini",     label: "Google Gemini" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "ollama",     label: "Ollama (local — no key)" },
];

// S.6.4.2 — per-row status. Recomputes every second while the tab is
// open so the "rate-limited · 8m 12s" badge ticks down.
function useNow(intervalMs: number = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function fmtCountdown(msRemaining: number): string {
  if (msRemaining <= 0) return "";
  const s = Math.ceil(msRemaining / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

type KeyStatus = {
  kind: "available" | "rate_limited" | "paused";
  text: string;
  className: string;
};

function fmtRelative(iso: string | null, nowMs: number): string {
  if (!iso) return "\u2014";
  const ms = nowMs - new Date(iso).getTime();
  if (ms < 60_000) return "just now";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function fmtTokens(n: number | null): string {
  if (n == null) return "\u2014";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function statusFor(c: { is_active: boolean; exhausted_until: string | null }, nowMs: number): KeyStatus {
  if (!c.is_active) {
    return {
      kind: "paused",
      text: "paused",
      className: "rounded-md px-2 py-0.5 text-[11px] bg-muted text-muted-foreground border border-border",
    };
  }
  if (c.exhausted_until) {
    const until = new Date(c.exhausted_until).getTime();
    const remaining = until - nowMs;
    if (remaining > 0) {
      return {
        kind: "rate_limited",
        text: `rate-limited · ${fmtCountdown(remaining)}`,
        className: "rounded-md px-2 py-0.5 text-[11px] bg-amber-500/15 text-amber-400 border border-amber-500/30",
      };
    }
  }
  return {
    kind: "available",
    text: "available",
    className: "rounded-md px-2 py-0.5 text-[11px] bg-green-500/15 text-green-400 border border-green-500/30",
  };
}

export default function CredentialsPage() {
  const qc = useQueryClient();
  const nowMs = useNow(1000);
  const [showForm, setShowForm] = useState(false);
  const [provider, setProvider] = useState("groq");
  const [apiKey, setApiKey] = useState("");
  // S.6.4.1d — optional user-supplied label. Empty string means
  // "let the backend derive {provider}-{key_hash4}".
  const [label, setLabel] = useState("");
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failCount, setFailCount] = useState(0);
  const [showSupport, setShowSupport] = useState(false);
  const [supportMsg, setSupportMsg] = useState("");

  const creds = useQuery({
    queryKey: ["credentials"],
    queryFn: () => api.listCredentials(),
  });

  const add = useMutation({
    mutationFn: () =>
      api.addCredential({
        provider,
        api_key: apiKey,
        label: label.trim() || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["credentials"] });
      setApiKey("");
      setLabel("");
      setShowForm(false);
      setSuccess("API key verified — successfully added");
      setError(null);
      setFailCount(0);
      setTimeout(() => setSuccess(null), 5000);
    },
    onError: (e) => {
      setError(getErrorMessage(e, "Failed to add the credential"));
      setSuccess(null);
      setFailCount((n) => n + 1);
    },
  });

  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api.toggleCredential(id, is_active),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteCredential(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });

  // S.6.4.2 — manual reset of a rate-limit cooldown
  const reset = useMutation({
    mutationFn: (id: string) => api.resetCredential(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["credentials"] });
      setSuccess("Cooldown cleared — the key is available again.");
      setTimeout(() => setSuccess(null), 4000);
    },
    onError: (e) => setError(getErrorMessage(e, "Failed to reset")),
  });

  const support = useMutation({
    mutationFn: (message: string) => api.requestSupport(message),
    onSuccess: () => {
      setShowSupport(false);
      setSupportMsg("");
      setSuccess("Support request sent. Admin will follow up.");
      setTimeout(() => setSuccess(null), 5000);
    },
  });

  const items = creds.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">API credentials</h1>
          <p className="mt-1 text-sm text-muted-foreground max-w-2xl">
            Bring your own LLM provider keys. Every generation uses your key —
            usage is billed to your provider account. Keys are encrypted at rest
            and only ever shown masked.
          </p>
        </div>
        <Button onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "Add API key"}
        </Button>
      </div>

      {success && (
        <div className="rounded-md border border-green-500/30 bg-green-500/5 p-3 text-sm text-green-400">
          ✓ {success}
        </div>
      )}

      {showForm && (
        <Card>
          <CardHeader>
            <CardTitle>Add an API key</CardTitle>
            <CardDescription>
              Pick your provider, paste your key, and click Save. We&apos;ll
              verify the key with the provider before storing it.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium">Provider</label>
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground [color-scheme:dark]"
              >
                {PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}
                          className="bg-background text-foreground">
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium">
                Label <span className="text-muted-foreground">(optional)</span>
              </label>
              <Input
                type="text"
                placeholder="My Groq key"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Give this key a name if you&apos;ll have more than one from
                the same provider. Leave blank for an auto-generated name.
              </p>
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium">API key</label>
              <Input
                type="password"
                placeholder="Paste your API key here"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
              <p className="mt-1 text-xs text-muted-foreground">
                We&apos;ll verify it with the provider before saving. The key
                will never be shown again in full.
              </p>
            </div>

            {error && (
              <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">
                {error}
              </div>
            )}

            <div className="flex gap-2">
              <Button
                onClick={() => add.mutate()}
                disabled={add.isPending || apiKey.length < 8}
              >
                {add.isPending ? "Checking your API key…" : "Save"}
              </Button>
              {failCount >= 2 && (
                <Button variant="outline" onClick={() => setShowSupport(true)}>
                  Contact admin
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {showSupport && (
        <Card>
          <CardHeader>
            <CardTitle>Contact administrator</CardTitle>
            <CardDescription>
              Describe what&apos;s happening. The admin will see this request
              and follow up.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <textarea
              value={supportMsg}
              onChange={(e) => setSupportMsg(e.target.value)}
              placeholder="I tried to add my Groq key but it keeps getting rejected…"
              className="w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm min-h-[100px]"
            />
            <div className="flex gap-2">
              <Button
                onClick={() => support.mutate(supportMsg)}
                disabled={supportMsg.length < 10 || support.isPending}
              >
                {support.isPending ? "Sending…" : "Send request"}
              </Button>
              <Button variant="outline" onClick={() => setShowSupport(false)}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Your keys</CardTitle>
          <CardDescription>
            {items.length} key{items.length === 1 ? "" : "s"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {creds.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No API keys yet. Add one above to enable generation.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                    <th className="text-left py-2 pr-3 font-medium">Label</th>
                    <th className="text-left py-2 pr-3 font-medium">Provider</th>
                    <th className="text-left py-2 pr-3 font-medium">Roles served</th>
                    <th className="text-left py-2 pr-3 font-medium">Key</th>
                    <th className="text-right py-2 pr-3 font-medium">Groq tokens</th>
                    <th className="text-right py-2 pr-3 font-medium">Groq remaining</th>
                    <th className="text-left py-2 pr-3 font-medium">Status</th>
                    <th className="text-right py-2 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((c) => (
                    <tr key={c.id} className="border-b border-border/40 last:border-0">
                      <td className="py-2 pr-3 font-medium">{c.label}</td>
                      <td className="py-2 pr-3 text-muted-foreground">{c.provider}</td>
                      <td className="py-2 pr-3 text-muted-foreground">
                        {(c.roles || []).join(", ") || "—"}
                      </td>
                      <td className="py-2 pr-3 font-mono text-xs">{c.key_preview}</td>
                      <td className="py-2 pr-3 text-right font-mono text-xs">
                        {fmtTokens(c.tokens_used)}
                        <div className="text-[10px] text-muted-foreground">
                          {c.calls_made} call{c.calls_made === 1 ? "" : "s"}
                        </div>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono text-xs">
                        {fmtTokens(c.last_remaining_tokens)}
                        <div className="text-[10px] text-muted-foreground">
                          {fmtRelative(c.last_rate_at, nowMs)}
                        </div>
                      </td>
                      <td className="py-2 pr-3">
                        {(() => {
                          const s = statusFor(c, nowMs);
                          return <span className={s.className}>{s.text}</span>;
                        })()}
                      </td>
                      <td className="py-2 text-right space-x-2">
                        <button
                          onClick={() => toggle.mutate({ id: c.id, is_active: !c.is_active })}
                          className="text-xs underline text-muted-foreground hover:text-foreground"
                        >
                          {c.is_active ? "pause" : "resume"}
                        </button>
                        {statusFor(c, nowMs).kind === "rate_limited" && (
                          <button
                            onClick={() => {
                              if (confirm(`Clear the rate-limit cooldown for "${c.label}"?`)) {
                                reset.mutate(c.id);
                              }
                            }}
                            className="text-xs underline text-amber-400 hover:text-amber-300"
                          >
                            reset
                          </button>
                        )}
                        <button
                          onClick={() => {
                            if (confirm(`Delete credential "${c.label}"?`)) remove.mutate(c.id);
                          }}
                          className="text-xs underline text-destructive hover:text-red-300"
                        >
                          delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
