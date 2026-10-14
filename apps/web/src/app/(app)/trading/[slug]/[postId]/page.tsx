"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function TradingPostPage() {
  const params = useParams<{ slug: string; postId: string }>();
  const router = useRouter();

  const post = useQuery({
    queryKey: ["trading-post", params.postId],
    queryFn: () => api.getTradingPost(params.postId),
  });

  if (post.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (post.isError || !post.data) {
    return <p className="text-sm text-destructive">Post not found</p>;
  }

  const p = post.data;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold">{p.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {new Date(p.published_at ?? p.created_at).toLocaleString()} ·{" "}
            {p.word_count ?? "?"} words ·{" "}
            <span className={
              p.status === "published" ? "text-green-500" :
              p.status === "draft" ? "text-amber-500" : "text-muted-foreground"
            }>
              {p.status}
            </span>
            {" · "}
            <span className="font-mono text-xs">{p.source_kind}</span>
          </p>
        </div>
        <Button variant="outline" onClick={() => router.back()}>Back</Button>
      </div>

      {p.symbols.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {p.symbols.slice(0, 60).map((s) => (
            <Link
              key={s}
              href={`/trading/symbols/${s}`}
              className="rounded-md border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] hover:bg-muted"
            >
              {s}
            </Link>
          ))}
          {p.symbols.length > 60 && (
            <span className="text-[11px] text-muted-foreground self-center">
              +{p.symbols.length - 60} more
            </span>
          )}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Body</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="prose prose-sm dark:prose-invert max-w-none
                          prose-table:text-xs prose-table:font-mono
                          prose-headings:font-semibold prose-h3:text-base">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {p.body}
            </ReactMarkdown>
          </div>
        </CardContent>
      </Card>

      {p.source_urls.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Sources</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-xs">
            {p.source_urls.map((u) => (
              <a
                key={u}
                href={u}
                target="_blank"
                rel="noopener noreferrer"
                className="block text-blue-400 hover:underline font-mono truncate"
              >
                {u}
              </a>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
