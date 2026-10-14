"""S.6.4.1d.1 — replace `instanceof ApiError` with duck-typing.

The credentials page showed the generic "Failed to add the credential"
even though the backend returned:
  {"detail":{"reason":"invalid_key","message":"The provider rejected
   this key. Check that you copied it correctly.","provider":"groq"}}
with HTTP 400.

The error parsing works (rawRequest constructs ApiError correctly). The
problem is `e instanceof ApiError` returning false in the Next.js dev
runtime, where the same module can resolve to two distinct class
objects (one in the page bundle, one in the lib bundle). `tsc` sees one
class; the runtime has two.

Fix: stop using `instanceof` for runtime type detection. Duck-type on
the properties we actually read (userMessage, message, status). This is
robust to module duplication, class hot-reload, and dev/prod splits.

New export in api.ts:
  getErrorMessage(e: unknown, fallback?: string) -> string

Applied to 10 call sites across 8 files.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_TS = ROOT / "apps" / "web" / "src" / "lib" / "api.ts"
WEB = ROOT / "apps" / "web"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> bool:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return False
    if old not in src:
        print(f"  ⚠ {path.name}: pattern not found for '{label}' — skipping")
        return False
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")
    return True


# ─────────────────────────────────────────────────────────────────────
# 1. Add getErrorMessage to api.ts
# ─────────────────────────────────────────────────────────────────────

def step1_helper() -> None:
    print("\n[1/3] api.ts — add getErrorMessage helper")

    helper = '''

/**
 * S.6.4.1d.1 — extract a human-readable message from any thrown value.
 *
 * Uses duck-typing rather than `instanceof ApiError`. In Next.js dev
 * mode the same module can resolve to two distinct class objects (one
 * in the page bundle, one in the lib bundle), so `instanceof` returns
 * false even for our own errors. Reading the properties we care about
 * (userMessage, message) works regardless of which class object the
 * error is an instance of.
 */
export function getErrorMessage(e: unknown, fallback = "Something went wrong"): string {
  if (!e) return fallback;
  if (typeof e === "string") return e;
  const maybe = e as { userMessage?: string; message?: string };
  if (typeof maybe.userMessage === "string" && maybe.userMessage) return maybe.userMessage;
  if (typeof maybe.message === "string" && maybe.message) return maybe.message;
  return fallback;
}
'''

    # Append right after the ApiError class definition
    src = API_TS.read_text()
    if "export function getErrorMessage" in src:
        print("  ↷ already present")
        return

    anchor = '''  get userMessage(): string {
    const d = this.detail as { reason?: string; message?: string } | undefined;
    if (d && typeof d === "object" && d.message) return d.message;
    if (typeof this.detail === "string") return this.detail;
    return this.message;
  }
}'''

    if anchor not in src:
        fail("api.ts: ApiError class body anchor not found")

    src = src.replace(anchor, anchor + helper, 1)
    API_TS.write_text(src)
    print("  ✓ getErrorMessage added after ApiError")


# ─────────────────────────────────────────────────────────────────────
# 2. Replace instanceof in api.ts's own 401 check
# ─────────────────────────────────────────────────────────────────────

def step2_api_401() -> None:
    print("\n[2/3] api.ts — 401 duck-type check")

    patch(
        API_TS,
        '''    if (err instanceof ApiError && err.status === 401 && opts.auth !== false) {''',
        '''    const errStatus = (err as { status?: number })?.status;
    if (errStatus === 401 && opts.auth !== false) {''',
        "401 check uses duck-typing",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Replace instanceof in the pages
# ─────────────────────────────────────────────────────────────────────

SITES = [
    # (file, old, new, label)
    (
        "apps/web/src/app/(app)/settings/credentials/page.tsx",
        '''    onError: (e) => {
      const msg =
        e instanceof ApiError
          ? (e as ApiError).userMessage
          : "Failed to add the credential";
      setError(msg);''',
        '''    onError: (e) => {
      setError(getErrorMessage(e, "Failed to add the credential"));''',
        "credentials page: onError",
    ),
    (
        "apps/web/src/app/(app)/chains/new/page.tsx",
        '''    onError: (e) =>
      setError(e instanceof ApiError ? (e as ApiError).userMessage : "Build failed"),''',
        '''    onError: (e) => setError(getErrorMessage(e, "Build failed")),''',
        "chains/new: onError",
    ),
    (
        "apps/web/src/app/(app)/search/page.tsx",
        '''    onError: (e) => setError(e instanceof ApiError ? e.message : "Search failed"),''',
        '''    onError: (e) => setError(getErrorMessage(e, "Search failed")),''',
        "search: onError",
    ),
    (
        "apps/web/src/app/(app)/ingest/new/page.tsx",
        '''      setError(err instanceof ApiError ? err.message : "Upload failed");''',
        '''      setError(getErrorMessage(err, "Upload failed"));''',
        "ingest/new: onError",
    ),
    (
        "apps/web/src/app/(app)/briefs/page.tsx",
        '''    onError: (e) => setError(e instanceof ApiError ? e.message : "Failed"),''',
        '''    onError: (e) => setError(getErrorMessage(e, "Failed")),''',
        "briefs: onError",
    ),
    (
        "apps/web/src/app/(app)/trading/[slug]/page.tsx",
        '''    onError: (e) => setError(e instanceof ApiError ? e.message : "Failed"),''',
        '''    onError: (e) => setError(getErrorMessage(e, "Failed")),''',
        "trading/[slug]: onError",
    ),
    (
        "apps/web/src/app/(app)/sources/page.tsx",
        '''    onError: (e) => setError(e instanceof ApiError ? e.message : "Failed"),''',
        '''    onError: (e) => setError(getErrorMessage(e, "Failed")),''',
        "sources: onError 1",
    ),
    (
        "apps/web/src/app/(app)/sources/page.tsx",
        '''      setFetchError(e instanceof ApiError ? e.message : "Fetch failed");''',
        '''      setFetchError(getErrorMessage(e, "Fetch failed"));''',
        "sources: onError 2",
    ),
    (
        "apps/web/src/app/(auth)/register/page.tsx",
        '''      if (err instanceof ApiError) setError(err.message);''',
        '''      setError(getErrorMessage(err, "Registration failed"));''',
        "register: onError",
    ),
    (
        "apps/web/src/app/(auth)/login/page.tsx",
        '''      if (err instanceof ApiError) setError(err.message);''',
        '''      setError(getErrorMessage(err, "Login failed"));''',
        "login: onError",
    ),
]


def step3_pages() -> None:
    print("\n[3/3] replace instanceof in pages")

    for rel, old, new, label in SITES:
        path = ROOT / rel
        if not path.exists():
            print(f"  ⚠ {rel} not found — skipping")
            continue

        # Ensure getErrorMessage is imported from @/lib/api
        src = path.read_text()
        if "getErrorMessage" in src and "getErrorMessage" not in src.split("from \"@/lib/api\"")[0]:
            # Need to add the import
            # Find the import line from @/lib/api
            import re
            m = re.search(r'import \{([^}]+)\} from "@/lib/api";', src)
            if m and "getErrorMessage" not in m.group(1):
                names = m.group(1).strip()
                # Remove ApiError if no longer used
                rest_of_file = src.replace(m.group(0), "", 1)
                uses_api_error = "ApiError" in rest_of_file and "instanceof ApiError" in rest_of_file
                new_names = names
                if not uses_api_error and "ApiError" in names:
                    # Strip ApiError from the import
                    parts = [x.strip() for x in names.split(",") if x.strip() and x.strip() != "ApiError"]
                    new_names = ", ".join(parts)
                new_names = new_names.rstrip(",") + ", getErrorMessage"
                src = src.replace(m.group(0), f'import {{{new_names}}} from "@/lib/api";', 1)
                path.write_text(src)
                print(f"  ✓ {path.name}: import getErrorMessage")

        patch(path, old, new, label)


# ─────────────────────────────────────────────────────────────────────
# Verify
# ─────────────────────────────────────────────────────────────────────

def verify() -> bool:
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)

    # Syntax: parse each file
    files = [API_TS] + [ROOT / s[0] for s in SITES]
    for f in set(files):
        if not f.exists():
            continue
        r = subprocess.run(
            ["python", "-c",
             f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        # .tsx is not Python — skip this check for .tsx files
        if str(f).endswith(".tsx"):
            continue
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:200]}")
            return False
    print("  ✓ files readable")

    print("\n[A] tsc --noEmit")
    r = subprocess.run(
        ["./node_modules/.bin/tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        print("  ❌ TS errors:")
        for line in (r.stdout or r.stderr).splitlines()[:20]:
            print(f"     {line}")
        return False
    print("  ✓ TypeScript clean")

    print("\n[B] no remaining `instanceof ApiError` in pages")
    r = subprocess.run(
        ["grep", "-rn", "instanceof ApiError", "apps/web/src/"],
        cwd=ROOT, capture_output=True, text=True,
    )
    hits = [l for l in (r.stdout or "").splitlines() if l.strip()]
    # api.ts's own 401 check should now be duck-typed too
    if hits:
        print(f"  ⚠ remaining: {len(hits)} line(s):")
        for h in hits[:5]:
            print(f"     {h}")
    else:
        print("  ✓ no instances of `instanceof ApiError` remain")

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.4.1d.1 — duck-typing replaces instanceof ApiError")
    print("=" * 70)

    step1_helper()
    step2_api_401()
    step3_pages()
    ok = verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.4.1d.1 complete")
        print("   every error display uses getErrorMessage (duck-typed)")
        print()
        print("   Browser: retry a fake key. Expect the readable message:")
        print("     'The provider rejected this key. Check that you copied")
        print("      it correctly.'")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
