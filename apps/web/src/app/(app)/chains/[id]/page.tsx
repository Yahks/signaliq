"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { api, type ImpactResponse, type ScenarioResponse, type WatchlistItemResponse, type ChainUpdateResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { FollowButton } from "@/components/chain/follow-button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

// --- helpers ---

function linkColor(t: string): string {
  switch (t) {
    case "contradicts": return "text-red-400 bg-red-500/10 border-red-500/30";
    case "escalates": return "text-orange-400 bg-orange-500/10 border-orange-500/30";
    case "responds_to": return "text-blue-400 bg-blue-500/10 border-blue-500/30";
    case "contextualizes": return "text-purple-400 bg-purple-500/10 border-purple-500/30";
    case "causes": return "text-green-400 bg-green-500/10 border-green-500/30";
    case "corroborates": return "text-teal-400 bg-teal-500/10 border-teal-500/30";
    case "correlates_with": return "text-cyan-400 bg-cyan-500/10 border-cyan-500/30";
    case "contrasts": return "text-pink-400 bg-pink-500/10 border-pink-500/30";
    case "explains": return "text-indigo-400 bg-indigo-500/10 border-indigo-500/30";
    case "compares": return "text-violet-400 bg-violet-500/10 border-violet-500/30";
    case "teaches": return "text-yellow-400 bg-yellow-500/10 border-yellow-500/30";
    case "warns": return "text-amber-400 bg-amber-500/10 border-amber-500/30";
    default: return "text-muted-foreground bg-muted border-border";
  }
}

function statusBadge(s: string): string {
  switch (s) {
    case "fact": return "bg-green-500/20 text-green-400";
    case "claim": return "bg-yellow-500/20 text-yellow-400";
    case "rumor": return "bg-orange-500/20 text-orange-400";
    case "disputed": return "bg-red-500/20 text-red-400";
    default: return "bg-muted text-muted-foreground";
  }
}

function directionColor(d: string | null): string {
  switch (d) {
    case "up": return "text-green-400";
    case "down": return "text-red-400";
    case "mixed": return "text-yellow-400";
    case "uncertain": return "text-muted-foreground";
    default: return "text-muted-foreground";
  }
}

function directionArrow(d: string | null): string {
  switch (d) {
    case "up": return "↑";
    case "down": return "↓";
    case "mixed": return "↕";
    case "uncertain": return "?";
    default: return "·";
  }
}

function strengthBadge(s: string | null): string {
  switch (s) {
    case "strong": return "bg-red-500/20 text-red-300";
    case "moderate": return "bg-yellow-500/20 text-yellow-300";
    case "weak": return "bg-blue-500/20 text-blue-300";
    default: return "bg-muted text-muted-foreground";
  }
}

function chainStatusBadge(s: string): string {
  switch (s) {
    case "ready": return "bg-green-500/15 text-green-400 border border-green-500/30";
    case "developing": return "bg-blue-500/15 text-blue-400 border border-blue-500/30";
    case "failed": return "bg-red-500/15 text-red-400 border border-red-500/30";
    case "draft": return "bg-muted text-muted-foreground border border-border";
    default: return "bg-muted text-muted-foreground border border-border";
  }
}

export default function ChainDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const chain = useQuery({
    queryKey: ["chain", params.id],
    queryFn: () => api.getChain(params.id),
    refetchInterval: 30_000,
  });

  const qc = useQueryClient();
  // S.7.6d.2 — move the article to trash. Reversible; no confirm.
  const deleteArticle = useMutation({
    mutationFn: (id: string) => api.deleteNewsPost(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["chain", params.id] });
    },
  });

  if (chain.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (chain.isError || !chain.data) return <p className="text-sm text-destructive">Chain not found</p>;

  const { chain: c, events, links, post, evidence, impacts, scenarios, watchlist, updates } = chain.data;
  const byId = Object.fromEntries(events.map((e) => [e.id, e]));
  const sortedLinks = [...links].sort((a, b) => a.sequence - b.sequence);

  return (
    <div className="space-y-6">
      {/* === Header === */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className={`rounded-md px-2 py-0.5 text-xs ${chainStatusBadge(c.status)}`}>
              {c.status}
            </span>
            {(c.update_count ?? 0) > 0 && (
              <span className="rounded-md bg-blue-500/15 text-blue-400 border border-blue-500/30 px-2 py-0.5 text-xs">
                {c.update_count} update{c.update_count === 1 ? "" : "s"}
              </span>
            )}
            {c.subtopic_slug && (
              <span className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                {c.subtopic_slug}
              </span>
            )}
          </div>
          <h1 className="text-2xl font-semibold">{c.title}</h1>
          {c.thesis && <p className="text-sm text-muted-foreground">{c.thesis}</p>}
          {c.last_updated_at && (
            <p className="text-xs text-muted-foreground">
              Last updated {new Date(c.last_updated_at).toLocaleString()}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <FollowButton chainId={params.id} />
          <Button variant="outline" onClick={() => router.back()}>Back</Button>
        </div>
      </div>

      {/* === Live updates banner === */}
      {updates.length > 0 && (
        <Card className="border-blue-500/30 bg-blue-500/5">
          <CardHeader>
            <CardTitle className="text-base">📡 This story is developing</CardTitle>
            <CardDescription>
              {updates.length} event{updates.length === 1 ? "" : "s"} joined this chain since it was first built.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {/* === The chain === */}
      <Card>
        <CardHeader>
          <CardTitle>Causal chain ({events.length} events, {links.length} links)</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {sortedLinks[0] && byId[sortedLinks[0].from_event_id] && (
              <EventCard event={byId[sortedLinks[0].from_event_id]} statusBadge={statusBadge} />
            )}
            {sortedLinks.map((link) => {
              const toEvent = byId[link.to_event_id];
              if (!toEvent) return null;
              return (
                <div key={`${link.sequence}-${link.to_event_id}`}>
                  <div className="ml-6 my-2 flex items-start gap-2">
                    <div className={`rounded-md border px-2 py-1 text-xs ${linkColor(link.link_type)}`}>
                      {link.link_type.toUpperCase()}
                    </div>
                    <div className="text-xs text-muted-foreground italic pt-1">{link.rationale}</div>
                  </div>
                  <EventCard event={toEvent} statusBadge={statusBadge} />
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* === Impacts + Watchlist side by side === */}
      <div className="grid gap-4 lg:grid-cols-2">
        {impacts.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>What this could affect</CardTitle>
              <CardDescription>{impacts.length} impacts identified</CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="divide-y divide-border">
                {impacts.map((imp: ImpactResponse) => (
                  <li key={imp.id} className="py-2">
                    <div className="flex items-center gap-2">
                      <span className={`text-lg font-mono ${directionColor(imp.direction)}`}>
                        {directionArrow(imp.direction)}
                      </span>
                      <span className="font-medium">{imp.label}</span>
                      <span className="text-xs text-muted-foreground">({imp.category})</span>
                      {imp.strength && (
                        <span className={`rounded px-1.5 py-0.5 text-xs ${strengthBadge(imp.strength)}`}>
                          {imp.strength}
                        </span>
                      )}
                    </div>
                    {imp.reasoning && (
                      <p className="mt-1 ml-6 text-xs text-muted-foreground">{imp.reasoning}</p>
                    )}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {watchlist.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Monitor this chain</CardTitle>
              <CardDescription>{watchlist.length} indicators to watch</CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="divide-y divide-border">
                {watchlist.map((w: WatchlistItemResponse) => {
                  const q = w.live_quote ?? {};
                  const hasPrice = q.current_price != null;
                  const changePct = q.daily_change_pct ?? null;
                  const changeColor =
                    changePct == null ? "text-muted-foreground" :
                    changePct > 0.05 ? "text-green-400" :
                    changePct < -0.05 ? "text-red-400" :
                    "text-muted-foreground";
                  return (
                    <li key={w.id} className="py-2">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="font-medium truncate">{w.label}</span>
                          {w.symbol && (
                            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs whitespace-nowrap">
                              {w.symbol}
                            </span>
                          )}
                        </div>
                        {hasPrice && (
                          <div className="flex items-center gap-2 text-sm whitespace-nowrap">
                            <span className="font-mono">${q.current_price!.toFixed(2)}</span>
                            {changePct != null && (
                              <span className={`text-xs font-medium ${changeColor}`}>
                                {changePct > 0 ? "+" : ""}
                                {changePct.toFixed(2)}%
                              </span>
                            )}
                          </div>
                        )}
                        {!hasPrice && w.symbol && (
                          <span className="text-xs text-muted-foreground whitespace-nowrap">
                            no data
                          </span>
                        )}
                      </div>
                      {w.reasoning && <p className="mt-1 text-xs text-muted-foreground">{w.reasoning}</p>}
                      {hasPrice && q.vol_30d_pct != null && (
                        <p className="mt-0.5 text-[11px] text-muted-foreground">
                          30d vol: {q.vol_30d_pct.toFixed(2)}%
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>

      {/* === Scenarios === */}
      {scenarios.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Scenarios</CardTitle>
            <CardDescription>{scenarios.length} conditional futures</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              {scenarios.map((s: ScenarioResponse) => {
                const q = s.quantified ?? {};
                const hasNumbers =
                  q.primary_symbol != null &&
                  q.expected_move_low_pct != null &&
                  q.expected_move_high_pct != null;
                const confidenceColor =
                  q.confidence === "high" ? "text-green-400" :
                  q.confidence === "medium" ? "text-yellow-400" :
                  "text-muted-foreground";
                return (
                  <div key={s.id} className="rounded-md border border-border p-3 space-y-2 flex flex-col">
                    <div>
                      <h3 className="font-medium">{s.name}</h3>
                      <p className="text-xs text-muted-foreground italic mt-1">{s.condition}</p>
                    </div>

                    {/* Quantified block */}
                    {hasNumbers && (
                      <div className="rounded-md bg-muted/40 p-2 space-y-1">
                        <div className="flex items-center gap-2 text-xs">
                          <span className="font-mono font-medium">{q.primary_symbol}</span>
                          {q.current_price != null && (
                            <span className="text-muted-foreground">${q.current_price.toFixed(2)}</span>
                          )}
                        </div>
                        <div className="text-sm font-medium">
                          Expected move:{" "}
                          <span className={q.expected_move_low_pct! >= 0 ? "text-green-400" : q.expected_move_high_pct! <= 0 ? "text-red-400" : "text-yellow-400"}>
                            {q.expected_move_low_pct! >= 0 ? "+" : ""}
                            {q.expected_move_low_pct!.toFixed(1)}%
                            {" to "}
                            {q.expected_move_high_pct! >= 0 ? "+" : ""}
                            {q.expected_move_high_pct!.toFixed(1)}%
                          </span>
                        </div>
                        {q.vol_30d_pct != null && (
                          <div className="text-xs text-muted-foreground">
                            30d vol: {q.vol_30d_pct.toFixed(2)}% · confidence:{" "}
                            <span className={confidenceColor}>{q.confidence}</span>
                          </div>
                        )}
                        {q.basis && (
                          <p className="text-[11px] text-muted-foreground leading-tight pt-1">
                            {q.basis}
                          </p>
                        )}
                        {q.notes && q.notes.length > 0 && (
                          <ul className="text-[11px] text-muted-foreground list-disc pl-4 pt-1 space-y-0.5">
                            {q.notes.map((n, i) => <li key={i}>{n}</li>)}
                          </ul>
                        )}
                      </div>
                    )}

                    {!hasNumbers && (
                      <div className="rounded-md bg-muted/30 p-2">
                        <p className="text-[11px] text-muted-foreground">
                          No market data available for this scenario
                          {s.candidate_symbols?.length > 0 && (
                            <> (tried: {s.candidate_symbols.join(", ")})</>
                          )}
                        </p>
                      </div>
                    )}

                    <p className="text-sm flex-1">{s.reasoning}</p>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* === Generated article === */}
      {post && (
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <CardTitle>Generated article ({post.word_count} words · {post.status})</CardTitle>
              <Button
                variant="ghost"
                size="sm"
                title="Move to trash"
                onClick={() => deleteArticle.mutate(post.id)}
                disabled={deleteArticle.isPending}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div className="prose prose-invert prose-sm max-w-none whitespace-pre-wrap">
              <h2 className="text-lg font-semibold mb-3">{post.title}</h2>
              <div className="text-sm leading-relaxed">{post.body}</div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* === Live updates feed === */}
      {updates.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Update log ({updates.length})</CardTitle>
            <CardDescription>New events that joined this chain</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-3">
              {updates.map((u: ChainUpdateResponse) => (
                <li key={u.id} className="rounded-md border border-border p-3">
                  <div className="flex items-start justify-between gap-3">
                    <Link href={`/records/${u.event_id}`} className="font-medium text-sm hover:underline">
                      {u.event_headline}
                    </Link>
                    <div className="flex items-center gap-2 text-xs whitespace-nowrap">
                      <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-muted-foreground">
                        sim {u.similarity.toFixed(2)}
                      </span>
                      <span className="text-muted-foreground">
                        {new Date(u.created_at).toLocaleDateString()}
                      </span>
                    </div>
                  </div>
                  {u.reasoning && (
                    <p className="mt-1 text-xs text-muted-foreground italic">{u.reasoning}</p>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* === Evidence === */}
      {evidence.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Evidence map ({evidence.length} claims)</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {evidence.slice(0, 30).map((e, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className={`rounded px-2 py-0.5 text-xs whitespace-nowrap ${
                    e.claim_type === "fact" ? "bg-green-500/20 text-green-400" :
                    e.claim_type === "inference" ? "bg-blue-500/20 text-blue-400" :
                    "bg-muted text-muted-foreground"
                  }`}>{e.claim_type}</span>
                  <span className="line-clamp-2 text-muted-foreground">{e.claim_text}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function EventCard({ event, statusBadge }: { event: any; statusBadge: (s: string) => string }) {
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium">{event.headline}</p>
        <span className={`rounded px-2 py-0.5 text-xs whitespace-nowrap ${statusBadge(event.status)}`}>
          {event.status}
        </span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground line-clamp-3">{event.summary}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {event.occurred_at && <span>{new Date(event.occurred_at).toLocaleString()}</span>}
        {event.location && <span>· {event.location}</span>}
        {event.actors?.length > 0 && <span>· {event.actors.join(", ")}</span>}
      </div>
    </div>
  );
}
