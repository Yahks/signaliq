"""S.6.2.3b — classify content-pipeline errors.

A user pasted a single-stock technical post and got:
    The chain build failed.
    Try again. If it keeps failing, the error details below may help
    the admin diagnose it.

That's the classifier's 'unknown' fallback. The real error was
'no_coherent_chain' — the LLM correctly refused to build a chain from
a text with only one real event.

Content-pipeline errors are the ones users hit most often in normal use
(they're not provider failures). Add them to the classifier, and fix the
frontend so the raw error always appears beneath the friendly message
when the classifier returns 'unknown'.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ERROR_TEXT = ROOT / "packages" / "ai" / "error_text.py"
PAGE = ROOT / "apps" / "web" / "src" / "app" / "(app)" / "chains" / "new" / "page.tsx"
WEB = ROOT / "apps" / "web"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() and new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n"
             f"--- expected ---\n{old[:500]}")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. error_text.py — add content-pipeline cases
# ─────────────────────────────────────────────────────────────────────

def step1_classifier() -> None:
    print("\n[1/3] error_text.py — content-pipeline cases")

    # Insert the content-pipeline checks right before the "Default — unknown"
    # block, so they take priority over the generic fallback but don't
    # override the provider-specific cases above.
    patch(
        ERROR_TEXT,
        '''    # Default — unknown but not raw
    return {
        "kind": "unknown",
        "message": "The chain build failed.",
        "action": (
            "Try again. If it keeps failing, the error details below may help "
            "the admin diagnose it."
        ),
        "provider": provider,
    }''',
        '''    # ── Content-pipeline outcomes (not provider errors) ──────────────
    # These are the failures users hit most often in normal use: the
    # pipeline ran cleanly but the input didn't fit the shape it needs.
    # They deserve a specific message and a specific action.

    if "no_coherent_chain" in lower:
        return {
            "kind": "no_chain",
            "message": (
                "The AI couldn't find a coherent story in that text. "
                "It looks like the content describes a single event or "
                "observation, but a chain needs a sequence of related "
                "events to build from."
            ),
            "action": (
                "Try pasting a longer article or a news story with multiple "
                "events. Chains work best with 4-8 distinct things that "
                "happened over time."
            ),
            "provider": None,
        }

    if "too_few_events" in lower or "too few" in lower:
        return {
            "kind": "too_few_events",
            "message": "The AI found fewer than 2 distinct events in your text.",
            "action": (
                "Try pasting a longer article, or one that covers several "
                "related happenings."
            ),
            "provider": None,
        }

    if "verify_failed" in lower or "too_many_unsupported" in lower:
        return {
            "kind": "verify_failed",
            "message": (
                "The article was written, but the fact-checker couldn't "
                "confirm enough of its claims."
            ),
            "action": (
                "This is usually temporary — try again. If it persists, "
                "contact your admin."
            ),
            "provider": None,
        }

    # ── Default — unknown but not raw ────────────────────────────────
    return {
        "kind": "unknown",
        "message": "The chain build failed.",
        "action": (
            "Try again. If it keeps failing, the details below may help "
            "your admin diagnose it."
        ),
        "provider": provider,
    }''',
        "add no_chain / too_few_events / verify_failed",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Frontend — always show the raw error when the classifier is unsure
# ─────────────────────────────────────────────────────────────────────

def step2_frontend() -> None:
    print("\n[2/3] page.tsx — show raw error on unknown")

    patch(
        PAGE,
        '''            {phase === "failed" && (
              <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm">
                <p className="font-medium text-red-400">
                  Couldn't build a chain
                </p>
                {status.data?.error_message ? (
                  <>
                    <p className="mt-2 text-red-400">
                      {status.data.error_message}
                    </p>
                    {status.data.error_action && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {status.data.error_action}
                      </p>
                    )}
                  </>
                ) : (
                  // Fallback when the backend couldn't classify: show a
                  // shortened version of the raw error rather than the
                  // whole JSON blob.
                  <p className="mt-2 text-red-400 text-xs break-all">
                    {(reason || "Unknown error").slice(0, 240)}
                    {(reason || "").length > 240 ? "…" : ""}
                  </p>
                )}
              </div>
            )}''',
        '''            {phase === "failed" && (
              <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm">
                <p className="font-medium text-red-400">
                  Couldn't build a chain
                </p>
                {status.data?.error_message && (
                  <p className="mt-2 text-red-400">
                    {status.data.error_message}
                  </p>
                )}
                {status.data?.error_action && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {status.data.error_action}
                  </p>
                )}
                {/* S.6.2.3b — always surface the raw error beneath the
                    friendly message, especially when the classifier
                    returned 'unknown'. Collapsed so it doesn't dominate
                    the card, expanded on click. */}
                {(status.data?.error_kind === "unknown" || !status.data?.error_kind) && reason && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                      Technical details
                    </summary>
                    <p className="mt-1 text-xs text-muted-foreground break-all font-mono">
                      {reason.slice(0, 400)}
                      {reason.length > 400 ? "…" : ""}
                    </p>
                  </details>
                )}
              </div>
            )}''',
        "show raw error under details on unknown",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Verify
# ─────────────────────────────────────────────────────────────────────

def step3_verify() -> bool:
    print("\n[3/3] verify")

    # Syntax
    for f in [ERROR_TEXT]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ error_text.py parses")

    # Classifier test including the new cases
    print("\n  classifier test (with content-pipeline cases)")
    code = r'''
from packages.ai.error_text import classify

cases = [
    # Existing
    ('Groq HTTP 429: {"error":{"message":"Rate limit reached..."}}', "rate_limit"),
    ('Groq HTTP 401: {"error":{"message":"Invalid API Key"}}', "invalid_key"),
    ('Groq HTTP 400: {"error":{"message":"json_validate_failed"}}', "json_validate"),
    ('Groq HTTP 429: {"error":{"message":"You exceeded your current quota"}}', "quota"),
    ('Groq connection failed: timed out', "unreachable"),
    ('No API credentials configured for this organisation.', "no_credentials"),
    # New content-pipeline cases
    ('no_coherent_chain', "no_chain"),
    ('pipeline_failed:no_coherent_chain', "no_chain"),
    ('too_few_events', "too_few_events"),
    ('verify_failed:All models failed. Last: ...', "verify_failed"),
    # Genuinely unknown
    ('Something completely unexpected happened', "unknown"),
    (None, "unknown"),
]

ok = True
for raw, expected in cases:
    c = classify(raw)
    got = c.get("kind")
    mark = "✓" if got == expected else "❌"
    msg = (c.get("message") or "")[:60]
    print(f"  {mark} {str(raw)[:45]:45} → {got:15} {msg!r}")
    if got != expected:
        ok = False

raise SystemExit(0 if ok else 1)
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=15,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-300:])
        return False

    # tsc
    print("  tsc --noEmit")
    r = subprocess.run(
        ["./node_modules/.bin/tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        print("  ❌ TS errors:")
        for line in (r.stdout or r.stderr).splitlines()[:10]:
            print(f"     {line}")
        return False
    print("  ✓ TypeScript clean")

    # Restart API
    print("  restart API")
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    with Path("/tmp/signaliq-api.log").open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(6)
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout}")
        return False
    print("  ✓ API up")

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.2.3b — classify content-pipeline errors")
    print("=" * 70)

    step1_classifier()
    step2_frontend()
    ok = step3_verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.2.3b complete")
        print("   no_coherent_chain now has a specific message + action")
        print("   the raw error is reachable under 'Technical details'")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
