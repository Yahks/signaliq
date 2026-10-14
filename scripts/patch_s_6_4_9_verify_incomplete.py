"""S.6.4.9 — verify_incomplete is not a claim; don't regenerate on it.

S.6.4.8 made verify_post fail closed: when both verify attempts fail
or return unparseable output, it returns (False, ["verification_incomplete"]).
That flows through call_llm_json_verified's "1 <= len(unsupported) <= 3"
branch, which triggers a regen with forbidden = ["verification_incomplete"].

The regen prompt then says:

    IMPORTANT — the previous draft contained claims that were NOT
    supported by the source data:
    - verification_incomplete

The model has no idea what "verification_incomplete" means. It rewrites
generically, burning a full reasoning call (~2,000 tokens) for no
reason. The regen happens to succeed because the model produces a
different draft, not because the instruction helped.

Two fixes:

  1. Introduce a module-level sentinel. failed_closed returns
     (False, [_VERIFICATION_INCOMPLETE]). call_llm_json_verified
     checks for it FIRST, before the regen branch, and short-circuits:
     mark verification_ok=False, set a human-readable unsupported
     reason, return. No regen.

  2. The verify retry (attempt 2 at 2x budget) currently hits the same
     cache entry as attempt 1 — the cache key doesn't include
     max_tokens. So when attempt 1 is a cache hit on a poisoned entry,
     attempt 2 is too, and _parse_unsupported_nums returns None twice.
     Pass use_cache=False on attempt 2 so it forces a real LLM call.

The 12 poisoned verify rows were deleted from ai_calls before this
commit; combined with S.6.4.7 (which stops new truncations from being
cached) and this commit, a verify cache entry is now only usable if it
contains the UNSUPPORTED: marker, and only the first attempt reads
from cache.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBBASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"

MARK = "S.6.4.9"


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


def step1_sentinel() -> None:
    print("\n[1/3] base.py — _VERIFICATION_INCOMPLETE sentinel")
    apply_once(
        SUBBASE,
        '''# S.6.4.8 — verify output budget. The verifier returns
# 'UNSUPPORTED: NONE' or 'UNSUPPORTED: 1, 3, 7' — small. But the
# model may emit reasoning tokens before the answer, and 200
# truncated mid-response. When it truncates, the previous code
# returned (True, []) — fail-open. 800 gives headroom; the retry
# in verify_post uses 2x this if the first attempt is unparseable.
_MAX_TOKENS_VERIFY = 800''',
        '''# S.6.4.8 — verify output budget. The verifier returns
# 'UNSUPPORTED: NONE' or 'UNSUPPORTED: 1, 3, 7' — small. But the
# model may emit reasoning tokens before the answer, and 200
# truncated mid-response. When it truncates, the previous code
# returned (True, []) — fail-open. 800 gives headroom; the retry
# in verify_post uses 2x this if the first attempt is unparseable.
_MAX_TOKENS_VERIFY = 800

# S.6.4.9 — sentinel returned by verify_post when both verify attempts
# failed. call_llm_json_verified checks for this BEFORE the regen
# branch: the regen prompt would otherwise include the literal string
# "verification_incomplete" as a "forbidden claim", which is
# meaningless to the model. Instead, mark the post unverified and let
# persist_post write it as a draft.
_VERIFICATION_INCOMPLETE = "__verification_incomplete__"''',
        "add _VERIFICATION_INCOMPLETE",
    )


def step2_failed_closed_sentinel() -> None:
    print("\n[2/3] base.py — failed_closed returns the sentinel")
    apply_once(
        SUBBASE,
        '''    if bad_nums is None:
        # Both attempts failed or returned garbage. Do not fail open.
        log.warning("verify.failed_closed", claims=len(claims))
        return False, ["verification_incomplete"]''',
        '''    if bad_nums is None:
        # Both attempts failed or returned garbage. Do not fail open.
        # S.6.4.9 — return the sentinel, not a synthetic claim string.
        # call_llm_json_verified checks for the sentinel before the
        # regen branch and short-circuits to verification_ok=False.
        log.warning("verify.failed_closed", claims=len(claims))
        return False, [_VERIFICATION_INCOMPLETE]''',
        "failed_closed returns sentinel",
    )


def step3_caller_shortcircuit() -> None:
    print("\n[3/3] base.py — call_llm_json_verified short-circuits on sentinel")
    src = SUBBASE.read_text()
    if f"{MARK}_sentinel_check" in src:
        print("  ↷ caller short-circuit: already applied")
        return

    # Anchor on the regen condition inside call_llm_json_verified.
    # From the earlier read, the condition is:
    #     if 1 <= len(unsupported) <= 3:
    # preceded by the ok check:
    #     if ok:
    #         post.verification_ok = True
    #         return post
    old = '''    if ok:
        post.verification_ok = True
        return post

    if 1 <= len(unsupported) <= 3:
        log.info("verify.regenerating", count=len(unsupported))'''

    new = '''    if ok:
        post.verification_ok = True
        return post

    # S.6.4.9_sentinel_check — verification could not complete (both
    # attempts failed or returned unparseable output). Do not regen:
    # the regen prompt would list "__verification_incomplete__" as a
    # "forbidden claim", which the model cannot act on. Mark the post
    # unverified and return; persist_post writes it as a draft.
    if unsupported and unsupported[0] == _VERIFICATION_INCOMPLETE:
        log.info("verify.incomplete_marked_draft", claims=len(unsupported))
        post.verification_ok = False
        post.unsupported_claims = [
            "verification could not complete — the fact-checker did not "
            "return a parseable response after two attempts"
        ]
        return post

    if 1 <= len(unsupported) <= 3:
        log.info("verify.regenerating", count=len(unsupported))'''

    if old not in src:
        fail("caller anchor not found — the code around 'if 1 <= len(unsupported)' "
             "does not match. Paste lines 680-730 of base.py.")

    src = src.replace(old, new, 1)

    # Now handle use_cache on the second verify attempt.
    old2 = '''    bad_nums: list[int] | None = None
    import asyncio as _asyncio
    for attempt, budget in ((1, _MAX_TOKENS_VERIFY), (2, _MAX_TOKENS_VERIFY * 2)):
        try:
            raw = await _try_verify(budget)'''

    new2 = '''    bad_nums: list[int] | None = None
    import asyncio as _asyncio
    # S.6.4.9 — attempt 2 disables the cache. The cache key does not
    # include max_tokens, so a poisoned entry from attempt 1 would be
    # served again on attempt 2. Forcing a cache miss makes the retry
    # actually retry the LLM.
    for attempt, budget, use_cache in (
        (1, _MAX_TOKENS_VERIFY, True),
        (2, _MAX_TOKENS_VERIFY * 2, False),
    ):
        try:
            raw = await _try_verify(budget, use_cache=use_cache)'''

    if old2 not in src:
        fail("verify loop anchor not found — paste the current _try_verify loop.")
    src = src.replace(old2, new2, 1)

    # Update _try_verify signature to accept use_cache.
    old3 = '''    async def _try_verify(max_tokens: int) -> str:
        return await complete(
            role="reasoning",
            system=system,
            user=user,
            org_id=org_id,
            prompt_version_id=_pv_verify,
            purpose="trading.verify",
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=False,
        )'''

    new3 = '''    async def _try_verify(max_tokens: int, *, use_cache: bool = True) -> str:
        return await complete(
            role="reasoning",
            system=system,
            user=user,
            org_id=org_id,
            prompt_version_id=_pv_verify,
            purpose="trading.verify",
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=False,
            use_cache=use_cache,
        )'''

    if old3 not in src:
        fail("_try_verify signature anchor not found — paste the current definition.")
    src = src.replace(old3, new3, 1)

    SUBBASE.write_text(src)
    try:
        ast.parse(src)
    except SyntaxError as e:
        fail(f"after caller short-circuit: {e}")
    print("  ✓ caller short-circuits on sentinel; retry uses use_cache=False")


def main() -> None:
    print("=" * 70)
    print("S.6.4.9 — verify_incomplete is not a claim; don't regenerate on it")
    print("=" * 70)
    step1_sentinel()
    step2_failed_closed_sentinel()
    step3_caller_shortcircuit()
    print()
    print("=" * 70)
    print("Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
