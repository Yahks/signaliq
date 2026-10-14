"""S.6.4.5 — refuse empty narration at four pipeline boundaries.

The pipeline occasionally persists posts with word_count=0 and title
'Untitled'. This happens when the narrator's LLM call returns an empty
body — either a network hiccup, a truncated response, or a model that
emitted nothing. The pipeline currently accepts the empty output and
continues to verification, persistence, and the review queue.

Four guards, layered:

  1. narrator.narrate()          — raise on word_count < 50
  2. chain/pipeline.run_*()      — set result.error = "empty_narration"
                                   if narrate returns short output
  3. news_chains task            — skip persistence if the post is short
  4. trading persist_post        — refuse to insert a short post

50 words is the threshold: low enough to never reject a legitimately
short response, high enough to catch empty/single-line misfires.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NARRATOR = ROOT / "packages" / "ai" / "chain" / "narrator.py"
PIPELINE = ROOT / "packages" / "ai" / "chain" / "pipeline.py"
NEWS = ROOT / "apps" / "worker" / "tasks" / "news_chains.py"
SUBBASE = ROOT / "packages" / "trading" / "subblocks" / "base.py"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch_py(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    # A patch is "already applied" if its new text is present AND the
    # old text is absent — UNLESS old is a substring of new (which
    # makes the check meaningless, since new always contains old).
    already = new.strip() in src
    if already and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if already and old in new:
        # old is a substring of new — the check can't distinguish.
        # Trust that new being present means the patch is done.
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n"
             f"--- expected ---\n{old[:500]}")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: wrote but new not found")
    try:
        ast.parse(path.read_text())
    except SyntaxError as e:
        fail(f"{path.name}: syntax error after '{label}': {e}")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. narrator.py — raise on empty output
# ─────────────────────────────────────────────────────────────────────

def step1_narrator() -> None:
    print("\n[1/4] narrator.py")
    patch_py(
        NARRATOR,
        '''    log.info("chain.narrate.done", block=block.slug, title=title[:60], words=word_count)
    return NarratedPost(title=title, body=body, word_count=word_count)''',
        '''    # S.6.4.5 — refuse to return an empty post. The pipeline downstream
    # would otherwise persist a 0-word draft to the review queue.
    if word_count < 50:
        raise RuntimeError(
            f"narrate produced an unusably short body: "
            f"{word_count} words, {len(body or '')} chars. "
            f"block={block.slug} title={title[:60]!r}"
        )

    log.info("chain.narrate.done", block=block.slug, title=title[:60], words=word_count)
    return NarratedPost(title=title, body=body, word_count=word_count)''',
        "raise on short body",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. pipeline.py — catch empty post before verify
# ─────────────────────────────────────────────────────────────────────

def step2_pipeline() -> None:
    print("\n[2/4] pipeline.py — post-narrate check")

    patch_py(
        PIPELINE,
        '''        result.post = post

        # D7: verify + regenerate-on-unsupported (S.6.1b.1b)''',
        '''        result.post = post

        # S.6.4.5 — belt-and-braces. If narrator.py's guard somehow
        # didn't fire, catch an empty post here before verify + persist.
        if post.word_count < 50:
            log.warning(
                "chain.pipeline.empty_narration",
                word_count=post.word_count,
                title=post.title[:60],
            )
            result.error = "empty_narration"
            return result

        # D7: verify + regenerate-on-unsupported (S.6.1b.1b)''',
        "check after narrate",
    )

    # S.6.4.5 — guard the regen path. The real block is an if/else
    # (regen_won vs regen_lost), so the check goes inside the if:
    # only accept post2 if it's both better AND non-empty.
    patch_py(
        PIPELINE,
        '''                    before = verification.unsupported_fact_count
                    after = verification2.unsupported_fact_count
                    if after < before:''',
        '''                    before = verification.unsupported_fact_count
                    after = verification2.unsupported_fact_count
                    # S.6.4.5 — a regen that is shorter than 50 words is
                    # garbage, regardless of its unsupported count.
                    # Treat it as a loss: keep the original draft.
                    if post2.word_count < 50:
                        log.warning(
                            "chain.pipeline.regen_empty",
                            word_count=post2.word_count,
                        )
                        result.regenerated = False
                        result.verification_status = (
                            "verified"
                            if verification.unsupported_fact_count == 0
                            else "unverified"
                        )
                    elif after < before:''',
        "guard regen output",
    )


def step3_news_chains() -> None:
    print("\n[3/4] news_chains.py")
    patch_py(
        NEWS,
        '''        # Persist post + evidence
        post_id = None
        if result.post:''',
        '''        # Persist post + evidence
        post_id = None
        # S.6.4.5 — belt-and-braces. Never persist a 0-word post, even
        # if the pipeline's own guard didn't fire.
        if result.post and result.post.word_count < 50:
            log.warning(
                "chain.task.empty_post_skipped",
                word_count=result.post.word_count,
                title=result.post.title[:60],
            )
            result.post = None
        if result.post:''',
        "skip short post",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. subblocks/base.py::persist_post — refuse short trading posts
# ─────────────────────────────────────────────────────────────────────

def step4_trading_persist() -> None:
    print("\n[4/4] subblocks/base.py")
    patch_py(
        SUBBASE,
        '''    """Insert a trading_posts row. Returns the new post id."""
    status = "published" if getattr(post, "verification_ok", True) else "draft"''',
        '''    """Insert a trading_posts row. Returns the new post id."""
    # S.6.4.5 — never persist a post with no content.
    if getattr(post, "word_count", 0) < 50:
        raise ValueError(
            f"refusing to persist a trading post with "
            f"{getattr(post, 'word_count', 0)} words: {getattr(post, 'title', '?')[:60]!r}"
        )
    status = "published" if getattr(post, "verification_ok", True) else "draft"''',
        "refuse short post",
    )


def main() -> None:
    print("=" * 70)
    print("S.6.4.5 — refuse empty narration at four boundaries")
    print("=" * 70)

    step1_narrator()
    step2_pipeline()
    step3_news_chains()
    step4_trading_persist()

    print()
    print("=" * 70)
    print("Four guards in place. Every LLM-output-consuming stage now")
    print("fails loudly on unusable output instead of persisting garbage.")
    print("=" * 70)


if __name__ == "__main__":
    main()
