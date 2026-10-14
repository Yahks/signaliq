"""
SignalIQ — Phase T.3.1.3: Fix verbosity + regeneration fallback.

Problems from T.3.1.2:
  1. Tightened prompt made LLM 2.4x more verbose (510 → 1234 output tokens)
  2. max_tokens=2000 too tight; retry hit the cap mid-JSON
  3. Regeneration exception killed the generator instead of falling back

Fixes:
  1. stock_lists: prompt says "one sentence per list, not per row"
  2. base.py: max_tokens 2000 → 3000
  3. base.py: wrap regeneration in try/except → fall back to draft 1
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STOCK_LISTS = ROOT / "packages" / "trading" / "subblocks" / "stock_lists.py"
BASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"\n❌ {path.relative_to(ROOT)} does not exist")
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:500]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    check = path.read_text()
    if new not in check:
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new text not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. Reduce verbosity in stock_lists prompt
# ─────────────────────────────────────────────────────────────────────

def step1_reduce_verbosity() -> None:
    print("\n[1/3] Reduce verbosity in stock_lists prompt")

    _patch_and_verify(
        STOCK_LISTS,
        '''        f"Write a short market roundup for {subblock['name']}. "
        f"Structure: (1) headline — what the numbers show, "
        f"(2) notable movers — list them with their exact % change, "
        f"(3) one honest caveat about single-day moves.\\n\\n"
        f"IMPORTANT — epistemic discipline:\\n"
        f"- State ONLY what the numbers show. Do NOT speculate about causes,\\n"
        f"  catalysts, reasons, or 'investor interest'. You have no news data;\\n"
        f"  you cannot know WHY a stock moved.\\n"
        f"- Do NOT write phrases like 'driven by', 'due to', 'after',\\n"
        f"  'following', 'amid', 'reflecting', or 'indicating'.\\n"
        f"- For each mover, describe the move itself (direction, magnitude),\\n"
        f"  not the reason.\\n"
        f"- Do NOT give buy/sell advice."''',
        '''        f"Write a short market roundup for {subblock['name']}.\\n\\n"
        f"Structure (be BRIEF — the tables carry the detail):\\n"
        f"1. Headline: 1-2 sentences summarizing the most notable move.\\n"
        f"2. The three tables (gainers, losers, active) — already provided.\\n"
        f"3. ONE short sentence per table (2-3 sentences total after tables).\\n"
        f"   NOT one sentence per row. Do not narrate every line.\\n"
        f"4. One honest caveat about single-day moves.\\n\\n"
        f"Epistemic discipline:\\n"
        f"- State ONLY what the numbers show.\\n"
        f"- Do NOT speculate about causes. Do NOT write 'driven by',\\n"
        f"  'due to', 'reflecting', 'indicating', 'amid', 'following'.\\n"
        f"- Total prose: 100-150 words. Tables are not counted.\\n"
        f"- Do NOT give buy/sell advice."''',
        "generator prompt — briefer, one sentence per table",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. Raise max_tokens
# ─────────────────────────────────────────────────────────────────────

def step2_raise_max_tokens() -> None:
    print("\n[2/3] Raise max_tokens to 3000 in call_llm_json")

    _patch_and_verify(
        BASE,
        '''    text = await complete(
        role="reasoning",
        system=full_system,
        user=full_user,
        org_id=org_id,
        purpose=prompt_name,
        temperature=0.3,
        max_tokens=2000,
        json_mode=True,
    )''',
        '''    text = await complete(
        role="reasoning",
        system=full_system,
        user=full_user,
        org_id=org_id,
        purpose=prompt_name,
        temperature=0.3,
        max_tokens=3000,
        json_mode=True,
    )''',
        "max_tokens 2000 → 3000",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Wrap regeneration in try/except (fail-open to first draft)
# ─────────────────────────────────────────────────────────────────────

def step3_fail_open_regeneration() -> None:
    print("\n[3/3] Wrap regeneration in try/except — fall back to first draft")

    _patch_and_verify(
        BASE,
        '''    if 1 <= len(unsupported) <= 3:
        log.info("verify.regenerating", count=len(unsupported))
        forbidden = "\\n".join(f"- {c}" for c in unsupported)
        retry_user = (
            f"{user}\\n\\n"
            f"IMPORTANT — the previous draft contained claims that were NOT "
            f"supported by the source data:\\n{forbidden}\\n\\n"
            f"Rewrite the post with TWO rules:\\n"
            f"1. Do NOT restate any of the forbidden claims, in any form.\\n"
            f"2. State ONLY what the source numbers show. Do NOT add\\n"
            f"   interpretation, causal language ('due to', 'because',\\n"
            f"   'reflecting', 'indicating', 'driven by'), or speculation\\n"
            f"   about investor sentiment. Describe the move; do not explain it.\\n\\n"
            f"If a sentence cannot be written without violating these rules, "
            f"omit the sentence entirely rather than rewrite it."
        )
        post2 = await call_llm_json(
            session,
            org_id=org_id,
            system=system,
            user=retry_user,
            prompt_name=prompt_name + ".retry",
            target_words=target_words,
        )
        post2.regenerated = True
        ok2, unsupported2 = await verify_post(
            session, org_id=org_id, post=post2, source_payload=source_payload,
        )
        if ok2:
            post2.verification_ok = True
            return post2
        post2.verification_ok = False
        post2.unsupported_claims = unsupported2
        return post2''',
        '''    if 1 <= len(unsupported) <= 3:
        log.info("verify.regenerating", count=len(unsupported))
        forbidden = "\\n".join(f"- {c}" for c in unsupported)
        retry_user = (
            f"{user}\\n\\n"
            f"IMPORTANT — the previous draft contained claims that were NOT "
            f"supported by the source data:\\n{forbidden}\\n\\n"
            f"Rewrite the post with TWO rules:\\n"
            f"1. Do NOT restate any of the forbidden claims, in any form.\\n"
            f"2. State ONLY what the source numbers show. Do NOT add\\n"
            f"   interpretation, causal language ('due to', 'because',\\n"
            f"   'reflecting', 'indicating', 'driven by'), or speculation\\n"
            f"   about investor sentiment. Describe the move; do not explain it.\\n\\n"
            f"If a sentence cannot be written without violating these rules, "
            f"omit the sentence entirely rather than rewrite it."
        )

        try:
            post2 = await call_llm_json(
                session,
                org_id=org_id,
                system=system,
                user=retry_user,
                prompt_name=prompt_name + ".retry",
                target_words=target_words,
            )
        except Exception as exc:
            # Regeneration failed (rate limit, JSON validation, network).
            # Fail open: accept the first draft, mark it draft so a human reviews.
            log.warning(
                "verify.regen_failed_falling_back",
                error=str(exc)[:200],
                unsupported=len(unsupported),
            )
            post.verification_ok = False
            post.unsupported_claims = unsupported
            return post

        post2.regenerated = True
        ok2, unsupported2 = await verify_post(
            session, org_id=org_id, post=post2, source_payload=source_payload,
        )
        if ok2:
            post2.verification_ok = True
            return post2
        post2.verification_ok = False
        post2.unsupported_claims = unsupported2
        return post2''',
        "regeneration fail-open",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Re-run
# ─────────────────────────────────────────────────────────────────────

def step4_rerun() -> bool:
    print("\n[4/4] Re-run stock_lists generator")
    r = subprocess.run(
        ["python", "scripts/run_stock_lists_once.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    print(r.stdout[-5000:] if len(r.stdout) > 5000 else r.stdout)
    if r.returncode != 0:
        print("  ❌ failed:")
        print(r.stderr[-1500:])
        return False
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.3.1.3: Verbosity + regeneration fallback")
    print("=" * 70)

    step1_reduce_verbosity()
    step2_raise_max_tokens()
    step3_fail_open_regeneration()
    ok = step4_rerun()

    print("=" * 70)
    if ok:
        print("✅ T.3.1.3 complete")
        sys.exit(0)
    print("❌ T.3.1.3 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
