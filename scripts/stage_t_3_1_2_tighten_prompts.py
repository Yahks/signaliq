"""
SignalIQ — Phase T.3.1.2: Tighten generator + regeneration prompts.

Problem: The verifier was doing too much work. The generator produced
soft hallucinations ('indicating strong investor interest') that the
verifier flagged but which the LLM shouldn't have generated.

Fix:
  1. stock_lists.py: user prompt forbids causal speculation
  2. base.py: regeneration directive is stronger + clearer

Every future generator inherits the improved regeneration prompt.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STOCK_LISTS = ROOT / "packages" / "trading" / "subblocks" / "stock_lists.py"
BASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    """Patch + immediately verify the result. Fail loudly on mismatch."""
    if not path.exists():
        raise SystemExit(f"\n❌ {path.relative_to(ROOT)} does not exist")
    src = path.read_text()

    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return

    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:400]}\n---"
        )

    new_src = src.replace(old, new, 1)
    path.write_text(new_src)

    # VERIFY: re-read the file, confirm the new text is present and old is gone
    check = path.read_text()
    if new not in check:
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new text not found — BUG")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. stock_lists.py — forbid causal speculation in the generator
# ─────────────────────────────────────────────────────────────────────

def step1_tighten_generator() -> None:
    print("\n[1/2] Tighten stock_lists generator prompt")

    _patch_and_verify(
        STOCK_LISTS,
        '''    user_prompt = (
        f"Today's market movers from StockAnalysis.com. "
        f"Use these exact tickers and numbers in your write-up. "
        f"Note: the 'change' column for the most-active list shows volume, "
        f"not percent change — reflect that in your framing.\\n\\n"
        f"{payload_md}\\n\\n"
        f"Write a short market roundup for {subblock['name']}. "
        f"Cover: (1) headline — what stands out, (2) notable movers with context, "
        f"(3) one honest caveat about single-day moves. "
        f"Do not give buy/sell advice."
    )''',
        '''    user_prompt = (
        f"Today's market movers from StockAnalysis.com. "
        f"Use these exact tickers and numbers in your write-up. "
        f"Note: the 'change' column for the most-active list shows volume, "
        f"not percent change — reflect that in your framing.\\n\\n"
        f"{payload_md}\\n\\n"
        f"Write a short market roundup for {subblock['name']}. "
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
        f"- Do NOT give buy/sell advice."
    )''',
        "generator prompt — forbids causal speculation",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. base.py — stronger regeneration directive
# ─────────────────────────────────────────────────────────────────────

def step2_tighten_regeneration() -> None:
    print("\n[2/2] Strengthen regeneration prompt in base.py")

    _patch_and_verify(
        BASE,
        '''        forbidden = "\\n".join(f"- {c}" for c in unsupported)
        retry_user = (
            f"{user}\\n\\n"
            f"IMPORTANT — the previous draft contained claims NOT supported by "
            f"the source data. Do NOT state any of the following:\\n{forbidden}\\n\\n"
            f"If you cannot make a claim without this context, omit it entirely."
        )''',
        '''        forbidden = "\\n".join(f"- {c}" for c in unsupported)
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
        )''',
        "regeneration directive — state only what numbers show",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Re-run
# ─────────────────────────────────────────────────────────────────────

def step3_rerun() -> bool:
    print("\n[3/3] Re-run stock_lists generator")
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
    print("SignalIQ — Phase T.3.1.2: Tighten generator + regeneration prompts")
    print("=" * 70)

    step1_tighten_generator()
    step2_tighten_regeneration()
    ok = step3_rerun()

    print("=" * 70)
    if ok:
        print("✅ T.3.1.2 complete — generator stops producing soft hallucinations")
        print()
        print("Expected on next run:")
        print("  - verify.all_supported on FIRST pass (no regeneration needed)")
        print("  - OR: regeneration but the regenerated body is clean")
        sys.exit(0)
    print("❌ T.3.1.2 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
