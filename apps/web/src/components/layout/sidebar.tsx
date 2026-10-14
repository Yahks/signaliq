"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, Upload, Database, FileText, Settings, Tag, Users, Search, Layers, AlertTriangle, Link2, Newspaper, Activity, LineChart, Hash, FileWarning, Trophy, KeyRound, Trash2} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

// S.7.5a — `block` tags which items belong to an enabled service.
// Items without a block tag are always shown.
const items = [
  { href: "/dashboard", label: "Dashboard", icon: BarChart3 },
  { href: "/sources", label: "Sources", icon: Database },
  { href: "/ingest/new", label: "Ingest", icon: Upload },
  { href: "/records", label: "Records", icon: FileText },
  { href: "/entities", label: "Entities", icon: Users },
  { href: "/topics", label: "Topics", icon: Tag },
  { href: "/clusters", label: "Clusters", icon: Layers },
  { href: "/pain-points", label: "Pain points", icon: AlertTriangle },
  { href: "/chains", label: "News chains", icon: Link2, block: "news" as const },
  { href: "/briefs", label: "Daily briefs", icon: Newspaper, block: "news" as const },
  { href: "/trading", label: "Trading", icon: LineChart, block: "trading" as const },
  { href: "/trading/performance", label: "Performance", icon: Activity, block: "trading" as const },
  { href: "/trading/chat", label: "Chat", icon: FileText, block: "trading" as const },
  { href: "/predictions", label: "Predictions", icon: Trophy, block: "football" as const },
  { href: "/admin/trading", label: "Gen health", icon: Activity, block: "trading" as const },
  { href: "/trading/symbols", label: "Symbols", icon: Hash, block: "trading" as const },
  { href: "/trading/paper-account", label: "Paper Account", icon: KeyRound, block: "trading" as const },
  { href: "/trading/trader-process", label: "Trader Process", icon: FileText, block: "trading" as const },
  { href: "/trading/trader-process/saved", label: "Saved process", icon: FileText, block: "trading" as const },
  { href: "/trading/market-conditions", label: "Market conditions", icon: Activity, block: "trading" as const },
  { href: "/trading/trader-process/drafts", label: "Process drafts", icon: FileWarning, block: "trading" as const },
  { href: "/trading/drafts", label: "Draft review", icon: FileWarning, block: "trading" as const },
  { href: "/search", label: "Search", icon: Search },
];

export function Sidebar() {
  const pathname = usePathname();
  // S.7.5a — filter items by the org's enabled blocks. Block-tagged
  // items are hidden until the block is enabled. Untagged items always
  // show. While the query loads, only untagged items render.
  const { data: blocksData } = useQuery({
    queryKey: ["block-settings"],
    queryFn: () => api.listBlockSettings(),
    refetchInterval: 60_000,
  });
  const enabled = new Set(
    (blocksData?.items ?? []).filter((b) => b.auto_enabled).map((b) => b.slug),
  );
  const visible = items.filter((it) => !it.block || enabled.has(it.block));
  return (
    <aside className="hidden w-56 flex-col border-r border-border bg-card md:flex">
      <div className="flex h-14 items-center border-b border-border px-4">
        <Link href="/dashboard" className="text-lg font-semibold">
          SignalIQ
        </Link>
      </div>
      <nav className="flex-1 space-y-1 p-3">
        {visible.map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href || pathname.startsWith(item.href + "/");
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition",
                active ? "bg-muted font-medium" : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-border p-3 space-y-1">
        <Link
          href="/admin/health"
          className={cn(
            "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition",
            pathname === "/admin/health"
              ? "bg-muted font-medium"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <Activity className="h-4 w-4" />
          Health
        </Link>
        <Link
          href="/settings"
          className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <Settings className="h-4 w-4" />
          Settings
        </Link>
        <Link
          href="/settings/credentials"
          className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <KeyRound className="h-4 w-4" />
          API keys
        </Link>
        <Link
          href="/settings/editorial"
          className={cn(
            "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition",
            pathname === "/settings/editorial"
              ? "bg-muted font-medium"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <Newspaper className="h-4 w-4" />
          Voice
        </Link>
        <Link
          href="/trash"
          className={cn(
            "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition",
            pathname === "/trash"
              ? "bg-muted font-medium"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <Trash2 className="h-4 w-4" />
          Trash
        </Link>
      </div>
    </aside>
  );
}
