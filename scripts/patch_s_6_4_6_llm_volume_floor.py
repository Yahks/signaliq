"""S.6.4.6 — reject undersized LLM output before verification.

call_llm_json_verified trusts whatever call_llm_json returns. If the model
emits 19 words for a 260-word target — usually as a refusal ("requires
inspection of SEC filings", "the data isn't available") — verify_post
sees a single, true claim and returns ok=True. The post then persists as
`published`. Three such posts were found in trading_posts (16, 19, 25
words, all status='published'), plus a 0-word row in generated_posts.

The fix: a word-count floor of `int(target_words * 0.5)` inside
call_llm_json_verified, applied right after call_llm_json returns and
before verify_post is called. If the first call is under the floor:

  1. Log llm.post_undersized with the word count, target, and a body
     preview (so the failure is diagnosable from the log alone).
  2. Retry once with an explicit "your previous response was N words;
     the target is M; write at least M" message appended to the user
     prompt.
  3. If the retry is also under the floor, RAISE ValueError. No row is
     persisted. The subblock task logs an error and moves on. Decision C
     from the design: a refusal is not content that needs human review;
     it is a subblock failure.

Also strips literal 'TITLE:' / 'BODY:' prefixes from the parsed post —
one of the observed rows had the model echoing a labeled format back
into the title and body.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"

MARK = "S.6.4.6"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def apply_once(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if MARK in src and new.strip() in src:
        print(f"  ↷ {label}: already applied")
        return
    if old not in src:
        fail(f"{label}: pattern not found in {path.name}\n"
             f"--- expected (first 400 chars) ---\n{old[:400]}")
    path.write_text(src.replace(old, new, 1))
    try:
        ast.parse(path.read_text())
    except SyntaxError as e:
        fail(f"{path.name}: syntax error after '{label}': {e}")
    print(f"  ✓ {label}")


def step1_add_constant() -> None:
    print("\n[1/3] base.py — module constant + helper")
    apply_once(
        BASE,
        '''log = get_logger(__name__)''',
        '''log = get_logger(__name__)

# S.6.4.6 — minimum acceptable length as a fraction of target_words.
# A 260-word target refuses anything under 130 words; a 150-word target
# refuses anything under 75. This catches refusals like
# "requires inspection of SEC filings" before verification, which would
# otherwise see a single true claim and accept the post.
_MIN_WORDS_RATIO = 0.5


def _strip_label_prefix(s: str) -> str:
    """S.6.4.6 — drop a leading 'TITLE:' or 'BODY:' the model may echo."""
    t = (s or "").lstrip()
    for prefix in ("TITLE:", "BODY:"):
        if t.upper().startswith(prefix):
            t = t[len(prefix):].lstrip()
    return t''',
        "add _MIN_WORDS_RATIO + _strip_label_prefix",
    )


def step2_guard_in_verified() -> None:
    print("\n[2/3] call_llm_json_verified — volume floor + retry + raise")
    apply_once(
        BASE,
        '''    post = await call_llm_json(
        session,
        org_id=org_id,
        system=system,
        user=user,
        prompt_name=effective_prompt,
        target_words=target_words,
    )

    ok, unsupported = await verify_post(
        session, org_id=org_id, post=post, source_payload=source_payload,
    )''',
        '''    post = await call_llm_json(
        session,
        org_id=org_id,
        system=system,
        user=user,
        prompt_name=effective_prompt,
        target_words=target_words,
    )

    # S.6.4.6 — reject undersized output before verification. A refusal
    # like "requires inspection of SEC filings" is under the floor; a
    # real post is not. Verify_post can't catch this — the refusal's
    # single claim is true — so the check must happen here.
    floor = int(target_words * _MIN_WORDS_RATIO)
    if post.word_count < floor:
        log.warning(
            "llm.post_undersized",
            prompt=prompt_name,
            words=post.word_count,
            target=target_words,
            floor=floor,
            preview=(post.body or "")[:200],
        )
        retry_user = (
            f"{user}\\n\\n"
            f"IMPORTANT — your previous response was only {post.word_count} "
            f"words. The target is {target_words} words. Write at least "
            f"{floor} words. Do not refuse; if a specific number is "
            f"missing, write what the available data does show and say "
            f"plainly which figures are not provided."
        )
        try:
            post = await call_llm_json(
                session,
                org_id=org_id,
                system=system,
                user=retry_user,
                prompt_name=prompt_name + ".lenretry",
                target_words=target_words,
            )
        except Exception as exc:
            raise ValueError(
                f"llm produced {post.word_count} words (floor {floor}) "
                f"and the length retry failed: {exc}"
            ) from exc

        if post.word_count < floor:
            raise ValueError(
                f"llm produced {post.word_count} words twice "
                f"(floor {floor}, target {target_words}) for prompt "
                f"{prompt_name!r}; refusing to persist"
            )

    ok, unsupported = await verify_post(
        session, org_id=org_id, post=post, source_payload=source_payload,
    )''',
        "add volume floor with retry + raise",
    )


def step3_strip_label_prefixes() -> None:
    print("\n[3/3] call_llm_json — strip TITLE:/BODY: from BOTH call sites")
    src = BASE.read_text()
    if MARK + "_strip" in src:
        print("  ↷ TITLE/BODY strip: already applied")
        return

    # Happy path — the parsed post. Wrap title= and body= with the
    # prefix stripper so a parser miss can't leak 'TITLE:'/'BODY:' into
    # the final post.
    old_happy = (
        "    return TradingPost(\n"
        "        title=(title or \"Untitled\")[:500],\n"
        "        body=body,\n"
        "        word_count=len(body.split()),\n"
        "    )"
    )
    new_happy = (
        "    # S.6.4.6_strip — belt-and-braces: even if _parse_title_body\n"
        "    # handed back a prefix, strip it here before constructing.\n"
        "    return TradingPost(\n"
        "        title=_strip_label_prefix((title or \"Untitled\")[:500]),\n"
        "        body=_strip_label_prefix(body),\n"
        "        word_count=len(_strip_label_prefix(body).split()),\n"
        "    )"
    )

    # Fallback path — raw text, which is the one that actually leaked
    # 'TITLE: ...' into the body in the observed row.
    old_fallback = (
        "        return TradingPost(\n"
        "            title=prompt_name.replace(\".\", \" \").title(),\n"
        "            body=text or \"(empty response)\",\n"
        "            word_count=len((text or \"\").split()),\n"
        "        )"
    )
    new_fallback = (
        "        # S.6.4.6_strip — the parser bailed, so 'text' is the raw\n"
        "        # model output including 'TITLE:' / 'BODY:' lines. Strip\n"
        "        # them before persisting.\n"
        "        _clean = _strip_label_prefix(text or \"(empty response)\")\n"
        "        return TradingPost(\n"
        "            title=_strip_label_prefix(prompt_name.replace(\".\", \" \").title()),\n"
        "            body=_clean,\n"
        "            word_count=len(_clean.split()),\n"
        "        )"
    )

    for old, new, label in (
        (old_happy, new_happy, "happy-path TradingPost"),
        (old_fallback, new_fallback, "fallback-path TradingPost"),
    ):
        if new.strip() in src:
            print(f"  ↷ {label}: already wrapped")
            continue
        if old not in src:
            fail(f"{label}: anchor not found\n--- expected ---\n{old}")
        src = src.replace(old, new, 1)
        print(f"  ✓ {label}: wrapped")

    BASE.write_text(src)
    try:
        ast.parse(src)
    except SyntaxError as e:
        fail(f"base.py: syntax error after TITLE/BODY wraps: {e}")


def main() -> None:
    print("=" * 70)
    print("S.6.4.6 — reject undersized LLM output before verification")
    print("=" * 70)
    step1_add_constant()
    step2_guard_in_verified()
    step3_strip_label_prefixes()
    print()
    print("=" * 70)
    print("Done. call_llm_json_verified now refuses sub-floor responses.")
    print("=" * 70)


if __name__ == "__main__":
    main()
