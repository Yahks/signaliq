"""S.5.4c.2: remove double JSON.stringify in the credentials methods.

rawRequest already stringifies opts.body. The credential methods were
stringifying first, so the server received a JSON string instead of a
JSON object → FastAPI 422 (validation), never reaching the verify step.

Audit the whole file for the same pattern, fix the credential methods,
report any other suspicious callers.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"
API_TS = WEB / "src" / "lib" / "api.ts"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


# ─────────────────────────────────────────────────────────────────────
# 1. Audit — find every `body: JSON.stringify(...)` in api.ts
# ─────────────────────────────────────────────────────────────────────

def audit(src: str) -> list[tuple[int, str]]:
    hits = []
    for i, line in enumerate(src.splitlines(), start=1):
        if "body:" in line and "JSON.stringify" in line:
            hits.append((i, line.strip()))
    return hits


# ─────────────────────────────────────────────────────────────────────
# 2. Fix credential methods
# ─────────────────────────────────────────────────────────────────────

FIXES = [
    (
        'request<Credential>("/api/settings/credentials", {\n      method: "POST",\n      body: JSON.stringify(input),\n    }),',
        'request<Credential>("/api/settings/credentials", {\n      method: "POST",\n      body: input,\n    }),',
        "addCredential",
    ),
    (
        'request<Credential>(`/api/settings/credentials/${id}`, {\n      method: "PATCH",\n      body: JSON.stringify({ is_active }),\n    }),',
        'request<Credential>(`/api/settings/credentials/${id}`, {\n      method: "PATCH",\n      body: { is_active },\n    }),',
        "toggleCredential",
    ),
    (
        'request<{ status: string }>("/api/settings/credentials/support-request", {\n      method: "POST",\n      body: JSON.stringify({ message }),\n    }),',
        'request<{ status: string }>("/api/settings/credentials/support-request", {\n      method: "POST",\n      body: { message },\n    }),',
        "requestSupport",
    ),
]


def main() -> None:
    print("=" * 70)
    print("S.5.4c.2: remove double JSON.stringify in api.ts")
    print("=" * 70)

    src = API_TS.read_text()

    print("\n[1/3] audit — every `body: JSON.stringify(...)` in api.ts")
    hits = audit(src)
    if hits:
        for line_no, text in hits:
            print(f"  line {line_no}: {text}")
    else:
        print("  (none — nothing double-encodes)")

    print("\n[2/3] patch credential methods")
    for old, new, label in FIXES:
        if new in src and old not in src:
            print(f"  ↷ {label}: already fixed")
            continue
        if old not in src:
            print(f"  ⚠ {label}: pattern not found — inspect manually")
            continue
        src = src.replace(old, new, 1)
        print(f"  ✓ {label}: removed JSON.stringify")

    API_TS.write_text(src)

    print("\n[3/3] tsc --noEmit")
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
    print("✅ S.5.4c.2 complete")
    print()
    print("Now reload the browser and retry. Fake key should give a readable")
    print("error (400 with reason=invalid_key), not 422.")
    print("=" * 70)


if __name__ == "__main__":
    main()
