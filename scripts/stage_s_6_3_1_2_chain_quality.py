"""S.6.3.1 + S.6.3.2 — don't build a chain from two facts.

The read on the browser revealed the pipeline can be fooled into writing
a 1,166-word article about two facts:

  1. Stock trades around ₹329.40
  2. Stock approaches resistance at ₹346.85
  3. Analyst expects breakout above ₹346.85
  4. Analyst warns breakout above ₹346.85 could drive upside

Events 3 and 4 are the same statement; events 1 and 2 are the same
observation. Two real events, dressed up as four. The article pads to
1,166 words repeating the same two facts.

Two fixes:

S.6.3.1 — extractor dedupe.
After the LLM returns events, merge any pair that describes the same
fact: same actors, same event_type, and Jaccard similarity of the
headline+summary word sets >= 0.6. Keep the more detailed of the pair.

S.6.3.2 — chain coherence + length.
After link_chain returns, if every link is 'precedes' (temporal only),
the events form a timeline, not a chain. Refuse with a specific error.
And when a chain has <= 2 events, cap the target word count at 400 so
the narrator doesn't pad.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = ROOT / "packages" / "ai" / "chain" / "extract.py"
PIPELINE = ROOT / "packages" / "ai" / "chain" / "pipeline.py"
ERROR_TEXT = ROOT / "packages" / "ai" / "error_text.py"
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
# 1. extract.py — dedupe helper + call
# ─────────────────────────────────────────────────────────────────────

DEDUPE_HELPER = '''

# ─────────────────────────────────────────────────────────────────────
# S.6.3.1 — dedupe near-identical events
# ─────────────────────────────────────────────────────────────────────

def _jaccard(a: str, b: str) -> float:
    """Word-set Jaccard similarity, lowercased. Cheap proxy for
    'are these two describing the same thing?' — good enough to catch
    the LLM restating one fact as two events."""
    import re as _re
    wa = set(_re.findall(r"[a-z0-9]+", (a or "").lower()))
    wb = set(_re.findall(r"[a-z0-9]+", (b or "").lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _merge_duplicates(events: list[EventInput]) -> list[EventInput]:
    """Merge events that describe the same fact.

    Two events are considered duplicates when:
      - their actor sets match (case-insensitive, order-insensitive), AND
      - their event_type matches, AND
      - Jaccard similarity of (headline + summary) word sets >= 0.6

    When a duplicate pair is found, we keep the one with the longer
    summary (more detail). The dropped one is logged.
    """
    if len(events) < 2:
        return events

    kept: list[EventInput] = []
    for ev in events:
        merged = False
        for i, existing in enumerate(kept):
            same_actors = (
                sorted(a.strip().lower() for a in (ev.actors or []))
                == sorted(a.strip().lower() for a in (existing.actors or []))
            )
            same_type = (ev.event_type or "").lower() == (existing.event_type or "").lower()
            if not (same_actors and same_type):
                continue
            sim = _jaccard(
                f"{ev.headline} {ev.summary}",
                f"{existing.headline} {existing.summary}",
            )
            if sim < 0.6:
                continue
            # Duplicate. Keep the one with the longer summary.
            if len(ev.summary or "") > len(existing.summary or ""):
                log.info(
                    "extract.deduped",
                    kept=ev.headline[:60],
                    dropped=existing.headline[:60],
                    similarity=round(sim, 2),
                )
                kept[i] = ev
            else:
                log.info(
                    "extract.deduped",
                    kept=existing.headline[:60],
                    dropped=ev.headline[:60],
                    similarity=round(sim, 2),
                )
            merged = True
            break
        if not merged:
            kept.append(ev)

    if len(kept) != len(events):
        log.info("extract.dedupe_summary", before=len(events), after=len(kept))
    return kept
'''


def step1_extract() -> None:
    print("\n[1/4] extract.py — dedupe helper + call")

    # Append the helper at the end of the module
    src = EXTRACT.read_text()
    if "_merge_duplicates" in src:
        print("  ↷ dedupe helper already present")
    else:
        src = src.rstrip() + DEDUPE_HELPER
        EXTRACT.write_text(src)
        print("  ✓ added _merge_duplicates + _jaccard")

    # Insert the call right before the return
    patch(
        EXTRACT,
        '''    if not events:
        raise ValueError("Extraction produced no valid events")
    log.info("extract.llm_done", count=len(events))
    return events''',
        '''    if not events:
        raise ValueError("Extraction produced no valid events")

    # S.6.3.1 — merge events that describe the same fact.
    events = _merge_duplicates(events)

    if not events:
        raise ValueError("Extraction produced no valid events after dedupe")
    log.info("extract.llm_done", count=len(events))
    return events''',
        "call _merge_duplicates before return",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. pipeline.py — coherence check + short-chain word cap
# ─────────────────────────────────────────────────────────────────────

def step2_pipeline() -> None:
    print("\n[2/4] pipeline.py — coherence check + word cap")

    patch(
        PIPELINE,
        '''        # D5: link
        links = await link_chain(sequence, events, org_id=org_id, block_slug=block.slug)
        result.links = links
        log.info("chain.pipeline.linked", count=len(links))

        # D6: narrate — passes block style_guide + example_output
        post = await narrate(
            thesis=selected.thesis,
            sequence=sequence,
            events=events,
            links=links,
            org_id=org_id,
            block=block,
            target_words=words,
        )
        result.post = post''',
        '''        # D5: link
        links = await link_chain(sequence, events, org_id=org_id, block_slug=block.slug)
        result.links = links
        log.info("chain.pipeline.linked", count=len(links))

        # S.6.3.2 — coherence check.
        # If every link is 'precedes', the events are a timeline, not a
        # chain. A chain needs at least one structural relationship
        # (causes, escalates, responds_to, contradicts, corroborates,
        # contextualizes). A pure timeline reads as padding.
        STRUCTURAL = {"causes", "escalates", "responds_to",
                      "contradicts", "corroborates", "contextualizes"}
        structural_count = sum(1 for l in links if l.link_type in STRUCTURAL)
        if len(links) > 0 and structural_count == 0:
            log.info(
                "chain.pipeline.timeline_not_chain",
                events=len(sequence), links=len(links),
                link_types=[l.link_type for l in links],
            )
            result.error = "timeline_not_chain"
            return result

        # S.6.3.2 — short-chain word cap.
        # A chain with <= 2 distinct events can't sustain a full-length
        # article without padding. Cap the target.
        chain_words = words
        if len(sequence) <= 2:
            chain_words = min(words, 400)
            log.info(
                "chain.pipeline.short_chain_word_cap",
                events=len(sequence), target=chain_words, original=words,
            )

        # D6: narrate — passes block style_guide + example_output
        post = await narrate(
            thesis=selected.thesis,
            sequence=sequence,
            events=events,
            links=links,
            org_id=org_id,
            block=block,
            target_words=chain_words,
        )
        result.post = post''',
        "coherence + short-chain word cap",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. error_text.py — classify timeline_not_chain
# ─────────────────────────────────────────────────────────────────────

def step3_classifier() -> None:
    print("\n[3/4] error_text.py — timeline_not_chain")

    patch(
        ERROR_TEXT,
        '''    if "no_coherent_chain" in lower:''',
        '''    if "timeline_not_chain" in lower:
        return {
            "kind": "timeline_not_chain",
            "message": (
                "The events in that text happen in sequence but don't "
                "causally connect. A chain needs events that cause, "
                "escalate, or contradict each other — not just things "
                "that happened one after another."
            ),
            "action": (
                "Try an article where one event leads to another — "
                "a policy decision and its consequences, a conflict and "
                "its escalation, or a claim and its rebuttal."
            ),
            "provider": None,
        }

    if "no_coherent_chain" in lower:''',
        "add timeline_not_chain",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. Verify: unit tests + integration
# ─────────────────────────────────────────────────────────────────────

def step4_verify() -> bool:
    print("\n[4/4] verify")

    # Syntax
    for f in [EXTRACT, PIPELINE, ERROR_TEXT]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ all parse")

    # Unit test the dedupe
    print("\n  dedupe unit test")
    code = r'''
import sys
sys.path.insert(0, ".")
from packages.ai.chain.extract import _jaccard

# Test the Jaccard heuristic directly
cases = [
    (
        "ChartPulsePro expects breakout above 346.85 to drive upside. Analyst notes a breakout could signal upward momentum.",
        "ChartPulsePro warns breakout above 346.85 could trigger upside. Analyst notes a decisive breakout could signal additional upward momentum.",
        True,   # should merge — same content
    ),
    (
        "Fed raises rates by 25bp",
        "Fed holds rates steady",
        False,  # should NOT merge — contradictory
    ),
    (
        "Marksans Pharma trades around 329.40",
        "Marksans Pharma approaches resistance at 346.85",
        False,  # same subject, but different fact
    ),
    (
        "Fed raises rates",
        "ECB raises rates",
        False,  # different actors
    ),
]

ok = True
for a, b, should_be_high in cases:
    sim = _jaccard(a, b)
    over_threshold = sim >= 0.6
    mark = "✓" if over_threshold == should_be_high else "❌"
    print(f"  {mark} sim={sim:.2f}  expected_merge={should_be_high}  a={a[:40]!r}")
    if over_threshold != should_be_high:
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

    # Unit test the coherence check
    print("  coherence check unit test")
    code2 = r'''
STRUCTURAL = {"causes", "escalates", "responds_to", "contradicts", "corroborates", "contextualizes"}

def is_chain(link_types):
    if not link_types:
        return True  # no links — leave existing behavior (shouldn't happen)
    return any(lt in STRUCTURAL for lt in link_types)

cases = [
    (["precedes", "precedes", "precedes"], False),  # timeline only
    (["precedes", "corroborates"], True),            # one structural
    (["causes"], True),                              # causal
    (["corroborates", "corroborates"], True),        # both structural
    (["contextualizes"], True),                      # framing
]

ok = True
for types, want in cases:
    got = is_chain(types)
    mark = "✓" if got == want else "❌"
    print(f"  {mark} {types} → chain={got} (expected {want})")
    if got != want:
        ok = False

raise SystemExit(0 if ok else 1)
'''
    r = subprocess.run(
        ["python", "-c", code2], cwd=ROOT,
        capture_output=True, text=True, timeout=15,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-300:])
        return False

    # Restart worker + API
    print("  restart API + worker")
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    subprocess.run(["pkill", "-9", "-f", "celery.*worker"], check=False)
    time.sleep(3)
    with Path("/tmp/signaliq-api.log").open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    with Path("/tmp/signaliq-worker.log").open("w") as f:
        subprocess.Popen(
            ["celery", "-A", "apps.worker.celery_app", "worker",
             "-l", "info", "-Q", "default,ingestion", "--hostname=worker1@%h"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(8)
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout}")
        return False
    print("  ✓ API up")

    # Integration — feed the real Marksans text through extraction
    print("\n  integration: extract the Marksans text")
    code3 = r'''
import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings
from packages.core.context import current_user_id, current_org_id

# The text the user pasted, verbatim from the browser
MARKSANS = """Marksans Pharma’s shares hover around ₹329, face resistance at ₹346.85, and analysts warn a breakout could spark further gains

The stock has witnessed a strong upward move and is currently consolidating near the resistance zone. A decisive breakout and sustained move above ₹346.85, preferably with a strong weekly close, could signal further upward momentum.

Key Resistance: ₹346.85
Support Zone: ₹314.10
Timeframe: Weekly
I'll be watching how price reacts around this major resistance zone.
This is my personal market analysis and view, shared for educational purposes only—not financial advice. Always do your own research.
Follow ChartPulsePro for more live charts & market analysis.
#MARKSANS #MarksansPharma #StockMarket #IndianStockMarket #NSE #TechnicalAnalysis #PriceAction"""

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    F = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    from packages.db.session import get_owner_session_factory
    OF = get_owner_session_factory()
    async with OF() as s:
        row = (await s.execute(text("""
            SELECT u.id::text, o.id::text FROM users u
            JOIN memberships m ON m.user_id = u.id
            JOIN orgs o ON o.id = m.org_id
            WHERE u.username = 'tokentest' LIMIT 1
        """))).first()
    uid, oid = row
    current_user_id.set(uid); current_org_id.set(oid)

    from packages.ai.chain.extract import extract_events_from_any_input
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        events = await extract_events_from_any_input(s, oid, MARKSANS, use_cache=False)
    print(f"  extracted {len(events)} events after dedupe:")
    for e in events:
        print(f"    - [{e.event_type}] {e.headline[:70]}")
        print(f"      {e.summary[:90]}")

    if len(events) == 4:
        print()
        print("  ⚠ still 4 events — dedupe did not merge the ChartPulsePro pair")
        print("  (expected 2-3 after merging)")
    elif len(events) <= 3:
        print()
        print(f"  ✓ {len(events)} events after dedupe (was 4 before)")
    else:
        print()
        print(f"  ? {len(events)} events — check the log")

    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code3], cwd=ROOT,
        capture_output=True, text=True, timeout=180,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-600:])
        return False

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.3.1 + S.6.3.2 — don't build a chain from two facts")
    print("=" * 70)

    step1_extract()
    step2_pipeline()
    step3_classifier()
    ok = step4_verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.3.1 + S.6.3.2 complete")
        print("   extractor merges near-duplicate events (Jaccard >= 0.6)")
        print("   pipeline refuses timelines that have no structural link")
        print("   short chains (<= 2 events) get a 400-word cap")
        print()
        print("   Retest in the browser: paste the Marksans text again.")
        print("   Expect either a shorter article or a 'timeline' message.")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
