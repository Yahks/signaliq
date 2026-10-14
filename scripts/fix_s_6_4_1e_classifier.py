"""S.6.4.1e.1 — broaden classifier to match message text.

The classifier matched only the reason CODE ("duplicate_key") but not the
MESSAGE text ("This key is already registered as 'X'"). If a caller
passes just the message — which the frontend does, via error_message —
the classifier returns 'unknown'.

Fix: match on both. Add substring checks for the message text.

The stale-identifier check that was in the previous draft is removed.
It was flagging legitimate uses of `role` (parameters, SQL binding keys,
comments) and its detection heuristic was wrong. The specific bug it was
meant to catch — `role=role` in a log call — is fixed at the source.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ERROR_TEXT = ROOT / "packages" / "ai" / "error_text.py"


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


def step1_broaden_classifier() -> None:
    print("\n[1/2] error_text.py — match message text too")

    patch(
        ERROR_TEXT,
        '    if "duplicate_key" in lower:',
        '''    # S.6.4.1e.1 — match the reason code OR the message text. The
    # frontend often has only the message (error_message), not the reason.
    if ("duplicate_key" in lower
            or "already registered as" in lower):''',
        "duplicate_key: also match message text",
    )

    patch(
        ERROR_TEXT,
        '    if "label_in_use" in lower:',
        '''    if ("label_in_use" in lower
            or "already in use by a different key" in lower
            or "already have a different key labelled" in lower):''',
        "label_in_use: also match message text",
    )


def verify() -> bool:
    print("\n[2/2] verify")

    r = subprocess.run(
        ["python", "-c", f"import ast; ast.parse(open('{ERROR_TEXT}').read())"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ❌ error_text.py: {r.stderr[:300]}")
        return False
    print("  ✓ error_text.py parses")

    code = r'''
import sys
sys.path.insert(0, ".")
from packages.ai.error_text import classify
cases = [
    # Reason codes
    ("duplicate_key: already exists", "duplicate_key"),
    ("label_in_use: my-key", "label_in_use"),
    # Message text (the new cases)
    ("This key is already registered as groq-6c35.", "duplicate_key"),
    ("You already have a different key labelled my-key.", "label_in_use"),
    ("That label is already in use by a different key.", "label_in_use"),
    # Regression — existing cases must still pass
    ("Groq HTTP 429: rate limit reached", "rate_limit"),
    ("Groq HTTP 401: Invalid API Key", "invalid_key"),
    ("no_coherent_chain", "no_chain"),
]
ok = True
for raw, expected in cases:
    got = classify(raw).get("kind")
    mark = "ok " if got == expected else "FAIL"
    print(f"  {mark} {raw[:55]:55} -> {got}")
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
    return True


def main() -> None:
    print("=" * 70)
    print("S.6.4.1e.1 — broaden classifier to match message text")
    print("=" * 70)

    step1_broaden_classifier()
    ok = verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.4.1e.1 complete")
        print()
        print("Restart the API, then test the browser:")
        print("  add the SAME key twice   -> 'already registered'")
        print("  add a key with a used label -> 'label in use'")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
