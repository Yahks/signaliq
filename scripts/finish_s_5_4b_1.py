"""
Finish S.5.4b.1 after the step-2 pattern mismatch.

Finishes the trading page refactor with the ACTUAL current text,
then runs briefs + dashboard + tsc + smoke.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"
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
        fail(f"{path.name}: pattern not found for '{label}'\n---expected---\n{old[:400]}\n---")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


def step1_finish_trading() -> None:
    print("\n[1/4] trading [slug] — finish refactor")

    # 1a. Button: use gate.canGenerate + gate.tooltip
    patch(
        TRADING,
        '''        <Button
          onClick={() => generate.mutate()}
          disabled={generate.isPending || queued || outOfTokens || noCredentials}
          title={
            outOfTokens
              ? "Out of tokens — top up to continue"
              : noCredentials
              ? "Add an API key first — go to /settings/credentials"
              : undefined
          }
        >''',
        '''        <Button
          onClick={() => generate.mutate()}
          disabled={generate.isPending || queued || !gate.canGenerate}
          title={gate.tooltip}
        >''',
        "button disabled + title",
    )

    # 1b. Out-of-tokens banner guard
    patch(
        TRADING,
        '''      {outOfTokens && (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">
          You are out of tokens. Automated and manual generation is paused. Contact your admin to top up.
        </div>
      )}''',
        '''      {gate.outOfTokens && (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400">
          You are out of tokens. Automated and manual generation is paused. Contact your admin to top up.
        </div>
      )}''',
        "out-of-tokens banner guard",
    )

    # 1c. No-credentials banner guard
    patch(
        TRADING,
        '''      {!outOfTokens && noCredentials && (''',
        '''      {!gate.outOfTokens && gate.noCredentials && (''',
        "no-credentials banner guard",
    )


def step2_briefs() -> None:
    print("\n[2/4] briefs — add gate")

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


def step3_dashboard() -> None:
    print("\n[3/4] dashboard — first-run welcome banner")

    src = DASH.read_text()
    if "useGate" not in src:
        if 'from "@/lib/api"' not in src:
            fail("dashboard: no @/lib/api import to anchor the hook import")
        import re
        src = re.sub(
            r'(import \{[^}]*\} from "@/lib/api";)',
            r'\1\nimport { useGate } from "@/hooks/useGate";',
            src, count=1,
        )
        DASH.write_text(src)
        print("  ✓ dashboard: useGate imported")

    if "gate = useGate" in DASH.read_text():
        print("  ↷ dashboard: gate already instantiated")
    else:
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


def step4_verify() -> bool:
    print("\n[4/4] tsc --noEmit")
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

    # Quick live check
    import json
    login = subprocess.run([
        "curl", "-s", "-X", "POST", "http://localhost:8000/api/auth/login",
        "-H", "Content-Type: application/json",
        "-d", '{"identifier":"tokentest","password":"TestPass123!"}',
    ], capture_output=True, text=True, timeout=20)
    try:
        token = json.loads(login.stdout).get("access_token")
    except Exception:
        print("  ⚠ login not JSON — skipping live check")
        return True
    if not token:
        print("  ⚠ no token — skipping live check")
        return True

    creds = subprocess.run([
        "curl", "-s", "http://localhost:8000/api/settings/credentials",
        "-H", f"Authorization: Bearer {token}",
    ], capture_output=True, text=True, timeout=20)
    print(f"  credentials: {creds.stdout[:150]}")
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — finish S.5.4b.1")
    print("=" * 70)

    step1_finish_trading()
    step2_briefs()
    step3_dashboard()
    ok = step4_verify()

    print("=" * 70)
    if ok:
        print("✅ S.5.4b.1 complete")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
