"""S.6.4.8 — verify must not fail open.

S.6.4.7 made truncated responses visible: `ai.truncated` log lines
appeared for purpose=trading.verify with finish_reason=length,
max_tokens=200, text_len=0. But verify_post treated an empty response
as "all claims supported" and returned (True, []). That's a fail-open:

  truncation → exception or empty text → parse → [] → (True, [])
  network error → exception → (True, [])
  rate limit → retry once → exception → (True, [])

Any failure in the verify layer was silently converted to "everything
is fine." A post with fabricated claims would be published because the
verifier couldn't be reached.

Fix:

  1. _MAX_TOKENS_VERIFY = 800 — the verify model returns a claims
     list, not a post. 200 tokens truncated mid-response for long
     posts; 800 gives headroom.
  2. _parse_unsupported_nums now returns None for an unparseable or
     empty response, distinct from [] for "UNSUPPORTED: NONE".
  3. verify_post:
       - first attempt: _MAX_TOKENS_VERIFY
       - if unparseable or exception: retry once with 2x budget
       - if retry also fails: return (False, ["verification_incomplete"])
         and log verify.failed_closed
     The post is written as a draft with a _rejection reason (existing
     persist_post behaviour). A human decides.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBBASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def apply_once(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src:
        print(f"  ↷ {label}: already applied")
        return
    if old not in src:
        fail(f"{label}: anchor not found\n--- expected (first 500) ---\n{old[:500]}")
    path.write_text(src.replace(old, new, 1))
    try:
        ast.parse(path.read_text())
    except SyntaxError as e:
        fail(f"after '{label}': {e}")
    print(f"  ✓ {label}")


def step1_constant() -> None:
    print("\n[1/3] base.py — _MAX_TOKENS_VERIFY")
    apply_once(
        SUBBASE,
        '''# S.6.4.7 — output token budget for reasoning-model subblock generation.
# gpt-oss-20b is a reasoning model: max_tokens covers the hidden
# reasoning trace AND the visible content. At 3000, a 350-word target
# often consumed the whole budget on reasoning and returned empty
# content with finish_reason='length'. 6000 gives headroom for the
# reasoning trace plus the post. Do NOT lower this without checking
# the observed out_tokens of the longest subblock (class_room ~4600).
_MAX_TOKENS_LONG = 6000''',
        '''# S.6.4.7 — output token budget for reasoning-model subblock generation.
# gpt-oss-20b is a reasoning model: max_tokens covers the hidden
# reasoning trace AND the visible content. At 3000, a 350-word target
# often consumed the whole budget on reasoning and returned empty
# content with finish_reason='length'. 6000 gives headroom for the
# reasoning trace plus the post. Do NOT lower this without checking
# the observed out_tokens of the longest subblock (class_room ~4600).
_MAX_TOKENS_LONG = 6000

# S.6.4.8 — verify output budget. The verifier returns
# 'UNSUPPORTED: NONE' or 'UNSUPPORTED: 1, 3, 7' — small. But the
# model may emit reasoning tokens before the answer, and 200
# truncated mid-response. When it truncates, the previous code
# returned (True, []) — fail-open. 800 gives headroom; the retry
# in verify_post uses 2x this if the first attempt is unparseable.
_MAX_TOKENS_VERIFY = 800''',
        "add _MAX_TOKENS_VERIFY",
    )


def step2_parse_return_none() -> None:
    print("\n[2/3] base.py — _parse_unsupported_nums returns None on unparseable")
    src = SUBBASE.read_text()

    # Locate the function. The current version returns [] for NONE, and
    # presumably [] for anything unparseable. We need to distinguish.
    fn_start = src.find("def _parse_unsupported_nums(")
    if fn_start < 0:
        fail("_parse_unsupported_nums not found")
    fn_end = src.find("\ndef ", fn_start + 10)
    if fn_end < 0:
        fn_end = src.find("\nasync def ", fn_start + 10)
    if fn_end < 0:
        fn_end = src.find("\n# ", fn_start + 10)
    if fn_end < 0:
        fail("could not find end of _parse_unsupported_nums")

    body = src[fn_start:fn_end]
    if "return None" in body and "None for unparseable" in body:
        print("  ↷ already applied")
        return

    # Show the current body — we need to see it to replace it correctly.
    print("  ── current body ──")
    print(body)
    print("  ── end current body ──")

    fail("STOP — paste the current _parse_unsupported_nums body and I'll write the exact replacement. "
         "The function's current shape determines how the None return is added.")


def main() -> None:
    print("=" * 70)
    print("S.6.4.8 — verify must not fail open")
    print("=" * 70)
    step1_constant()
    step2_parse_return_none()
    print()
    print("=" * 70)
    print("Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
