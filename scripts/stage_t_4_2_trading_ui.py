"""
SignalIQ — Phase T.4.2: Trading block UI.

Ships:
  1. react-markdown + remark-gfm deps
  2. lib/api.ts additions: trading types + 5 methods
  3. /trading hub          — 16 cards
  4. /trading/[slug]       — post list + generate button
  5. /trading/[slug]/[postId] — full post with markdown
  6. sidebar link
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"
API_TS = WEB / "src" / "lib" / "api.ts"
HUB = WEB / "src" / "app" / "(app)" / "trading" / "page.tsx"
SLUG_PAGE = WEB / "src" / "app" / "(app)" / "trading" / "[slug]" / "page.tsx"
POST_PAGE = WEB / "src" / "app" / "(app)" / "trading" / "[slug]" / "[postId]" / "page.tsx"
SIDEBAR = WEB / "src" / "components" / "layout" / "sidebar.tsx"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(f"\n❌ {path.name}: pattern not found for '{label}'")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. Install react-markdown + remark-gfm
# ─────────────────────────────────────────────────────────────────────

def step1_install_deps() -> None:
    print("\n[1/6] Install react-markdown + remark-gfm")
    r = subprocess.run(
        ["npm", "install", "--save", "react-markdown", "remark-gfm"],
        cwd=WEB, capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        raise SystemExit(f"❌ npm install failed:\n{r.stderr[-1000:]}")
    print("  ✓ installed")


# ─────────────────────────────────────────────────────────────────────
# 2. lib/api.ts additions
# ─────────────────────────────────────────────────────────────────────

def step2_api_ts() -> None:
    print("\n[2/6] Extend lib/api.ts")
    src = API_TS.read_text()

    if "TradingSubblockResponse" in src:
        print("  ↷ api.ts: types already present")
    else:
        types_block = '''

// ---------- Trading block (Phase T) ----------

export type TradingSubblockResponse = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  category: string | null;
  cadence: string;
  source_kind: string | null;
  enabled: boolean;
  sort_order: number;
  theme: { color?: string; icon?: string; layout?: string } | null;
  post_count: number;
  latest_post_at: string | null;
};

export type TradingSubblockListResponse = {
  items: TradingSubblockResponse[];
  total: number;
};

export type TradingPostResponse = {
  id: string;
  subblock_id: string;
  subblock_slug: string | null;
  title: string;
  body: string;
  format: string;
  source_kind: string;
  source_urls: string[];
  symbols: string[];
  status: string;
  word_count: number | null;
  published_at: string | null;
  created_at: string;
};

export type TradingPostListResponse = {
  items: TradingPostResponse[];
  total: number;
  limit: number;
  offset: number;
};

export type TradingGenerateResponse = {
  task_id: string;
  status: string;
  slug: string;
};
'''
        # Insert before "export const api"
        marker = "export const api = {"
        if marker not in src:
            marker = "export const api:"
            if marker not in src:
                raise SystemExit("❌ api.ts: cannot find 'export const api'")
        src = src.replace(marker, types_block + "\n" + marker, 1)
        API_TS.write_text(src)
        print("  ✓ api.ts: trading types added")

    # Methods
    src = API_TS.read_text()
    if "listTradingSubblocks" in src:
        print("  ↷ api.ts: methods already present")
        return

    marker = '  // dashboard\n  dashboardSummary: () => request<DashboardSummary>("/api/dashboard/summary"),'
    if marker not in src:
        raise SystemExit("❌ api.ts: dashboardSummary marker not found")

    new = marker + '''

  // trading block (Phase T)
  listTradingSubblocks: () =>
    request<TradingSubblockListResponse>("/api/trading/subblocks"),
  getTradingSubblock: (slug: string) =>
    request<TradingSubblockResponse>(`/api/trading/subblocks/${slug}`),
  listTradingPosts: (slug: string, limit: number = 20, offset: number = 0) =>
    request<TradingPostListResponse>(
      `/api/trading/subblocks/${slug}/posts?limit=${limit}&offset=${offset}`,
    ),
  getTradingPost: (postId: string) =>
    request<TradingPostResponse>(`/api/trading/posts/${postId}`),
  generateTradingPost: (slug: string) =>
    request<TradingGenerateResponse>(
      `/api/trading/subblocks/${slug}/generate`,
      { method: "POST" },
    ),'''

    src = src.replace(marker, new, 1)
    API_TS.write_text(src)
    print("  ✓ api.ts: trading methods added")


# ─────────────────────────────────────────────────────────────────────
# 3. Hub page
# ─────────────────────────────────────────────────────────────────────

HUB_SRC = '''"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, type TradingSubblockResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function categoryBadge(cat: string | null): string {
  switch (cat) {
    case "market":    return "bg-blue-500/15 text-blue-400 border border-blue-500/30";
    case "education": return "bg-purple-500/15 text-purple-400 border border-purple-500/30";
    case "macro":     return "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30";
    case "tools":     return "bg-cyan-500/15 text-cyan-400 border border-cyan-500/30";
    default:          return "bg-muted text-muted-foreground border border-border";
  }
}

function SubblockCard({ s }: { s: TradingSubblockResponse }) {
  const accent = s.theme?.color || "#6366f1";
  const latest = s.latest_post_at
    ? new Date(s.latest_post_at).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      })
    : null;

  return (
    <Link href={`/trading/${s.slug}`}>
      <Card className="hover:bg-muted/30 transition-colors h-full">
        <div className="h-1 rounded-t-lg" style={{ background: accent }} />
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-2">
            <CardTitle className="text-base">{s.name}</CardTitle>
            {s.category && (
              <span className={`shrink-0 rounded-md px-2 py-0.5 text-[10px] uppercase tracking-wider ${categoryBadge(s.category)}`}>
                {s.category}
              </span>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {s.description && (
            <p className="text-xs text-muted-foreground line-clamp-2">
              {s.description}
            </p>
          )}
          <div className="flex items-center justify-between pt-1 text-xs text-muted-foreground">
            <span>
              <span className="font-mono text-foreground">{s.post_count}</span> post{s.post_count === 1 ? "" : "s"}
            </span>
            {latest ? (
              <span className="whitespace-nowrap">{latest}</span>
            ) : (
              <span className="italic opacity-70">no posts yet</span>
            )}
          </div>
          <div className="flex items-center justify-between text-[10px] text-muted-foreground pt-1">
            <span>{s.cadence.replace("_", " ")}</span>
            <span className="font-mono">{s.source_kind}</span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

export default function TradingHubPage() {
  const q = useQuery({
    queryKey: ["trading-subblocks"],
    queryFn: () => api.listTradingSubblocks(),
    refetchInterval: 60_000,
  });

  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Trading</h1>
        <p className="text-sm text-muted-foreground">
          {total} subblocks · {items.reduce((n, s) => n + s.post_count, 0)} published posts
        </p>
      </div>

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading subblocks…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No subblocks configured.</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {items.map((s) => <SubblockCard key={s.id} s={s} />)}
        </div>
      )}
    </div>
  );
}
'''


# ─────────────────────────────────────────────────────────────────────
# 4. Subblock detail page
# ─────────────────────────────────────────────────────────────────────

SLUG_SRC = '''"use client";

import Link from "next/link";
import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

export default function TradingSubblockPage() {
  const params = useParams<{ slug: string }>();
  const slug = params.slug;
  const qc = useQueryClient();

  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);

  const subblock = useQuery({
    queryKey: ["trading-subblock", slug],
    queryFn: () => api.getTradingSubblock(slug),
  });

  const posts = useQuery({
    queryKey: ["trading-posts", slug],
    queryFn: () => api.listTradingPosts(slug, 30, 0),
    refetchInterval: 30_000,
  });

  const generate = useMutation({
    mutationFn: () => api.generateTradingPost(slug),
    onSuccess: () => {
      setQueued(true);
      setError(null);
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["trading-posts", slug] });
        qc.invalidateQueries({ queryKey: ["trading-subblock", slug] });
        setQueued(false);
      }, 30_000);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Failed"),
  });

  const items = posts.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{subblock.data?.name ?? slug}</h1>
          {subblock.data?.description && (
            <p className="mt-1 text-sm text-muted-foreground max-w-2xl">
              {subblock.data.description}
            </p>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            {posts.data?.total ?? 0} posts · cadence: {subblock.data?.cadence ?? "?"} ·{" "}
            source: {subblock.data?.source_kind ?? "?"}
          </p>
        </div>
        <Button
          onClick={() => generate.mutate()}
          disabled={generate.isPending || queued}
        >
          {generate.isPending ? "Queuing…" : queued ? "Queued — refreshing in 30s" : "Generate now"}
        </Button>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {queued && (
        <div className="rounded-md border border-green-500/30 bg-green-500/5 p-3 text-sm text-green-400">
          ✓ Generation queued. Will appear below within ~30s.
        </div>
      )}

      {posts.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading posts…</p>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No posts yet. Use "Generate now" above, or wait for the scheduled cadence.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Recent posts</CardTitle>
            <CardDescription>Newest first</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              {items.map((p) => (
                <li key={p.id}>
                  <Link
                    href={`/trading/${slug}/${p.id}`}
                    className="block py-3 -mx-3 px-3 hover:bg-muted rounded"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-medium truncate">{p.title}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {new Date(p.published_at ?? p.created_at).toLocaleString()} ·{" "}
                          {p.word_count ?? "?"} words · {p.symbols.length} symbols
                        </p>
                      </div>
                      <span className="text-xs text-muted-foreground whitespace-nowrap">→</span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
'''


# ─────────────────────────────────────────────────────────────────────
# 5. Post detail page
# ─────────────────────────────────────────────────────────────────────

POST_SRC = '''"use client";

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
            <span
              key={s}
              className="rounded-md border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px]"
            >
              {s}
            </span>
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
'''


def step3_write_pages() -> None:
    print("\n[3/6] Write hub page")
    HUB.parent.mkdir(parents=True, exist_ok=True)
    HUB.write_text(HUB_SRC)
    print(f"  ✓ wrote {HUB.relative_to(ROOT)}")

    print("\n[4/6] Write subblock detail page")
    SLUG_PAGE.parent.mkdir(parents=True, exist_ok=True)
    SLUG_PAGE.write_text(SLUG_SRC)
    print(f"  ✓ wrote {SLUG_PAGE.relative_to(ROOT)}")

    print("\n[5/6] Write post detail page")
    POST_PAGE.parent.mkdir(parents=True, exist_ok=True)
    POST_PAGE.write_text(POST_SRC)
    print(f"  ✓ wrote {POST_PAGE.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 6. Sidebar link + TS check
# ─────────────────────────────────────────────────────────────────────

def step6_sidebar_and_typecheck() -> bool:
    print("\n[6/6] Add sidebar link + TypeScript check")

    src = SIDEBAR.read_text()
    if '"/trading"' not in src:
        # Add "Trading" link after /briefs (if it exists)
        if '  { href: "/briefs", label: "Daily briefs", icon: Newspaper },' in src:
            _patch_and_verify(
                SIDEBAR,
                '  { href: "/briefs", label: "Daily briefs", icon: Newspaper },',
                '  { href: "/briefs", label: "Daily briefs", icon: Newspaper },\n'
                '  { href: "/trading", label: "Trading", icon: LineChart },',
                "add trading link after briefs",
            )
            # Add LineChart to lucide imports if not present
            src2 = SIDEBAR.read_text()
            if "LineChart" not in src2.split("\n")[0]:
                import re
                m = re.search(r'import \{([^}]+)\} from "lucide-react"', src2)
                if m:
                    imports = m.group(1)
                    if "LineChart" not in imports:
                        new_imports = imports.rstrip() + ", LineChart"
                        src2 = src2.replace(m.group(0), f'import {{{new_imports}}} from "lucide-react"')
                        SIDEBAR.write_text(src2)
                        print("  ✓ sidebar.tsx: LineChart import added")
        else:
            print("  ⚠ sidebar.tsx: /briefs anchor not found — add manually")

    print("\n  running tsc --noEmit...")
    r = subprocess.run(
        ["npx", "tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        print("  ❌ TypeScript errors:")
        for line in r.stdout.splitlines()[:20]:
            print(f"      {line}")
        return False
    print("  ✓ TypeScript clean")
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.4.2: Trading block UI")
    print("=" * 70)

    step1_install_deps()
    step2_api_ts()
    step3_write_pages()
    ok = step6_sidebar_and_typecheck()

    print("=" * 70)
    if ok:
        print("✅ T.4.2 complete — /trading hub live")
        print()
        print("Open http://localhost:3000/trading")
        sys.exit(0)
    print("❌ T.4.2 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
