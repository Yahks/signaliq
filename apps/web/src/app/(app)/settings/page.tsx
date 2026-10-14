"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

function AccountNameCard() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["account-name"],
    queryFn: api.getAccountName,
  });

  const [draft, setDraft] = useState<string>("");
  const [touched, setTouched] = useState(false);

  const current = q.data?.account_name ?? "";
  const value = touched ? draft : current;

  const m = useMutation({
    mutationFn: (name: string) => api.setAccountName(name),
    onSuccess: () => {
      setTouched(false);
      qc.invalidateQueries({ queryKey: ["account-name"] });
      qc.invalidateQueries({ queryKey: ["trading-performance"] });
    },
  });

  const dirty = value.trim() !== current && value.trim().length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Trade account</CardTitle>
        <CardDescription>
          Name the account your trade log represents. Shown on the
          performance page and in every article&apos;s performance
          sentence.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex gap-2 items-center">
          <input
            type="text"
            value={value}
            onChange={(e) => {
              setTouched(true);
              setDraft(e.target.value);
            }}
            placeholder={q.isLoading ? "Loading…" : "e.g. John's personal trade account"}
            className="border border-border rounded-md px-3 py-1.5 text-sm w-80 bg-transparent focus:outline-none focus:ring-1 focus:ring-foreground/30"
            maxLength={120}
          />
          <button
            type="button"
            disabled={!dirty || m.isPending}
            onClick={() => m.mutate(value.trim())}
            className="text-sm px-3 py-1.5 rounded-md bg-muted hover:bg-muted/80 disabled:opacity-40 transition"
          >
            {m.isPending ? "Saving…" : "Save"}
          </button>
          {m.isSuccess && !dirty && (
            <span className="text-xs text-green-500">Saved</span>
          )}
          {m.isError && (
            <span className="text-xs text-red-500">Failed to save</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ScheduleCard() {
  const qc = useQueryClient();
  const avail = useQuery({
    queryKey: ["subblocks-available"],
    queryFn: api.listAvailableSubblocks,
  });
  const sched = useQuery({
    queryKey: ["schedule"],
    queryFn: api.getSchedule,
  });

  // Local edit state. Starts as the loaded schedule, merges when
  // the query resolves.
  type Cfg = { enabled: boolean; interval_hours: number };
  const [draft, setDraft] = useState<Record<string, Cfg> | null>(null);

  useEffect(() => {
    if (!sched.data) return;
    // Merge server state with every available subblock (default to
    // disabled for ones not in the schedule yet).
    const merged: Record<string, Cfg> = {};
    for (const sb of avail.data ?? []) {
      const existing = sched.data.subblocks[sb.slug];
      merged[sb.slug] = existing ?? { enabled: false, interval_hours: 6 };
    }
    setDraft(merged);
  }, [sched.data, avail.data]);

  const m = useMutation({
    mutationFn: (payload: Record<string, Cfg>) => api.setSchedule(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schedule"] });
      qc.invalidateQueries({ queryKey: ["subblocks-available"] });
    },
  });

  const rows = avail.data ?? [];
  const working = draft ?? {};

  const update = (slug: string, patch: Partial<Cfg>) => {
    setDraft({
      ...working,
      [slug]: { ...working[slug], ...patch } as Cfg,
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Article schedule</CardTitle>
        <CardDescription>
          Each subblock runs on its own cadence. Toggle the ones you
          want, set the hours between runs. Minimum 2 hours.
          The scheduler checks every minute and fires the ones whose
          interval has elapsed.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No subblocks available.
          </p>
        ) : (
          <div className="space-y-2">
            <div className="grid grid-cols-[1fr_auto_auto] gap-3 text-xs text-muted-foreground uppercase tracking-wider pb-2 border-b border-border">
              <span>Subblock</span>
              <span className="w-24 text-center">Enabled</span>
              <span className="w-32 text-center">Run every</span>
            </div>

            {rows.map((sb) => {
              const cfg = working[sb.slug] ?? {
                enabled: false,
                interval_hours: 6,
              };
              const nextIso = sched.data?.next_fire?.[sb.slug];
              return (
                <div
                  key={sb.slug}
                  className="grid grid-cols-[1fr_auto_auto] gap-3 items-center py-2 border-b border-border/50 last:border-0"
                >
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">
                      {sb.name}
                    </div>
                    {sb.description ? (
                      <div className="text-xs text-muted-foreground truncate">
                        {sb.description}
                      </div>
                    ) : null}
                    {cfg.enabled && nextIso ? (
                      <div className="text-[10px] text-muted-foreground mt-0.5">
                        Next: {new Date(nextIso).toLocaleString()}
                      </div>
                    ) : null}
                  </div>

                  <div className="w-24 flex justify-center">
                    <input
                      type="checkbox"
                      checked={cfg.enabled}
                      onChange={(e) =>
                        update(sb.slug, { enabled: e.target.checked })
                      }
                    />
                  </div>

                  <div className="w-32 flex items-center gap-2">
                    <input
                      type="number"
                      min={2}
                      max={168}
                      value={cfg.interval_hours}
                      disabled={!cfg.enabled}
                      onChange={(e) =>
                        update(sb.slug, {
                          interval_hours: Math.max(
                            2,
                            Math.min(168, parseInt(e.target.value) || 2),
                          ),
                        })
                      }
                      className="border border-border rounded-md px-2 py-1 w-16 bg-transparent disabled:opacity-40 text-sm"
                    />
                    <span className="text-xs text-muted-foreground">hrs</span>
                  </div>
                </div>
              );
            })}

            <div className="flex justify-end pt-3">
              <button
                type="button"
                disabled={m.isPending || draft === null}
                onClick={() => m.mutate(working)}
                className="text-sm px-4 py-1.5 rounded-md bg-muted hover:bg-muted/80 disabled:opacity-40 transition"
              >
                {m.isPending ? "Saving…" : "Save"}
              </button>
            </div>

            {m.isError ? (
              <p className="text-sm text-red-400 mt-2">
                {getErrorMessage(m.error, "Save failed")}
              </p>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}


export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Manage your organisation, API keys, and automation preferences.
        </p>
      </div>

      <AccountNameCard />

      <ScheduleCard />

      <Card>
        <CardHeader>
          <CardTitle>API Credentials (BYOK)</CardTitle>
          <CardDescription>
            Bring your own LLM provider keys. Every generation uses your key —
            the platform never shares keys between users.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Link href="/settings/credentials" className="text-sm underline">
            Manage API credentials →
          </Link>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Block automation</CardTitle>
          <CardDescription>
            Pause automatic generation for a block. Existing content stays visible.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Link href="/dashboard" className="text-sm underline">
            Block toggles are on the dashboard →
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
