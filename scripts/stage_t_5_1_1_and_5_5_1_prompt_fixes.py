"""
SignalIQ — Phase T.5.1.1 + T.5.5.1: Two prompt fixes.

1. class_room — was too permissive with numeric examples; verifier
   flagged 7 unsupported claims and the post was marked draft.
   Fix: forbid numeric illustrations entirely.

2. fundamental_metrics — LLM produced a 19-word stub because the
   metrics were already in the prompt and it thought prose was optional.
   Fix: require a markdown table + 150+ words of analysis in the body.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLASS_ROOM = ROOT / "packages" / "trading" / "subblocks" / "class_room.py"
FUNDAMENTAL = ROOT / "packages" / "trading" / "subblocks" / "fundamental_metrics.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(f"\n❌ {path.name}: pattern not found for '{label}'")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# Fix 1 — class_room: forbid numeric examples
# ─────────────────────────────────────────────────────────────────────

def step1_fix_class_room() -> None:
    print("\n[1/3] Fix class_room prompt — no numeric illustrations")

    _patch_and_verify(
        CLASS_ROOM,
        '''PROMPT_TEMPLATE = """Write a short, high-quality educational piece on this topic:

**{topic}**

The reader is a motivated trader or investor. Assume general knowledge of markets,
but no prior familiarity with this specific topic.

Structure (BRIEF — the whole piece is 250-400 words of markdown):
1. A one-paragraph framing of why this topic matters in practice.
2. Two or three short sections with headings.
3. One concrete example (numeric or scenario-based) if it helps.
4. A one-sentence "what to watch for" caveat.

Epistemic discipline:
- No invented statistics, no fake studies, no fake historical dates.
- Concrete examples may use round numbers and be clearly hypothetical —
  label them as such ("for example, if a $100K portfolio…").
- Do NOT give buy/sell advice.
- Do NOT promise returns.

Style notes are in the system prompt.
"""''',
        '''PROMPT_TEMPLATE = """Write a short, high-quality educational piece on this topic:

**{topic}**

The reader is a motivated trader or investor. Assume general knowledge of markets,
but no prior familiarity with this specific topic.

Structure (BRIEF — the whole piece is 250-400 words of markdown):
1. A one-paragraph framing of why this topic matters in practice.
2. Two or three short sections with headings.
3. One conceptual example (scenario-based, NOT numeric).
4. A one-sentence "what to watch for" caveat.

CRITICAL — do NOT invent numbers:
- No numeric examples. Not even hypothetical ones. Not even round ones.
- Explain concepts through words, analogies, and scenarios — not formulas
  with placeholder values like "$100K" or "10 shares" or "2% spread".
- Describe WHY an idea matters in prose. Do not show it working with numbers.
- This applies even when the topic seems to invite a numeric example.

Rationale: the verifier will flag any specific number not present in the
source. Since class_room has no source data, ALL numbers get flagged.
Write as if numbers do not exist.

Other rules:
- No invented studies, dates, or statistics.
- Do NOT give buy/sell advice.
- Do NOT promise returns.

Style notes are in the system prompt.
"""''',
        "class_room prompt — forbid numbers",
    )


# ─────────────────────────────────────────────────────────────────────
# Fix 2 — fundamental_metrics: require prose
# ─────────────────────────────────────────────────────────────────────

def step2_fix_fundamental_metrics() -> None:
    print("\n[2/3] Fix fundamental_metrics prompt — require prose")

    _patch_and_verify(
        FUNDAMENTAL,
        '''    prompt = (
        f"Fundamental metrics for {name} ({symbol}) from StockAnalysis.com.\\\\n\\\\n"
        f"{payload}\\\\n\\\\n"
        f"Write a brief fundamental snapshot. Structure:\\\\n"
        f"1. Headline: one sentence on what the valuation and quality metrics show.\\\\n"
        f"2. Valuation: cite the PE, PS, PB values in context.\\\\n"
        f"3. Quality: cite the ROE, ROA, ROIC values and any margins.\\\\n"
        f"4. One-sentence caveat: fundamental ratios are historical and lag the market.\\\\n\\\\n"
        f"Use ONLY the numbers in the table above.\\\\n"
        f"Do NOT invent metrics, comparisons to peers, or forecasts.\\\\n"
        f"No causal language ('driven by', 'because of', 'reflecting').\\\\n"
        f"~180 words. Do NOT give buy/sell advice."
    )''',
        '''    prompt = (
        f"Fundamental metrics for {name} ({symbol}) from StockAnalysis.com.\\\\n\\\\n"
        f"SOURCE DATA (for your reference — do NOT just repeat this):\\\\n{payload}\\\\n\\\\n"
        f"Write a fundamental snapshot post.\\\\n\\\\n"
        f"REQUIRED STRUCTURE (both parts, in this order):\\\\n"
        f"1. A markdown TABLE containing the metrics above "
        f"(same rows, same columns, same values).\\\\n"
        f"2. At least 150 words of PROSE analysis BELOW the table.\\\\n\\\\n"
        f"Prose must cover:\\\\n"
        f"- Headline sentence on what the valuation and quality metrics show.\\\\n"
        f"- Valuation discussion: cite the PE, PS, PB, EV/EBITDA values in context.\\\\n"
        f"- Quality discussion: cite the ROE, ROA, ROIC values and margins.\\\\n"
        f"- One sentence caveat: fundamental ratios are historical and lag the market.\\\\n\\\\n"
        f"Length check: if the prose below the table is fewer than 150 words, "
        f"it is TOO SHORT. Expand the discussion.\\\\n\\\\n"
        f"Use ONLY the numbers in the table.\\\\n"
        f"Do NOT invent metrics, comparisons to peers, or forecasts.\\\\n"
        f"No causal language ('driven by', 'because of', 'reflecting').\\\\n"
        f"Do NOT give buy/sell advice."
    )''',
        "fundamental_metrics prompt — require table + 150-word prose",
    )

    # Bump target_words to give the LLM headroom
    _patch_and_verify(
        FUNDAMENTAL,
        "        target_words=180,\n    )\n\n    post_id = await persist_post(",
        "        target_words=260,\n    )\n\n    post_id = await persist_post(",
        "target_words 180 → 260",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Re-run both generators
# ─────────────────────────────────────────────────────────────────────

def step3_rerun() -> bool:
    print("\n[3/3] Re-run class_room and fundamental_metrics")

    for slug in ("class_room", "fundamental_metrics"):
        print(f"\n--- {slug} ---")
        r = subprocess.run([
            "python", "-c", f"""
import asyncio, sys
sys.path.insert(0, '.')
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.trading.subblocks.base import generate_subblock

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        org = (await session.execute(text(
            "SELECT id::text FROM orgs WHERE slug = 'aardesst'"
        ))).first()
    org_id = org[0]
    async with factory() as session:
        await session.execute(text(f"SET app.current_org_id = '{{org_id}}'"))
        await session.commit()
        result = await generate_subblock(session, org_id=org_id, slug='{slug}')
        print(f"result: {{result}}")
    await engine.dispose()

asyncio.run(main())
            """.strip()
        ], cwd=ROOT, capture_output=True, text=True, timeout=180)
        print(r.stdout)
        if r.returncode != 0:
            print(r.stderr[-1200:])
            return False

    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — T.5.1.1 + T.5.5.1: Prompt fixes")
    print("=" * 70)

    step1_fix_class_room()
    step2_fix_fundamental_metrics()
    ok = step3_rerun()

    print("=" * 70)
    if ok:
        print("✅ T.5.1.1 + T.5.5.1 complete")
        print()
        print("Expected results:")
        print("  class_room        → published, 250+ words, no fabricated numbers")
        print("  fundamental_metrics → published, 150+ words prose + table")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
