"""
S.6.1b.1b — fix chain.verify truncation + add regenerate-on-unsupported.

Three problems, one commit:

1. chain.verify truncates at max_tokens=2000 on realistic bodies → Groq
   returns json_validate_failed 5× → pipeline catches as 'failed_soft' and
   continues WITHOUT verification. The hallucination gate silently never runs.

2. When verify legitimately finds 1-3 unsupported facts, the pipeline has no
   regeneration path — it sets result.error='too_many_unsupported_facts:>2'
   and news_chains.py writes nothing.

3. When verify raises (LLM error), the pipeline swallows the exception and
   proceeds with verification_status effectively 'unknown' — the caller
   cannot distinguish 'verified clean' from 'never checked'.

Fixes:
- chain.verify@3: 20-claim cap so output fits the model
- verifier.py: max_tokens 2000 → 4000, log input/output lengths
- narrator.py: accepts forbidden_claims, appends to rendered prompt
- pipeline.py: regen-once when 1-3 unsupported; verify_status field on result
- news_chains.py: persists verification_status from the pipeline
- migration 0041: generated_posts.verification_status varchar(16) default 'unknown'

Policy (user decisions A1-A3):
- Clean verify           → verification_status='verified'
- 1-3 unsupported        → regen once; if clean → 'verified', else 'unverified'
- >3 unsupported         → 'unverified' (do not regenerate; too risky)
- Verify raises entirely  → 'error' (post still persists, status='draft')
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"

SEED_EXTRA = ROOT / "packages" / "ai" / "prompts" / "seed_chain_extra.py"
VERIFIER = ROOT / "packages" / "ai" / "chain" / "verifier.py"
NARRATOR = ROOT / "packages" / "ai" / "chain" / "narrator.py"
PIPELINE = ROOT / "packages" / "ai" / "chain" / "pipeline.py"
NEWS_CHAINS = ROOT / "apps" / "worker" / "tasks" / "news_chains.py"
MIGRATION = ROOT / "alembic" / "versions" / "0041_verification_status.py"
FIX_IMPORTS = ROOT / "scripts" / "fix_s_6_1a_imports.py"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() and new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n"
             f"--- expected (first 400 chars) ---\n{old[:400]}")
    src = src.replace(old, new, 1)
    path.write_text(src)
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. Migration 0041 — generated_posts.verification_status
# ─────────────────────────────────────────────────────────────────────

MIGRATION_SRC = '''"""generated_posts.verification_status.

Adds a column distinguishing 'this post passed factual verification' from
'this post was never verified because the verifier itself failed'.

Values:
  - 'unknown'   — written before this column existed (backfill)
  - 'verified'  — verifier ran and found 0 unsupported facts
  - 'unverified' — verifier found unsupported facts that regen couldn't clear
  - 'error'     — verifier itself failed (LLM error, timeout, etc.)

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generated_posts",
        sa.Column(
            "verification_status",
            sa.String(16),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_index(
        "ix_generated_posts_verification_status",
        "generated_posts",
        ["org_id", "verification_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_generated_posts_verification_status", table_name="generated_posts")
    op.drop_column("generated_posts", "verification_status")
'''


def step1_migration() -> None:
    print("\n[1/8] migration 0041")
    if MIGRATION.exists():
        print(f"  ↷ {MIGRATION.name} already exists")
        return
    MIGRATION.write_text(MIGRATION_SRC)
    print(f"  ✓ wrote {MIGRATION.name}")


# ─────────────────────────────────────────────────────────────────────
# 2. chain.verify@3 prompt — 20-claim cap
# ─────────────────────────────────────────────────────────────────────

VERIFY_V3 = '''You are an evidence auditor.

POST BODY:
{{body}}

EVIDENCE (events and their sources):
{{evidence}}

For each sentence in the body:
1. Classify as: fact | inference | hypothesis | recommendation
2. If fact: cite the event id(s) that support it. If none, mark "unsupported": true.
3. If inference: list the event ids it is inferred FROM.

Rules:
- Return AT MOST 20 claims. If the body has more sentences than that,
  prioritise in this order and stop once you have 20:
    1. Claims of type "fact" that contain numbers, dates, or named entities
    2. Other claims of type "fact"
    3. Claims of type "inference"
- Skip "recommendation" and "teaching" claims entirely if you have already
  reached 20 with higher-priority claims.
- Every claim object must contain all five fields, even when
  supported_by_event_ids is empty. Do not omit "unsupported".

Return ONLY valid JSON in this exact shape:
[
  {
    "claim_text": "...",
    "claim_type": "fact",
    "supported_by_event_ids": ["event_id_1"],
    "confidence": 0.0,
    "unsupported": false
  }
]

JSON:'''


def step2_seed_verify_v3() -> None:
    print("\n[2/8] seed chain.verify@3")

    src = SEED_EXTRA.read_text()
    if "CHAIN_VERIFY_V3" in src:
        print("  ↷ already present")
        return

    # Append the v3 constant + a seed function to the existing seed file
    addition = f'''

# ─────────────────────────────────────────────────────────────────────
# S.6.1b.1b — chain.verify@3 with 20-claim cap
# ─────────────────────────────────────────────────────────────────────

CHAIN_VERIFY_V3 = """{VERIFY_V3}"""


def seed_s61b_1b() -> None:
    """Activate chain.verify@3 with the 20-claim cap."""
    import json

    from sqlalchemy import create_engine, text as sql_text
    from sqlalchemy.orm import sessionmaker

    from packages.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        # Deactivate all versions of chain.verify
        session.execute(
            sql_text("UPDATE prompt_versions SET is_active = false WHERE name = 'chain.verify'")
        )
        session.execute(
            sql_text("""
                INSERT INTO prompt_versions (name, version, role, template, variables, is_active)
                VALUES ('chain.verify', 3, 'fast', :template, CAST(:vars AS jsonb), true)
                ON CONFLICT (name, version) DO UPDATE
                SET template = EXCLUDED.template,
                    variables = EXCLUDED.variables,
                    role = EXCLUDED.role,
                    is_active = true
            """),
            {{
                "template": CHAIN_VERIFY_V3,
                "vars": json.dumps(["body", "evidence"]),
            }},
        )
        session.commit()
    print("  seeded chain.verify@3")


if __name__ == "__main__":
    seed()
    seed_s61b_1()
    seed_s61b_1b()
'''

    src = src.rstrip() + addition
    SEED_EXTRA.write_text(src)
    print("  ✓ appended CHAIN_VERIFY_V3 + seed_s61b_1b")


# ─────────────────────────────────────────────────────────────────────
# 3. verifier.py — max_tokens 4000 + length logging
# ─────────────────────────────────────────────────────────────────────

def step3_verifier() -> None:
    print("\n[3/8] verifier.py — max_tokens + logging")

    patch(
        VERIFIER,
        '''    raw = await complete(
        role="fast",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose=f"chain.verify.{block_slug or 'default'}",
        temperature=0.1,
        max_tokens=2000,
        json_mode=True,
        use_cache=use_cache,
    )''',
        '''    log.info(
        "chain.verify.call",
        body_chars=len(body),
        prompt_chars=len(prompt),
        max_tokens=4000,
        block=block_slug,
    )

    raw = await complete(
        role="fast",
        system=None,
        user=prompt,
        org_id=org_id,
        prompt_version_id=_pv,
        purpose=f"chain.verify.{block_slug or 'default'}",
        temperature=0.1,
        max_tokens=4000,   # was 2000 — truncated on bodies > ~400 words
        json_mode=True,
        use_cache=use_cache,
    )

    log.info("chain.verify.result", output_chars=len(raw or ""))''',
        "max_tokens + length logging",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. narrator.py — accept forbidden_claims
# ─────────────────────────────────────────────────────────────────────

def step4_narrator() -> None:
    print("\n[4/8] narrator.py — forbidden_claims parameter")

    # 4a. Add the parameter to the signature
    patch(
        NARRATOR,
        '''async def narrate(
    *,
    thesis: str,
    sequence: list[str],
    events: list[dict],
    links: list,
    org_id: str,
    block: BlockConfig,
    target_words: int | None = None,
    use_cache: bool = False,
) -> NarratedPost:
    """Generate the long-form article using the block's config."""''',
        '''async def narrate(
    *,
    thesis: str,
    sequence: list[str],
    events: list[dict],
    links: list,
    org_id: str,
    block: BlockConfig,
    target_words: int | None = None,
    use_cache: bool = False,
    forbidden_claims: list[str] | None = None,
) -> NarratedPost:
    """Generate the long-form article using the block's config.

    forbidden_claims: if provided, the prompt is extended with an explicit
    block forbidding these claims. Used by the regen-on-unsupported path.
    """''',
        "signature",
    )

    # 4b. Append the forbidden block after render
    patch(
        NARRATOR,
        '''    prompt, _pv = render_with_version(
        "chain.narrate",
        block_slug=block.slug,
        thesis=thesis or "(no explicit thesis)",
        events_with_links=events_with_links,
        target_words=str(words),
        style_guide=block.style_guide or "(no style guide)",
        example_output=block.example_output or "(no example)",
    )''',
        '''    prompt, _pv = render_with_version(
        "chain.narrate",
        block_slug=block.slug,
        thesis=thesis or "(no explicit thesis)",
        events_with_links=events_with_links,
        target_words=str(words),
        style_guide=block.style_guide or "(no style guide)",
        example_output=block.example_output or "(no example)",
    )

    if forbidden_claims:
        bullets = "\\n".join(f"- {c}" for c in forbidden_claims)
        prompt = (
            prompt
            + "\\n\\nIMPORTANT — a previous draft contained these claims that"
              " were NOT supported by the evidence. Do NOT restate any of"
              " them, in any form. If a sentence cannot be written without"
              " one of them, omit the sentence entirely.\\n"
            + bullets
            + "\\n\\nWrite the article now, avoiding the claims above:"
        )''',
        "forbidden_claims injection",
    )


# ─────────────────────────────────────────────────────────────────────
# 5. pipeline.py — verification_status + regen-on-unsupported
# ─────────────────────────────────────────────────────────────────────

def step5_pipeline() -> None:
    print("\n[5/8] pipeline.py — verification_status + regen")

    # 5a. Add verification_status to ChainPipelineResult
    patch(
        PIPELINE,
        '''@dataclass
class ChainPipelineResult:
    selected: SelectedChain | None = None
    sequence: list[str] = field(default_factory=list)
    links: list[ChainLink] = field(default_factory=list)
    post: NarratedPost | None = None
    verification: VerificationResult | None = None
    living: LivingEnrichment | None = None
    error: str | None = None
    block_slug: str | None = None
    subtopic_slug: str | None = None''',
        '''@dataclass
class ChainPipelineResult:
    selected: SelectedChain | None = None
    sequence: list[str] = field(default_factory=list)
    links: list[ChainLink] = field(default_factory=list)
    post: NarratedPost | None = None
    verification: VerificationResult | None = None
    living: LivingEnrichment | None = None
    error: str | None = None
    block_slug: str | None = None
    subtopic_slug: str | None = None
    # S.6.1b.1b — verification outcome, independent of the editorial status
    # 'verified' | 'unverified' | 'error' | 'unknown'
    verification_status: str = "unknown"
    # True when a regen was attempted (useful for observability)
    regenerated: bool = False''',
        "ChainPipelineResult fields",
    )

    # 5b. Replace the verify block with the regen logic
    patch(
        PIPELINE,
        '''        # D7: verify (best-effort — failure here doesn't invalidate the post)
        try:
            chain_event_ids = set(sequence)
            chain_events = [e for e in events if e["id"] in chain_event_ids]
            verification = await verify(
                body=post.body,
                events=chain_events,
                org_id=org_id,
                block_slug=block.slug,
            )
            result.verification = verification

            log.info(
                "chain.pipeline.done",
                words=post.word_count,
                claims=len(verification.claims),
                unsupported=verification.unsupported_fact_count,
            )

            if verification.unsupported_fact_count > 2:
                result.error = f"too_many_unsupported_facts:{verification.unsupported_fact_count}"
        except Exception as exc:
            log.warning("chain.verify.failed_soft", error=str(exc)[:200])''',
        '''        # D7: verify + regenerate-on-unsupported (S.6.1b.1b)
        #
        # Policy (Reading A):
        #   verify clean          → verification_status='verified'
        #   1-3 unsupported       → regen once; if clean 'verified', else 'unverified'
        #   >3 unsupported        → 'unverified' (no regen — too far off)
        #   verify raised         → 'error' (post still persists)
        try:
            chain_event_ids = set(sequence)
            chain_events = [e for e in events if e["id"] in chain_event_ids]
            verification = await verify(
                body=post.body,
                events=chain_events,
                org_id=org_id,
                block_slug=block.slug,
            )
            result.verification = verification

            log.info(
                "chain.pipeline.verify_done",
                words=post.word_count,
                claims=len(verification.claims),
                unsupported=verification.unsupported_fact_count,
            )

            if verification.unsupported_fact_count == 0:
                result.verification_status = "verified"
            elif 1 <= verification.unsupported_fact_count <= 3:
                # One regeneration attempt with the flagged claims forbidden.
                forbidden = [
                    c.claim_text
                    for c in verification.claims
                    if c.unsupported and c.claim_type == "fact"
                ]
                log.info("chain.pipeline.regen_start", forbidden=len(forbidden))
                try:
                    post2 = await narrate(
                        thesis=selected.thesis,
                        sequence=sequence,
                        events=events,
                        links=links,
                        org_id=org_id,
                        block=block,
                        target_words=words,
                        forbidden_claims=forbidden,
                    )
                    verification2 = await verify(
                        body=post2.body,
                        events=chain_events,
                        org_id=org_id,
                        block_slug=block.slug,
                    )
                    result.post = post2
                    result.verification = verification2
                    result.regenerated = True
                    if verification2.unsupported_fact_count == 0:
                        result.verification_status = "verified"
                    else:
                        result.verification_status = "unverified"
                    log.info(
                        "chain.pipeline.regen_done",
                        new_unsupported=verification2.unsupported_fact_count,
                        status=result.verification_status,
                    )
                except Exception as exc:
                    # Regen failed (LLM error, timeout). Keep the first draft
                    # and mark unverified so a human can review.
                    log.warning("chain.pipeline.regen_failed", error=str(exc)[:200])
                    result.verification_status = "unverified"
            else:
                # >3 unsupported — don't attempt regen
                log.warning(
                    "chain.pipeline.too_many_unsupported",
                    count=verification.unsupported_fact_count,
                )
                result.verification_status = "unverified"
        except Exception as exc:
            # Verify itself failed (LLM error, timeout, credentials).
            # Keep the post but tag it so callers know it was never checked.
            log.warning("chain.verify.failed", error=str(exc)[:200])
            result.verification = None
            result.verification_status = "error"''',
        "verify + regen block",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. news_chains.py — persist verification_status
# ─────────────────────────────────────────────────────────────────────

def step6_news_chains() -> None:
    print("\n[6/8] news_chains.py — persist verification_status")

    # The INSERT column list + params need the new column, and status
    # needs to reflect the verification outcome:
    #   verified   → 'review'   (normal editorial queue)
    #   unverified → 'review'   (still queued — human decides)
    #   error      → 'draft'    (held for re-verification)
    patch(
        NEWS_CHAINS,
        '''        # Persist post + evidence
        post_id = None
        if result.post:
            post_row = (await session.execute(_t("""
                INSERT INTO generated_posts
                    (org_id, chain_id, title, body, word_count, status)
                VALUES
                    (:org_id, :chain_id, :title, :body, :wc, :status)
                RETURNING id::text
            """), {
                "org_id": org_id, "chain_id": chain_id,
                "title": result.post.title[:500],
                "body": result.post.body,
                "wc": result.post.word_count,
                "status": "review",
            })).first()
            post_id = post_row[0]''',
        '''        # Persist post + evidence
        post_id = None
        if result.post:
            # S.6.1b.1b — status reflects the verification outcome.
            # 'error' means verify itself failed → hold as draft for retry.
            editorial_status = (
                "draft" if result.verification_status == "error" else "review"
            )
            post_row = (await session.execute(_t("""
                INSERT INTO generated_posts
                    (org_id, chain_id, title, body, word_count, status,
                     verification_status)
                VALUES
                    (:org_id, :chain_id, :title, :body, :wc, :status,
                     :verification_status)
                RETURNING id::text
            """), {
                "org_id": org_id, "chain_id": chain_id,
                "title": result.post.title[:500],
                "body": result.post.body,
                "wc": result.post.word_count,
                "status": editorial_status,
                "verification_status": result.verification_status,
            })).first()
            post_id = post_row[0]''',
        "INSERT verification_status",
    )

    # Return dict — include the new field so the API can surface it
    patch(
        NEWS_CHAINS,
        '''        "unsupported_facts": result.verification.unsupported_fact_count if result.verification else 0,''',
        '''        "unsupported_facts": result.verification.unsupported_fact_count if result.verification else 0,
        "verification_status": result.verification_status,
        "regenerated": result.regenerated,''',
        "return dict",
    )


# ─────────────────────────────────────────────────────────────────────
# 7. Fix the symbol-check false positive in the older script
# ─────────────────────────────────────────────────────────────────────

def step7_fix_symbol_check() -> None:
    print("\n[7/8] fix symbol-check false positive")
    if not FIX_IMPORTS.exists():
        print(f"  ↷ {FIX_IMPORTS.name} not present — skipping")
        return
    src = FIX_IMPORTS.read_text()
    old_check = '''    r = subprocess.run(
        ["bash", "-c",
         "for f in $(grep -rl render_with_version packages/ apps/ --include='*.py'); do "
         "  if ! grep -q 'import.*render_with_version' \\"$f\\"; then echo \\"MISSING: $f\\"; fi; "
         "done"],
        cwd=ROOT, capture_output=True, text=True,
    )'''
    new_check = '''    r = subprocess.run(
        ["bash", "-c",
         "for f in $(grep -rl render_with_version packages/ apps/ --include='*.py'); do "
         "  case \\"$f\\" in */loader.py) continue ;; esac; "     # skip the definition site
         "  if ! grep -q 'import.*render_with_version' \\"$f\\"; then echo \\"MISSING: $f\\"; fi; "
         "done"],
        cwd=ROOT, capture_output=True, text=True,
    )'''
    if new_check in src:
        print("  ↷ already fixed")
        return
    if old_check not in src:
        print("  ⚠ symbol check shape changed — skipping (not critical)")
        return
    src = src.replace(old_check, new_check, 1)
    FIX_IMPORTS.write_text(src)
    print("  ✓ loader.py skip added")


# ─────────────────────────────────────────────────────────────────────
# 8. Run migration + seed + verify
# ─────────────────────────────────────────────────────────────────────

def step8_apply_and_verify() -> bool:
    print("\n[8/8] apply + verify")

    # Syntax check first
    print("  syntax check")
    for f in [SEED_EXTRA, VERIFIER, NARRATOR, PIPELINE, NEWS_CHAINS, MIGRATION]:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ❌ {f.name}: {r.stderr[:300]}")
            return False
    print("  ✓ all files parse")

    # alembic upgrade
    print("  alembic upgrade head")
    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ❌ alembic: {r.stderr[-400:]}")
        return False
    print("  ✓ migration applied")

    # confirm the column
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-t", "-c",
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='generated_posts' AND column_name='verification_status'"
    ], capture_output=True, text=True)
    if "verification_status" not in r.stdout:
        print(f"  ❌ column missing. psql said: {r.stdout!r}")
        return False
    print("  ✓ column exists")

    # run the seeds
    print("  running seeds")
    r = subprocess.run(
        ["python", "-c",
         "from packages.ai.prompts.seed_chain_extra import seed, seed_s61b_1, seed_s61b_1b; "
         "seed(); seed_s61b_1(); seed_s61b_1b(); "
         "from packages.ai.prompts.loader import clear_cache; clear_cache()"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(f"  ❌ seed: {r.stderr[-400:]}")
        return False
    print("  ✓ seed ran")

    # Confirm chain.verify@3 is active
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        "SELECT name, version, role, is_active FROM prompt_versions "
        "WHERE name = 'chain.verify' ORDER BY version;"
    ], capture_output=True, text=True)
    print(r.stdout)

    # tsc
    print("  tsc --noEmit")
    r = subprocess.run(
        ["./node_modules/.bin/tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        print(f"  ❌ tsc: {(r.stdout or r.stderr)[:400]}")
        return False
    print("  ✓ tsc clean")

    # End-to-end: build a news chain, check verification_status lands
    print("  live chain build (checking verification_status on the post)")
    code = r'''
import asyncio, json, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from packages.core.config import get_settings

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    F = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    from packages.db.session import get_owner_session_factory
    OF = get_owner_session_factory()

    async with OF() as s:
        row = (await s.execute(text("""
            SELECT o.id::text FROM orgs o
            JOIN memberships m ON m.org_id = o.id
            JOIN users u ON u.id = m.user_id
            WHERE u.username = 'tokentest' LIMIT 1
        """))).first()
    oid = row[0]

    from apps.worker.tasks.news_chains import _build_chain_async
    events = [
        {"_placeholder_id": "e1", "external_id": "e1",
         "headline": "Fed signals no rate cut",
         "summary": "Powell said rates will stay higher for longer.",
         "occurred_at": "2026-09-15T12:00:00Z", "reported_at": None, "location": None,
         "actors": ["Fed", "Powell"], "event_type": "statement", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e2", "external_id": "e2",
         "headline": "10-year yield hits 5%",
         "summary": "Treasury yields jumped on the Fed statement.",
         "occurred_at": "2026-09-15T14:00:00Z", "reported_at": None, "location": None,
         "actors": ["Treasury"], "event_type": "price_move", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e3", "external_id": "e3",
         "headline": "S&P 500 drops 2%",
         "summary": "Equities sold off sharply after yields rose.",
         "occurred_at": "2026-09-15T20:00:00Z", "reported_at": None, "location": None,
         "actors": ["S&P 500"], "event_type": "price_move", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
        {"_placeholder_id": "e4", "external_id": "e4",
         "headline": "Dollar strengthens against yen",
         "summary": "Higher yields supported the dollar.",
         "occurred_at": "2026-09-16T08:00:00Z", "reported_at": None, "location": None,
         "actors": ["USD", "JPY"], "event_type": "price_move", "status": "fact",
         "confidence": 0.9, "sources": [], "topics": []},
    ]
    result = await _build_chain_async(oid, events, "news", None, 800)
    print(f"  status:               {result.get('status')}")
    print(f"  verification_status:  {result.get('verification_status')}")
    print(f"  regenerated:          {result.get('regenerated')}")
    print(f"  unsupported_facts:    {result.get('unsupported_facts')}")
    print(f"  post_id:              {result.get('post_id')}")

    # Confirm the row in the DB
    if result.get("post_id"):
        async with F() as s:
            await s.execute(text(f"SET app.current_org_id = '{oid}'"))
            await s.commit()
            row = (await s.execute(text("""
                SELECT status, verification_status FROM generated_posts
                WHERE id = :pid
            """), {"pid": result["post_id"]})).first()
        if row:
            print(f"  DB row:               status={row[0]}  verification_status={row[1]}")

    await engine.dispose()

asyncio.run(main())
'''
    r = subprocess.run(
        ["python", "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=240,
    )
    print(r.stdout[-1200:])
    if r.returncode != 0:
        print(r.stderr[-800:])
        return False

    # Assert the outcome, not just the execution
    if "verification_status:  verified" in r.stdout or "verification_status:  unverified" in r.stdout:
        return True
    if "verification_status:  error" in r.stdout:
        print("  ⚠ verify errored — that is a valid state, but check the log")
        return True
    return False


def main() -> None:
    print("=" * 70)
    print("S.6.1b.1b — verifier: 20-claim cap + regen-on-unsupported")
    print("=" * 70)

    step1_migration()
    step2_seed_verify_v3()
    step3_verifier()
    step4_narrator()
    step5_pipeline()
    step6_news_chains()
    step7_fix_symbol_check()
    ok = step8_apply_and_verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.1b.1b complete")
        print("   verify has a 20-claim cap, max_tokens=4000, regen-on-unsupported")
        print("   generated_posts.verification_status lands with each build")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
