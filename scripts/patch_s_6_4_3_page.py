"""S.6.4.3 — patch ONLY page.tsx (step 8 of the previous script).

The api.ts patch succeeded before ast.parse was run on it (which is a
Python-only check and shouldn't have been applied to a .ts file). Only
page.tsx still needs its three edits.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "apps" / "web" / "src" / "app" / "(app)" / "settings" / "credentials" / "page.tsx"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n"
             f"--- expected ---\n{old[:400]}")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: wrote but new not found")
    # NOTE: no ast.parse here. page.tsx is TSX, not Python.
    print(f"  ✓ {path.name}: {label}")


def main() -> None:
    print("=" * 70)
    print("S.6.4.3 — patch page.tsx (step 8)")
    print("=" * 70)

    # 1. Add fmtRelative + fmtTokens helpers
    patch(
        PAGE,
        "function statusFor(c: { is_active: boolean; exhausted_until: string | null }, nowMs: number): KeyStatus {",
        '''function fmtRelative(iso: string | null, nowMs: number): string {
  if (!iso) return "\\u2014";
  const ms = nowMs - new Date(iso).getTime();
  if (ms < 60_000) return "just now";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function fmtTokens(n: number | null): string {
  if (n == null) return "\\u2014";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function statusFor(c: { is_active: boolean; exhausted_until: string | null }, nowMs: number): KeyStatus {''',
        "add fmtRelative + fmtTokens helpers",
    )

    # 2. Header columns
    patch(
        PAGE,
        '''                    <th className="text-left py-2 pr-3 font-medium">Key</th>
                    <th className="text-left py-2 pr-3 font-medium">Status</th>''',
        '''                    <th className="text-left py-2 pr-3 font-medium">Key</th>
                    <th className="text-right py-2 pr-3 font-medium">Groq tokens</th>
                    <th className="text-right py-2 pr-3 font-medium">Groq remaining</th>
                    <th className="text-left py-2 pr-3 font-medium">Status</th>''',
        "table header",
    )

    # 3. Row cells
    patch(
        PAGE,
        '''                      <td className="py-2 pr-3 font-mono text-xs">{c.key_preview}</td>
                      <td className="py-2 pr-3">
                        {(() => {
                          const s = statusFor(c, nowMs);
                          return <span className={s.className}>{s.text}</span>;
                        })()}
                      </td>''',
        '''                      <td className="py-2 pr-3 font-mono text-xs">{c.key_preview}</td>
                      <td className="py-2 pr-3 text-right font-mono text-xs">
                        {fmtTokens(c.tokens_used)}
                        <div className="text-[10px] text-muted-foreground">
                          {c.calls_made} call{c.calls_made === 1 ? "" : "s"}
                        </div>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono text-xs">
                        {fmtTokens(c.last_remaining_tokens)}
                        <div className="text-[10px] text-muted-foreground">
                          {fmtRelative(c.last_rate_at, nowMs)}
                        </div>
                      </td>
                      <td className="py-2 pr-3">
                        {(() => {
                          const s = statusFor(c, nowMs);
                          return <span className={s.className}>{s.text}</span>;
                        })()}
                      </td>''',
        "table row cells",
    )

    print()
    print("=" * 70)
    print("All page.tsx patches applied.")
    print("Next: tsc check + File 3 (verify).")
    print("=" * 70)


if __name__ == "__main__":
    main()
