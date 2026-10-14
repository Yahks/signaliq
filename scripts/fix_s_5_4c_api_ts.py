"""S.5.4c.1: fix the two rawRequest bugs left over from S.5.4c.

  1. new ApiError(...) was called with 4 args; the constructor takes 3.
  2. rawRequest only parsed { error: {...} }, not FastAPI's { detail: ... }.

Both live in the same `if (!res.ok)` block. One patch fixes both.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"
API_TS = WEB / "src" / "lib" / "api.ts"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


OLD = '''  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; context?: Record<string, unknown> } }).error;
    throw new ApiError(
      res.status,
      err?.code ?? "unknown",
      err?.message ?? `HTTP ${res.status}`,
      err?.context,
    );
  }
  return data as T;'''

NEW = '''  if (!res.ok) {
    // Two error shapes arrive at this client:
    //   1. Our own endpoints:  { error: { code, message, context } }
    //   2. FastAPI validation: { detail: "..." }
    //      our verify error:   { detail: { reason, message, provider } }
    //
    // Prefer FastAPI's structured detail when it carries a message.
    // This is what surfaces "The provider rejected this key" instead of
    // a bare "HTTP 400".
    type OwnError = { error?: { code?: string; message?: string; context?: Record<string, unknown> } };
    type FastApiDetail = string | { message?: string; reason?: string; provider?: string } | undefined;

    const own = (data as OwnError).error;
    const detail = (data as { detail?: FastApiDetail }).detail;

    let message = `HTTP ${res.status}`;
    let code = "unknown";
    let extra: unknown = undefined;

    if (detail !== undefined) {
      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail === "object") {
        message = detail.message ?? detail.reason ?? message;
        code = detail.reason ?? code;
        extra = detail;
      }
    } else if (own?.message) {
      message = own.message;
      code = own.code ?? "unknown";
      extra = own.context;
    }

    throw new ApiError(res.status, message, { code, extra });
  }
  return data as T;'''


def main() -> None:
    print("=" * 70)
    print("S.5.4c.1: fix rawRequest — arity + {detail} parsing")
    print("=" * 70)

    src = API_TS.read_text()

    if NEW.strip() in src and OLD not in src:
        print("\n↷ api.ts: rawRequest already patched")
    else:
        if OLD not in src:
            fail("api.ts: expected rawRequest block not found — paste the current block")
        src = src.replace(OLD, NEW, 1)
        API_TS.write_text(src)
        print("\n✓ api.ts: rawRequest patched")

    print("\nrunning tsc…")
    r = subprocess.run(
        ["./node_modules/.bin/tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        print("  ❌ TS errors:")
        for line in (r.stdout or r.stderr).splitlines()[:30]:
            print(f"      {line}")
        sys.exit(1)
    print("  ✓ TypeScript clean")

    print("=" * 70)
    print("✅ S.5.4c.1 complete — the frontend compiles and the error path is readable")
    print("=" * 70)


if __name__ == "__main__":
    main()
