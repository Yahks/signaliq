"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { api, getErrorMessage, type StudioMessageItem } from "@/lib/api";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

// Form and tone options — mirrors the backend's `forms.py` and
// `editorial.py` defaults. Kept short here for a chat strip; the
// full list is available but the common few are enough.
const FORM_OPTIONS = [
  { value: "", label: "Auto (explainer)" },
  { value: "explainer", label: "Explainer" },
  { value: "deep_dive", label: "Deep dive" },
  { value: "opinion", label: "Opinion" },
  { value: "blog_post", label: "Blog post" },
  { value: "news_article", label: "News article" },
  { value: "analytical", label: "Analytical" },
];

const TONE_OPTIONS = [
  { value: "", label: "Auto (neutral)" },
  { value: "neutral", label: "Neutral" },
  { value: "analytical", label: "Analytical" },
  { value: "conversational", label: "Conversational" },
  { value: "warm", label: "Warm" },
  { value: "academic", label: "Academic" },
  { value: "formal", label: "Formal" },
];

const TRACK_OPTIONS = [
  { value: "", label: "No track" },
  { value: "retail", label: "Retail" },
  { value: "quant", label: "Quant" },
];

export default function StudioChatPage() {
  const qc = useQueryClient();
  const [prompt, setPrompt] = useState("");
  const [form, setForm] = useState("");
  const [tone, setTone] = useState("");
  const [track, setTrack] = useState("");
  const [error, setError] = useState<string | null>(null);

  // S.8.9.9 — two surfaces. "chat" is the manual prompt feed (user_id
  // IS NOT NULL). "auto" is the movers feed (user_id IS NULL). Tabs
  // at the top switch between them.
  const [tab, setTab] = useState<"chat" | "auto">("chat");

  // S.8.7.1 — optimistic user messages. When the user hits Send, we
  // push a synthetic StudioMessageItem here immediately so their own
  // message appears in the scrollback without waiting for the
  // 30-60 second round-trip. When the mutation settles (success or
  // error), we drop the synthetic and let the invalidated query
  // supply the real row.
  const [pending, setPending] = useState<StudioMessageItem[]>([]);

  const scrollRef = useRef<HTMLDivElement>(null);

  // S.8.9.18 — only the 5 newest messages.
  const messages = useQuery({
    queryKey: ["studio-messages", "chat", "latest5"],
    queryFn: () => api.getStudioMessages("user", 5),
    refetchOnWindowFocus: false,
  });

  // S.8.9.10 — counts for the two surfaces, shown on the bottom
  // cards. Refetched whenever a message is added or removed.
  const counts = useQuery({
    queryKey: ["studio-message-counts"],
    queryFn: () => api.getStudioMessageCounts(),
    refetchOnWindowFocus: false,
  });

  // Auto-scroll to the newest message.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.data]);

  const send = useMutation({
    mutationFn: (text: string) =>
      api.postStudioMessage({
        prompt: text,
        form: form || null,
        tone: tone || null,
        track: track || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["studio-messages"] });
      qc.invalidateQueries({ queryKey: ["trading-posts"] });
      setError(null);
    },
    onError: (e) => {
      setError(getErrorMessage(e, "Failed to send message"));
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteStudioMessage(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["studio-messages"] });
    },
    onError: (e) => setError(getErrorMessage(e, "Failed to delete message")),
  });

  const handleSend = () => {
    const text = prompt.trim();
    if (!text || send.isPending) return;

    // Optimistic user message
    const optimistic: StudioMessageItem = {
      id: `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      role: "user",
      content: text,
      prompt_kind: null,
      trading_post_id: null,
      track: track || null,
      created_at: new Date().toISOString(),
    };
    setPending((prev) => [...prev, optimistic]);
    setPrompt("");

    send.mutate(text, {
      onSettled: () => {
        // Remove the optimistic row once the real query has been
        // invalidated. The invalidate in the mutation's onSuccess
        // will refetch and the real row will replace it.
        setPending((prev) => prev.filter((m) => m.id !== optimistic.id));
      },
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const serverItems = messages.data?.items ?? [];
  // Append any in-flight optimistic messages after the server list.
  // The server list is chronological; pending are the newest.
  const items = [...serverItems, ...pending];

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col min-w-0">
      <div className="border-b border-border pb-4">
        <h1 className="text-2xl font-semibold">Studio</h1>
        <p className="text-sm text-muted-foreground">
          {tab === "chat"
            ? "Ask for an article or a question. Articles are saved as drafts."
            : "Auto-generated articles from the movers scanner. Newest first."}
        </p>

      </div>

      {error && (
        <p className="mt-3 text-sm text-destructive">{error}</p>
      )}

      <div
        ref={scrollRef}
        className="flex-1 space-y-4 overflow-y-auto overflow-x-hidden py-4 min-w-0"
      >
        {messages.isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {!messages.isLoading && serverItems.length === 0 && pending.length === 0 && (
          <div className="rounded-md border border-dashed border-border p-6 text-sm text-muted-foreground">
            {tab === "chat" ? (
              <>
                <p className="mb-2">Try one of these:</p>
                <ul className="list-disc space-y-1 pl-5">
                  <li>Why are Tesla and Nvidia climbing today?</li>
                  <li>Write a short explainer about reading the S&amp;P 500.</li>
                  <li>How does margin trading work?</li>
                </ul>
              </>
            ) : (
              <>
                <p>No auto-generated articles yet.</p>
                <p className="mt-2">
                  The movers scanner runs at :20 of every hour during
                  US market hours. It writes about stocks that cross
                  the daily move threshold.
                </p>
              </>
            )}
          </div>
        )}
        {items.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>
                {tab === "chat" ? "Messages" : "Auto-generated articles"}
              </CardTitle>
              <CardDescription>Newest last</CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="divide-y divide-border">
                {items.map((m) => (
                  <li key={m.id}>
                    <MessageRow
                      message={m}
                      onDelete={() => remove.mutate(m.id)}
                      deleting={remove.isPending && remove.variables === m.id}
                    />
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
        {send.isPending && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-primary" />
            Writing article…
          </div>
        )}
      </div>

      {tab === "chat" && (
        <div className="pb-4">
        {/* Settings strip */}
        <div className="mb-3 flex flex-wrap gap-3 text-xs">
          <label className="flex items-center gap-2">
            <span className="text-muted-foreground">Form</span>
            <select
              value={form}
              onChange={(e) => setForm(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1"
            >
              {FORM_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="text-muted-foreground">Tone</span>
            <select
              value={tone}
              onChange={(e) => setTone(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1"
            >
              {TONE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="text-muted-foreground">Track</span>
            <select
              value={track}
              onChange={(e) => setTrack(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1"
            >
              {TRACK_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {/* Input row */}
        <div className="flex items-end gap-2">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask for an article or a question… (Enter to send, Shift+Enter for newline)"
            rows={2}
            disabled={send.isPending}
            className="flex-1 resize-none rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-60"
          />
          <Button
            onClick={handleSend}
            disabled={!prompt.trim() || send.isPending}
          >
            {send.isPending ? "Writing…" : "Send"}
          </Button>
        </div>
        </div>
      )}

      {/* Bottom surface cards — open the article list pages. */}
      <div className="border-t border-border pt-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <SurfaceCard
            title="Chat"
            description="Studio-generated articles."
            active={false}
            accent="#3b82f6"
            badge="CHAT"
            href="/trading/studio"
          />
          <SurfaceCard
            title="Auto"
            description="Auto-generated movers articles."
            active={false}
            accent="#f59e0b"
            badge="AUTO"
            href="/trading/movers"
          />
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// One message row
// ─────────────────────────────────────────────────────────────────

function extractTitle(content: string): string {
  // First markdown heading (# / ## / ###), else first non-empty line.
  const lines = content.split("\n");
  for (const line of lines) {
    const m = line.match(/^#{1,3}\s+(.+?)\s*$/);
    if (m) return m[1].trim();
  }
  const first = lines.find((l) => l.trim().length > 0);
  return (first || "Untitled").trim().slice(0, 120);
}

function countWords(content: string): number {
  return content.trim().split(/\s+/).filter(Boolean).length;
}

function MessageRow({
  message,
  onDelete,
  deleting,
}: {
  message: StudioMessageItem;
  onDelete: () => void;
  deleting: boolean;
}) {
  const isUser = message.role === "user";
  const title = isUser ? message.content : extractTitle(message.content);
  const words = countWords(message.content);
  // For short user prompts, title === content. Don't duplicate.
  const showBody = !isUser && message.content.trim() !== title.trim();

  return (
    <div className="flex items-start gap-3 py-4">
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-3">
          <p className="font-medium">{title}</p>
          <span className="text-xs text-muted-foreground whitespace-nowrap pt-1">
            {new Date(message.created_at).toLocaleString()} · {words} words
            {message.track ? ` · ${message.track}` : ""}
            {message.is_system ? " · auto" : ""}
          </span>
        </div>

        {showBody && (
          <div className="mt-3 prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h1: (props) => (
                  <h1 className="mt-4 mb-2 text-xl font-semibold" {...props} />
                ),
                h2: (props) => (
                  <h2 className="mt-4 mb-2 text-lg font-semibold" {...props} />
                ),
                h3: (props) => (
                  <h3 className="mt-3 mb-1.5 text-base font-semibold" {...props} />
                ),
                p: (props) => <p className="mb-3" {...props} />,
                ul: (props) => (
                  <ul className="mb-3 list-disc space-y-1 pl-5" {...props} />
                ),
                ol: (props) => (
                  <ol className="mb-3 list-decimal space-y-1 pl-5" {...props} />
                ),
                table: (props) => (
                  <div className="my-3 overflow-x-auto">
                    <table
                      className="min-w-full border border-border text-xs"
                      {...props}
                    />
                  </div>
                ),
                th: (props) => (
                  <th
                    className="border border-border bg-muted px-2 py-1 text-left font-medium"
                    {...props}
                  />
                ),
                td: (props) => (
                  <td className="border border-border px-2 py-1" {...props} />
                ),
                strong: (props) => (
                  <strong className="font-semibold" {...props} />
                ),
                a: (props) => (
                  <a
                    className="text-primary hover:underline"
                    target="_blank"
                    rel="noopener noreferrer"
                    {...props}
                  />
                ),
                code: (props) => (
                  <code
                    className="rounded bg-muted px-1 py-0.5 font-mono text-xs"
                    {...props}
                  />
                ),
              }}
            >
              {message.content}
            </ReactMarkdown>
          </div>
        )}
      </div>

      <Button
        variant="ghost"
        size="sm"
        title="Delete message"
        disabled={deleting}
        onClick={onDelete}
      >
        ×
      </Button>
    </div>
  );
}


function SurfaceCard({
  title,
  description,
  active,
  accent,
  badge,
  href,
}: {
  title: string;
  description: string;
  active: boolean;
  accent: string;
  badge: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className={
        "block text-left w-full transition-colors h-full rounded-lg border " +
        (active
          ? "border-primary bg-primary/5"
          : "border-border hover:bg-muted/30")
      }
    >
      <div className="h-1 rounded-t-lg" style={{ background: accent }} />
      <div className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div className="text-base font-medium">{title}</div>
          <span
            className="shrink-0 rounded-md px-2 py-0.5 text-[10px] uppercase tracking-wider"
            style={{
              background: `${accent}26`,
              color: accent,
              border: `1px solid ${accent}4d`,
            }}
          >
            {badge}
          </span>
        </div>
        <p className="text-xs text-muted-foreground line-clamp-2">
          {description}
        </p>
      </div>
    </Link>
  );
}
