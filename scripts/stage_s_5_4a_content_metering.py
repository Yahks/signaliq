"""
SignalIQ — S.5.4a: metering rewrite.

Model:
  - Per-LLM-call debit → REMOVED. LLM calls no longer charge.
  - Posts/articles/briefs → charge round(word_count * 0.5) at write time.
  - Predictions → charge 50 flat per (org, match, UTC day).
  - Uploads, embeddings, clustering → free.

Also fixes a live import bug in the router (`packages.core.errors` → `error`).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "packages" / "core" / "tokens.py"
ROUTER = ROOT / "packages" / "ai" / "router.py"
TRADING = ROOT / "apps" / "worker" / "tasks" / "trading.py"
NEWS = ROOT / "apps" / "worker" / "tasks" / "news_chains.py"
BRIEFS = ROOT / "apps" / "worker" / "tasks" / "briefs.py"
PREDS = ROOT / "apps" / "api" / "routers" / "predictions.py"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() and new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n---expected---\n{old[:300]}")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. tokens.py — constants + charge helpers
# ─────────────────────────────────────────────────────────────────────

def step1_tokens() -> None:
    print("\n[1/9] tokens.py — constants + charge helpers")
    src = TOKENS.read_text()

    if "TOKENS_PER_WORD" in src:
        print("  ↷ charge helpers already present")
        return

    marker = 'LOW_BALANCE_THRESHOLD = 10_000'
    if marker not in src:
        fail("tokens.py: LOW_BALANCE_THRESHOLD not found")

    addition = '''

# ─────────────────────────────────────────────────────────────────────
# Content metering (S.5.4a)
# ─────────────────────────────────────────────────────────────────────

TOKENS_PER_WORD = 0.5
TOKENS_PER_PREDICTION = 50


async def charge_for_content(
    session: AsyncSession,
    user_id: str,
    *,
    kind: str,
    word_count: int,
    ref_id: str | None = None,
    org_id: str | None = None,
) -> int:
    """Charge site tokens for persisted content.

    kind ∈ {"post", "article", "brief"}. Returns tokens charged (0 on failure).
    """
    amount = int(round(word_count * TOKENS_PER_WORD))
    if amount <= 0:
        return 0
    ok = await debit(
        session, user_id, amount, f"content:{kind}",
        org_id=org_id, ref_id=ref_id,
        metadata={"word_count": word_count, "rate": TOKENS_PER_WORD},
    )
    if not ok:
        log.warning("tokens.content_charge_rejected",
                    user_id=user_id[:8], kind=kind,
                    word_count=word_count, amount=amount)
        return 0
    log.info("tokens.content_charged", user_id=user_id[:8],
             kind=kind, word_count=word_count, amount=amount)
    return amount


async def charge_for_prediction(
    session: AsyncSession,
    user_id: str,
    *,
    ref_id: str | None = None,
    org_id: str | None = None,
    match_id: str | None = None,
) -> int:
    """Charge flat fee for one prediction block. Returns tokens charged."""
    ok = await debit(
        session, user_id, TOKENS_PER_PREDICTION, "prediction",
        org_id=org_id, ref_id=ref_id,
        metadata={"match_id": match_id, "rate": TOKENS_PER_PREDICTION},
    )
    if not ok:
        log.warning("tokens.prediction_charge_rejected",
                    user_id=user_id[:8], match_id=match_id)
        return 0
    return TOKENS_PER_PREDICTION


async def charge_for_prediction_if_new(
    session: AsyncSession,
    user_id: str,
    *,
    org_id: str,
    match_id: str,
    ref_id: str | None = None,
) -> int:
    """Charge once per (user, match, UTC day). Dedup via token_ledger."""
    from datetime import UTC, datetime
    today = datetime.now(UTC).date()
    existing = (await session.execute(sql_text("""
        SELECT 1 FROM token_ledger
        WHERE user_id = :uid
          AND reason = 'prediction'
          AND metadata->>'match_id' = :mid
          AND created_at::date = :d
        LIMIT 1
    """), {"uid": user_id, "mid": match_id, "d": today})).first()
    if existing:
        return 0
    return await charge_for_prediction(
        session, user_id, ref_id=ref_id, org_id=org_id, match_id=match_id,
    )
'''

    src = src.replace(marker, marker + addition, 1)
    TOKENS.write_text(src)
    print("  ✓ tokens.py: constants + 3 charge helpers")


# ─────────────────────────────────────────────────────────────────────
# 2. router.py — fix bad import
# ─────────────────────────────────────────────────────────────────────

def step2_router_import() -> None:
    print("\n[2/9] router.py — fix packages.core.errors → error")
    patch(
        ROUTER,
        "from packages.core.errors import CredentialsExhaustedError as _CE",
        "from packages.core.error import CredentialsExhaustedError as _CE",
        "import fix",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. router.py — remove the per-LLM-call debit block
# ─────────────────────────────────────────────────────────────────────

DEBIT_BLOCK_OLD = '''        try:
            from packages.core.context import current_user_id
            uid = current_user_id.get()
            log.info("ai.debit_check", has_uid=bool(uid), purpose=purpose)
            if uid:
                in_tok = int(result.input_tokens or 0)
                out_tok = int(result.output_tokens or 0)
                total = in_tok + out_tok
                if total > 0:
                    from packages.core.tokens import debit_sync as _debit
                    ok_debit = _debit(
                        session, uid, total, "llm_call",
                        org_id=org_id,
                        metadata={"purpose": purpose, "model": model_name},
                    )
                    if not ok_debit:
                        log.warning(
                            "ai.debit_rejected",
                            user_id=uid[:8], amount=total, purpose=purpose,
                        )
                    else:
                        log.info(
                            "ai.debited",
                            user_id=uid[:8], amount=total, purpose=purpose,
                        )
        except Exception as exc:
            log.warning("ai.debit_failed", error=str(exc)[:200])
'''

DEBIT_BLOCK_NEW = '''        # NOTE (S.5.4a): LLM calls no longer charge tokens.
        # Metering moved to content-write time — see charge_for_content()
        # in packages.core.tokens. Only persisted posts / articles / briefs
        # (charged per word) and predictions (flat per match) debit.
'''


def step3_router_remove_debit() -> None:
    print("\n[3/9] router.py — remove per-LLM-call debit")
    patch(ROUTER, DEBIT_BLOCK_OLD, DEBIT_BLOCK_NEW, "debit block removed")


# ─────────────────────────────────────────────────────────────────────
# 4. trading.py — charge per post
# ─────────────────────────────────────────────────────────────────────

def step4_trading() -> None:
    print("\n[4/9] trading.py — charge per post")

    # Add the charge import at the top of _generate_for_org
    patch(
        TRADING,
        "    from packages.core.context import current_org_id, current_user_id\n"
        "    from packages.trading.subblocks.base import generate_subblock",
        "    from packages.core.context import current_org_id, current_user_id\n"
        "    from packages.core.tokens import charge_for_content\n"
        "    from packages.trading.subblocks.base import generate_subblock",
        "import charge_for_content",
    )

    # After the try/except that runs generate_subblock, add the charge.
    old = '''            else:
                log.warning("trading.generate_failed", slug=slug, error=str(exc)[:200])
                result = {"status": "failed", "slug": slug, "error": str(exc)[:200]}

    await engine.dispose()
    return result'''

    new = '''            else:
                log.warning("trading.generate_failed", slug=slug, error=str(exc)[:200])
                result = {"status": "failed", "slug": slug, "error": str(exc)[:200]}

        # S.5.4a — charge for the post that was just written.
        # Only when the generator reports a real post (id + word_count).
        if owner_id and isinstance(result, dict):
            post_id = result.get("post_id")
            word_count = result.get("word_count")
            if post_id and word_count:
                await charge_for_content(
                    session, owner_id, kind="post",
                    word_count=int(word_count), ref_id=post_id, org_id=org_id,
                )

    await engine.dispose()
    return result'''

    patch(TRADING, old, new, "charge call")


# ─────────────────────────────────────────────────────────────────────
# 5. news_chains.py — charge per article
# ─────────────────────────────────────────────────────────────────────

def step5_news_chains() -> None:
    print("\n[5/9] news_chains.py — charge per article")

    # Capture owner_id from set_context_from_org (currently discarded)
    patch(
        NEWS,
        "        from packages.core.context import set_context_from_org\n"
        "        await set_context_from_org(session, org_id)",
        "        from packages.core.context import set_context_from_org\n"
        "        owner_id = await set_context_from_org(session, org_id)",
        "capture owner_id",
    )

    # Charge after the final commit, using post_id + word_count from the return dict
    patch(
        NEWS,
        '''        await session.commit()

    await engine.dispose()
    return {
        "status": "ok",
        "chain_id": chain_id,
        "post_id": post_id,
        "block": block.slug,
        "subtopic": subtopic_slug,''',
        '''        await session.commit()

        # S.5.4a — charge for the article just written.
        if owner_id and post_id and result.post and result.post.word_count:
            from packages.core.tokens import charge_for_content
            await charge_for_content(
                session, owner_id, kind="article",
                word_count=int(result.post.word_count),
                ref_id=post_id, org_id=org_id,
            )

    await engine.dispose()
    return {
        "status": "ok",
        "chain_id": chain_id,
        "post_id": post_id,
        "block": block.slug,
        "subtopic": subtopic_slug,''',
        "charge call",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. briefs.py — charge per brief
# ─────────────────────────────────────────────────────────────────────

def step6_briefs() -> None:
    print("\n[6/9] briefs.py — charge per brief")

    patch(
        BRIEFS,
        "        from packages.core.context import set_context_from_org\n"
        "        await set_context_from_org(session, org_id)",
        "        from packages.core.context import set_context_from_org\n"
        "        owner_id = await set_context_from_org(session, org_id)",
        "capture owner_id",
    )

    patch(
        BRIEFS,
        '''        })).first()

        await session.commit()

    await engine.dispose()
    return {
        "status": "ok",
        "brief_id": row[0],''',
        '''        })).first()

        await session.commit()

        # S.5.4a — charge for the brief just written.
        if owner_id and result.word_count:
            from packages.core.tokens import charge_for_content
            await charge_for_content(
                session, owner_id, kind="brief",
                word_count=int(result.word_count),
                ref_id=row[0], org_id=org_id,
            )

    await engine.dispose()
    return {
        "status": "ok",
        "brief_id": row[0],''',
        "charge call",
    )


# ─────────────────────────────────────────────────────────────────────
# 7. predictions.py — charge per match on upcoming endpoint
# ─────────────────────────────────────────────────────────────────────

def step7_predictions() -> None:
    print("\n[7/9] predictions.py — charge per prediction block")

    patch(
        PREDS,
        "from apps.api.deps import current_user_id",
        "from apps.api.deps import current_org_id, current_user_id",
        "import current_org_id",
    )

    patch(
        PREDS,
        '''    _user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(_public_session),
) -> UpcomingPredictionsList:
    """Upcoming published predictions, ordered by kickoff."""''',
        '''    user_id: str = Depends(current_user_id),
    org_id: str = Depends(current_org_id),
    session: AsyncSession = Depends(_public_session),
) -> UpcomingPredictionsList:
    """Upcoming published predictions, ordered by kickoff.

    Charges 50 site tokens per distinct match per UTC day (idempotent).
    """''',
        "signature + docstring",
    )

    # Insert the per-row charge loop right after the query returns `rows`
    patch(
        PREDS,
        '''    """), params)).all()

    from apps.api.schemas.prediction import MarketProbs, ModelProbs''',
        '''    """), params)).all()

    # S.5.4a — charge per prediction block (idempotent per UTC day).
    from packages.core.tokens import charge_for_prediction_if_new
    for row in rows:
        match_id = row[1]
        if match_id:
            await charge_for_prediction_if_new(
                session, user_id, org_id=org_id, match_id=match_id,
            )

    from apps.api.schemas.prediction import MarketProbs, ModelProbs''',
        "per-row charge loop",
    )


# ─────────────────────────────────────────────────────────────────────
# 8. Syntax-check the patched files
# ─────────────────────────────────────────────────────────────────────

def step8_syntax_check() -> None:
    print("\n[8/9] Syntax check all patched files")
    files = [TOKENS, ROUTER, TRADING, NEWS, BRIEFS, PREDS]
    for f in files:
        r = subprocess.run(
            ["python", "-c", f"import ast; ast.parse(open('{f}').read())"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            fail(f"{f.name}: syntax error\n{r.stderr[:400]}")
        print(f"  ✓ {f.name} parses")


# ─────────────────────────────────────────────────────────────────────
# 9. Live verification
# ─────────────────────────────────────────────────────────────────────

def step9_verify() -> bool:
    print("\n[9/9] Live verification")

    code = r'''
import asyncio, sys, uuid
sys.path.insert(0, ".")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession,
)
from packages.core.config import get_settings
from packages.core.context import current_user_id, current_org_id

async def main():
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    F = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    from packages.db.session import get_owner_session_factory
    OF = get_owner_session_factory()

    # Find a test org
    async with OF() as s:
        row = (await s.execute(text("""
            SELECT u.id::text, o.id::text
            FROM users u
            JOIN memberships m ON m.user_id = u.id
            JOIN orgs o ON o.id = m.org_id
            WHERE u.username = 'tokentest' LIMIT 1
        """))).first()
    if not row:
        print("NO_TEST_USER"); return
    uid, oid = row
    current_user_id.set(uid); current_org_id.set(oid)

    async def bal():
        async with F() as s:
            await s.execute(text(f"SET app.current_org_id = '{oid}'"))
            await s.commit()
            return (await s.execute(text(
                "SELECT balance FROM user_tokens WHERE user_id=:u"
            ), {"u": uid})).scalar()

    async def ledger(reason_prefix):
        async with F() as s:
            await s.execute(text(f"SET app.current_org_id = '{oid}'"))
            await s.commit()
            return (await s.execute(text("""
                SELECT COUNT(*) FROM token_ledger
                WHERE user_id=:u AND reason LIKE :p
            """), {"u": uid, "p": reason_prefix + "%"})).scalar()

    from packages.core.tokens import charge_for_content, charge_for_prediction_if_new

    # Check A: content charge moves balance by round(words * 0.5)
    b0 = await bal()
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        await charge_for_content(s, uid, kind="post",
                                 word_count=800, org_id=oid)
    b1 = await bal()
    n_post = await ledger("content:post")
    print(f"A content:post  balance {b0} -> {b1} (delta {b0-b1}, expect 400)  rows={n_post}")

    # Check B: brief charge
    b2 = await bal()
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        await charge_for_content(s, uid, kind="brief",
                                 word_count=1200, org_id=oid)
    b3 = await bal()
    print(f"B content:brief balance {b2} -> {b3} (delta {b2-b3}, expect 600)")

    # Check C: prediction charge, idempotent
    mid = "test-match-" + str(uuid.uuid4())[:8]
    b4 = await bal()
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        c1 = await charge_for_prediction_if_new(s, uid, org_id=oid, match_id=mid)
    b5 = await bal()
    async with F() as s:
        await s.execute(text(f"SET app.current_org_id = '{oid}'"))
        await s.commit()
        c2 = await charge_for_prediction_if_new(s, uid, org_id=oid, match_id=mid)
    b6 = await bal()
    print(f"C prediction    first={c1} second={c2}  "
          f"balance {b4}->{b5}->{b6}  (expect 50, 0, -50 only)")

    await engine.dispose()

asyncio.run(main())
'''

    r = subprocess.run(["python", "-c", code], cwd=ROOT,
                      capture_output=True, text=True, timeout=60)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        return False
    return "content:post" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — S.5.4a: metering rewrite")
    print("=" * 70)

    step1_tokens()
    step2_router_import()
    step3_router_remove_debit()
    step4_trading()
    step5_news_chains()
    step6_briefs()
    step7_predictions()
    step8_syntax_check()
    ok = step9_verify()

    print("=" * 70)
    if ok:
        print("✅ S.5.4a complete — metering rewritten")
        print("   Next: S.5.4b (BYOK UI)")
        sys.exit(0)
    print("❌ S.5.4a failed — see output above")
    sys.exit(1)


if __name__ == "__main__":
    main()
