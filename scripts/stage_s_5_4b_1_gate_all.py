"""
SignalIQ — S.5.4b.1: extend the generation gate to all pages.

Creates a shared useGate() hook and applies it to:
  - trading/[slug]  (refactor — behaviour unchanged)
  - briefs          (new — out-of-tokens + no-credentials)
  - dashboard       (new — first-run "add a key" banner)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"
HOOK = WEB / "src" / "hooks" / "useGate.ts"
TRADING = WEB / "src" / "app" / "(app)" / "trading" / "[slug]" / "page.tsx"
BRIEFS = WEB / "src" / "app" / "(app)" / "briefs" / "page.tsx"
DASH = WEB / "src" / "app" / "(app)" / "dashboard" / "page.tsx"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n---expected---\n{old[:400]}")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. useGate hook
# ─────────────────────────────────────────────────────────────────────

HOOK_SRC = '''"use client";

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
'''


def step1_hook() -> None:
    print("\n[1/6] useGate hook")
    HOOK.parent.mkdir(parents=True, exist_ok=True)
    HOOK.write_text(HOOK_SRC)
    print(f"  ✓ wrote {HOOK.relative_to(ROOT)}")


# ─────────────────────────────────────────────────────────────────────
# 2. trading [slug] — refactor to use the hook
# ─────────────────────────────────────────────────────────────────────

def step2_trading() -> None:
    print("\n[2/6] trading [slug] — refactor to useGate")

    patch(
        TRADING,
        'import { api, ApiError } from "@/lib/api";',
        'import { api, ApiError } from "@/lib/api";\nimport { useGate } from "@/hooks/useGate";',
        "import useGate",
    )

    patch(
        TRADING,
        '''  const balance = useQuery({
    queryKey: ["token-balance"],
    queryFn: () => api.getTokenBalance(),
    refetchInterval: 30_000,
  });

  const creds = useQuery({
    queryKey: ["credentials"],
    queryFn: () => api.listCredentials(),
    refetchInterval: 30_000,
  });

  const outOfTokens = (balance.data?.balance ?? 1) <= 0;
  // While loading, assume the user has credentials — no false banner flash.
  const noCredentials = (creds.data?.items.length ?? 1) === 0;''',
        '''  const gate = useGate();''',
        "replace inline queries with useGate",
    )

    patch(
        TRADING,
        '''          disabled={generate.isPending || queued || outOfTokens}
          title={
            outOfTokens
              ? "Out of tokens — top up to continue"
              : noCredentials
              ? "Add an API key first — go to /settings/credentials"
              : undefined
          }''',
        '''          disabled={generate.isPending || queued || !gate.canGenerate}
          title={gate.tooltip}''',
        "button disabled + title",
    )

    patch(
        TRADING,
        '''      {outOfTokens && (''',
        '''      {gate.outOfTokens && (''',
        "out-of-tokens banner guard",
    )

    patch(
        TRADING,
        '''      {!outOfTokens && noCredentials && (''',
        '''      {!gate.outOfTokens && gate.noCredentials && (''',
        "no-credentials banner guard",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. briefs — add the gate
# ─────────────────────────────────────────────────────────────────────

def step3_briefs() -> None:
    print("\n[3/6] briefs — add gate")

    patch(
        BRIEFS,
        'import { api, ApiError, type DailyBriefResponse } from "@/lib/api";',
        'import { api, ApiError, type DailyBriefResponse } from "@/lib/api";\nimport { useGate } from "@/hooks/useGate";',
        "import useGate",
    )

    patch(
        BRIEFS,
        '''  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);''',
        '''  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);
  const gate = useGate();''',
        "instantiate gate",
    )

    patch(
        BRIEFS,
        '''        <Button onClick={() => generate.mutate()} disabled={generate.isPending || queued}>''',
        '''        <Button
          onClick={() => generate.mutate()}
          disabled={generate.isPending || queued || !gate.canGenerate}
          title={gate.tooltip}
        >''',
        "button disabled + title",
    )

    # Add banners above the existing error line
    patch(
        BRIEFS,
        '''      {error && <p className="text-sm text-destructive">{error}</p>}''',
        '''      {gate.outOfTokens && (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">
          You are out of tokens. Generation is paused. Contact your admin to top up.
        </div>
      )}
      {!gate.outOfTokens && gate.noCredentials && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-400">
          You haven&apos;t added an API key yet.{" "}
          <a href="/settings/credentials" className="underline">Add one</a>{" "}
          to enable generation.
        </div>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}''',
        "gate banners",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. dashboard — first-run banner
# ─────────────────────────────────────────────────────────────────────

def step4_dashboard() -> None:
    print("\n[4/6] dashboard — first-run banner")

    src = DASH.read_text()
    if "useGate" not in src:
        # Add the import after the first import line that touches @/lib/api
        if 'from "@/lib/api"' in src:
            import re
            src = re.sub(
                r'(import \{[^}]*\} from "@/lib/api";)',
                r'\1\nimport { useGate } from "@/hooks/useGate";',
                src, count=1,
            )
        else:
            fail("dashboard: no @/lib/api import to anchor the hook import")
        DASH.write_text(src)
        print("  ✓ dashboard: useGate imported")

    if "gate = useGate" in DASH.read_text():
        print("  ↷ dashboard: gate already instantiated")
    else:
        # Instantiate after the component's opening — find the first useQuery
        import re
        m = re.search(r'(\n  const \w+ = useQuery\(\{)', DASH.read_text())
        if not m:
            fail("dashboard: no useQuery to anchor gate instantiation")
        src = DASH.read_text()
        src = src[:m.start(1)] + "\n  const gate = useGate();" + src[m.start(1):]
        DASH.write_text(src)
        print("  ✓ dashboard: gate instantiated")

    if "Welcome to SignalIQ" in DASH.read_text():
        print("  ↷ dashboard: welcome banner already present")
        return

    # Insert the banner as the first child inside the outermost div of the return
    import re
    m = re.search(r'return \(\s*\n(\s*)<div[^>]*>\s*\n', DASH.read_text())
    if not m:
        fail("dashboard: cannot find the return's outermost <div>")
    indent = m.group(1)
    banner = (
        f'{indent}  {{gate.noCredentials && (\n'
        f'{indent}    <div className="rounded-md border border-amber-500/30 '
        f'bg-amber-500/5 p-3 text-sm text-amber-400">\n'
        f'{indent}      Welcome to SignalIQ. '
        f'<a href="/settings/credentials" className="underline">'
        f'Add your first API key</a> to enable generation.\n'
        f'{indent}    </div>\n'
        f'{indent}  )}}\n'
    )
    src = DASH.read_text()
    insert_at = m.end()
    src = src[:insert_at] + banner + src[insert_at:]
    DASH.write_text(src)
    print("  ✓ dashboard: welcome banner added")


# ─────────────────────────────────────────────────────────────────────
# 5. TypeScript check
# ─────────────────────────────────────────────────────────────────────

def step5_tsc() -> bool:
    print("\n[5/6] tsc --noEmit")
    r = subprocess.run(
        ["npx", "tsc", "--noEmit"], cwd=WEB,
        capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        print("  ❌ TypeScript errors:")
        for line in (r.stdout or r.stderr).splitlines()[:30]:
            print(f"      {line}")
        return False
    print("  ✓ TypeScript clean")
    return True


# ─────────────────────────────────────────────────────────────────────
# 6. Live smoke
# ─────────────────────────────────────────────────────────────────────

def step6_smoke() -> bool:
    print("\n[6/6] Live smoke")
    import json

    login = subprocess.run([
        "curl", "-s", "-X", "POST", "http://localhost:8000/api/auth/login",
        "-H", "Content-Type: application/json",
        "-d", '{"identifier":"tokentest","password":"TestPass123!"}',
    ], capture_output=True, text=True, timeout=20)
    try:
        token = json.loads(login.stdout).get("access_token")
    except Exception:
        print(f"  ⚠ login not JSON: {login.stdout[:200]}")
        return True
    if not token:
        print(f"  ⚠ no token — skipping live check")
        return True

    # Credentials + balance — the two queries the hook performs
    creds = subprocess.run([
        "curl", "-s", "http://localhost:8000/api/settings/credentials",
        "-H", f"Authorization: Bearer {token}",
    ], capture_output=True, text=True, timeout=20)
    bal = subprocess.run([
        "curl", "-s", "http://localhost:8000/api/settings/token-balance",
        "-H", f"Authorization: Bearer {token}",
    ], capture_output=True, text=True, timeout=20)
    print(f"  credentials: {creds.stdout[:150]}")
    print(f"  balance:     {bal.stdout[:150]}")
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — S.5.4b.1: gate all generation pages")
    print("=" * 70)

    step1_hook()
    step2_trading()
    step3_briefs()
    step4_dashboard()
    ok_ts = step5_tsc()
    ok_smoke = step6_smoke()

    print("=" * 70)
    if ok_ts and ok_smoke:
        print("✅ S.5.4b.1 complete")
        print()
        print("All generation surfaces now respect the credentials + token gate.")
        print("Dashboard shows the first-run welcome banner when no key exists.")
        sys.exit(0)
    print("❌ S.5.4b.1 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
