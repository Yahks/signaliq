"use client";

import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth";
import { Button } from "@/components/ui/button";
import { NotificationBell } from "@/components/layout/notification-bell";
import { TokenBadge } from "@/components/layout/token-badge";

export function Topbar() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const orgSlug = useAuthStore((s) => s.orgSlug);
  const role = useAuthStore((s) => s.role);
  const clear = useAuthStore((s) => s.clear);

  function logout() {
    // S.7.5a — clear the middleware cookies so the router stops
    // treating the user as authed.
    void import("@/lib/cookies").then(({ clearAuthCookies }) => clearAuthCookies());
    clear();
    router.replace("/login");
  }

  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-card px-6">
      <div className="flex items-center gap-3 text-sm">
        {orgSlug && <span className="font-medium">{orgSlug}</span>}
        {role && <span className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">{role}</span>}
      </div>
      <div className="flex items-center gap-3 text-sm">
        <TokenBadge />
        <NotificationBell />
        <span className="text-muted-foreground">{user?.email}</span>
        <Button variant="ghost" size="sm" onClick={logout}>
          Sign out
        </Button>
      </div>
    </header>
  );
}
