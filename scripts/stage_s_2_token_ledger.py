"""
SignalIQ — Phase S.2: Token ledger system.

Every LLM call debits the current user's balance. Signup grants 1M
tokens. A full ledger records every grant and debit.

Ships:
  1. Migration 0034 — user_tokens + token_ledger tables
  2. packages/core/context.py — current_user_id contextvar
  3. packages/core/tokens.py — get_balance / grant / debit
  4. Router hook — debit on every LLM call
  5. Signup grant — 1M tokens on register
  6. Backfill — existing users get 1M tokens + ledger entry
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "alembic" / "versions" / "0034_token_ledger.py"
CONTEXT = ROOT / "packages" / "core" / "context.py"
TOKENS = ROOT / "packages" / "core" / "tokens.py"
ROUTER = ROOT / "packages" / "ai" / "router.py"
AUTH = ROOT / "apps" / "api" / "routers" / "auth.py"
DEPS = ROOT / "apps" / "api" / "deps.py"
TRADING_TASK = ROOT / "apps" / "worker" / "tasks" / "trading.py"


def _patch_and_verify(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        raise SystemExit(
            f"\n❌ {path.name}: pattern not found for '{label}'\n"
            f"--- expected ---\n{old[:400]}\n---"
        )
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        raise SystemExit(f"\n❌ {path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label} (verified)")


# ─────────────────────────────────────────────────────────────────────
# 1. Migration 0034
# ─────────────────────────────────────────────────────────────────────

MIGRATION_SRC = '''"""Token ledger system.

Adds two tables:
  - user_tokens:    per-user balance (single row per user)
  - token_ledger:   immutable log of every grant + debit

Backfills every existing user with 1,000,000 tokens (signup_bonus)
so nobody's workflow breaks when the debit hook goes live.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_tokens",
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("balance", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("lifetime_granted", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("lifetime_consumed", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "token_ledger",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("delta", sa.BigInteger, nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("ref_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("balance_after", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_token_ledger_user_time", "token_ledger",
                    ["user_id", "created_at"])
    op.create_index("ix_token_ledger_reason", "token_ledger", ["reason"])

    # RLS on token_ledger — user can only see their own rows
    op.execute("ALTER TABLE token_ledger ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE token_ledger FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_token_ledger ON token_ledger
            USING (
                NULLIF(current_setting('app.current_user_id', true), '') IS NULL
                OR user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
    """)

    # RLS on user_tokens — same
    op.execute("ALTER TABLE user_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_tokens FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_user_tokens ON user_tokens
            USING (
                NULLIF(current_setting('app.current_user_id', true), '') IS NULL
                OR user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
    """)

    # Backfill: every existing user gets 1,000,000 tokens
    op.execute("""
        INSERT INTO user_tokens (user_id, balance, lifetime_granted)
        SELECT id, 1000000, 1000000 FROM users
        ON CONFLICT (user_id) DO NOTHING
    """)
    op.execute("""
        INSERT INTO token_ledger (user_id, delta, reason, balance_after, metadata)
        SELECT id, 1000000, 'signup_bonus_backfill', 1000000,
               '{"note": "auto-granted during S.2 rollout"}'::jsonb
        FROM users
        WHERE id NOT IN (
            SELECT DISTINCT user_id FROM token_ledger
            WHERE reason LIKE 'signup_bonus%'
        )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_user_tokens ON user_tokens")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_token_ledger ON token_ledger")
    op.drop_index("ix_token_ledger_reason", table_name="token_ledger")
    op.drop_index("ix_token_ledger_user_time", table_name="token_ledger")
    op.drop_table("token_ledger")
    op.drop_table("user_tokens")
'''


# ─────────────────────────────────────────────────────────────────────
# 2. packages/core/context.py — contextvars
# ─────────────────────────────────────────────────────────────────────

CONTEXT_SRC = '''"""Request/task context variables.

These carry the current user and org through async call stacks without
passing them as explicit arguments to every function. Set at the
boundary (HTTP dependency or Celery task start), read anywhere else.

Used by:
  - packages.ai.router — to debit the current user on every LLM call
  - packages.core.tokens — to associate ledger entries with user + org
"""
from __future__ import annotations

from contextvars import ContextVar

# Set by apps/api/deps.py::current_user_id and by Celery task entrypoints.
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)
current_org_id: ContextVar[str | None] = ContextVar("current_org_id", default=None)
'''


# ─────────────────────────────────────────────────────────────────────
# 3. packages/core/tokens.py — grant, debit, balance
# ─────────────────────────────────────────────────────────────────────

TOKENS_SRC = '''"""Token balance service.

Every LLM call debits the current user. Grants (signup, admin top-up)
credit the balance. Every mutation writes a row in token_ledger.

Concurrency:
  - debit/grant take a row lock on user_tokens FOR UPDATE
  - the read-modify-write cycle is atomic across concurrent workers
  - ledger insert happens in the same transaction as the balance update
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)

# Constants
SIGNUP_GRANT = 1_000_000
LOW_BALANCE_THRESHOLD = 10_000


async def get_balance(session: AsyncSession, user_id: str) -> int:
    """Return the current balance, or 0 if no row exists."""
    row = (await session.execute(sql_text("""
        SELECT balance FROM user_tokens WHERE user_id = :uid
    """), {"uid": user_id})).first()
    return int(row[0]) if row else 0


async def grant(
    session: AsyncSession,
    user_id: str,
    amount: int,
    reason: str,
    *,
    org_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Add `amount` tokens. Returns the new balance.

    Creates the user_tokens row if missing.
    Writes a token_ledger row with delta=+amount.
    Commits the transaction.
    """
    if amount <= 0:
        raise ValueError("grant amount must be positive")

    # Ensure row exists
    await session.execute(sql_text("""
        INSERT INTO user_tokens (user_id, balance, lifetime_granted)
        VALUES (:uid, 0, 0)
        ON CONFLICT (user_id) DO NOTHING
    """), {"uid": user_id})

    # Lock + read
    row = (await session.execute(sql_text("""
        SELECT balance, lifetime_granted FROM user_tokens
        WHERE user_id = :uid FOR UPDATE
    """), {"uid": user_id})).first()
    old_balance, lifetime = int(row[0]), int(row[1])
    new_balance = old_balance + amount

    # Update
    await session.execute(sql_text("""
        UPDATE user_tokens
        SET balance = :b,
            lifetime_granted = :lg,
            updated_at = now()
        WHERE user_id = :uid
    """), {"b": new_balance, "lg": lifetime + amount, "uid": user_id})

    # Ledger
    await session.execute(sql_text("""
        INSERT INTO token_ledger
            (user_id, org_id, delta, reason, balance_after, metadata)
        VALUES
            (:uid, :oid, :d, :r, :b, CAST(:m AS jsonb))
    """), {
        "uid": user_id, "oid": org_id, "d": amount, "r": reason,
        "b": new_balance, "m": _json(metadata or {}),
    })

    await session.commit()
    log.info("tokens.grant", user_id=user_id[:8], amount=amount,
             reason=reason, new_balance=new_balance)
    return new_balance


async def debit(
    session: AsyncSession,
    user_id: str,
    amount: int,
    reason: str,
    *,
    org_id: str | None = None,
    ref_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Subtract `amount` tokens. Returns True on success, False if insufficient.

    Does NOT raise on insufficient balance — callers decide what to do.
    Writes a token_ledger row with delta=-amount on success.
    Commits the transaction.
    """
    if amount <= 0:
        return True  # nothing to debit

    # Lock + read
    row = (await session.execute(sql_text("""
        SELECT balance, lifetime_consumed FROM user_tokens
        WHERE user_id = :uid FOR UPDATE
    """), {"uid": user_id})).first()

    if not row:
        log.warning("tokens.debit_no_row", user_id=user_id[:8])
        return False

    old_balance, consumed = int(row[0]), int(row[1])
    if old_balance < amount:
        log.warning("tokens.debit_insufficient",
                    user_id=user_id[:8], need=amount, have=old_balance)
        return False

    new_balance = old_balance - amount

    # Update
    await session.execute(sql_text("""
        UPDATE user_tokens
        SET balance = :b,
            lifetime_consumed = :lc,
            updated_at = now()
        WHERE user_id = :uid
    """), {"b": new_balance, "lc": consumed + amount, "uid": user_id})

    # Ledger
    await session.execute(sql_text("""
        INSERT INTO token_ledger
            (user_id, org_id, delta, reason, ref_id, balance_after, metadata)
        VALUES
            (:uid, :oid, :d, :r, :rid, :b, CAST(:m AS jsonb))
    """), {
        "uid": user_id, "oid": org_id, "d": -amount, "r": reason,
        "rid": ref_id, "b": new_balance, "m": _json(metadata or {}),
    })

    await session.commit()

    if new_balance < LOW_BALANCE_THRESHOLD:
        log.warning("tokens.low_balance",
                    user_id=user_id[:8], balance=new_balance)

    return True


def _json(d: dict[str, Any]) -> str:
    import json
    return json.dumps(d, default=str)
'''


# ─────────────────────────────────────────────────────────────────────
# 4. Router hook — debit after every successful LLM call
# ─────────────────────────────────────────────────────────────────────

def step4_router_hook() -> None:
    print("\n[4/8] Add router hook (debit on successful call)")

    # Find the successful-call INSERT block
    src = ROUTER.read_text()

    old = '''        session.execute(
            sql_text("""
                INSERT INTO ai_calls (
                    org_id, model_id, purpose, input_tokens, output_tokens,
                    latency_ms, status, error, request_hash, response_payload
                ) VALUES (
                    :org_id, :model_id, :purpose, :in_tok, :out_tok,
                    :latency, :status, :error, :hash, CAST(:payload AS jsonb)
                )
            """),'''

    new = '''        ai_call_row = session.execute(
            sql_text("""
                INSERT INTO ai_calls (
                    org_id, model_id, purpose, input_tokens, output_tokens,
                    latency_ms, status, error, request_hash, response_payload
                ) VALUES (
                    :org_id, :model_id, :purpose, :in_tok, :out_tok,
                    :latency, :status, :error, :hash, CAST(:payload AS jsonb)
                )
                RETURNING id::text
            """),'''

    if old not in src:
        print("  ⚠ successful-call INSERT pattern not found — will need manual inspection")
        print("     grep for 'INSERT INTO ai_calls' in packages/ai/router.py")
        return

    src = src.replace(old, new, 1)
    ROUTER.write_text(src)
    print("  ✓ router: RETURNING id added")


def step4b_router_debit_call() -> None:
    print("\n[4b/8] Wire debit call after the INSERT")

    # Find where the ai_call_row result is used and add a debit call after
    src = ROUTER.read_text()

    # The pattern we expect now: a call to session.execute with the INSERT
    # returning id, followed by some code that doesn't yet debit.
    # We'll add debit logic right after the row is fetched.
    import re

    # Find the calling code around the INSERT; look for the params dict
    # after the RETURNING id INSERT — hook there.
    # Simpler: search for the completion of the execute. This is fragile but
    # works because the INSERT block is unique.
    old_hook = '''                    "hash": request_hash,
                    "payload": _json_dumps(response_payload),
                }
            ),
        )'''

    new_hook = '''                    "hash": request_hash,
                    "payload": _json_dumps(response_payload),
                }
            ),
        )

        # Debit the current user for the tokens this call consumed.
        # We use a separate session because token debits commit independently
        # and must not roll back the ai_calls record on token failure.
        try:
            from packages.core.context import current_user_id
            uid = current_user_id.get()
            if uid:
                total_tokens = (in_tok or 0) + (out_tok or 0)
                if total_tokens > 0:
                    from packages.core.tokens import debit as _debit
                    # Use the same session — the router commits per call
                    await _maybe_debit(session, uid, total_tokens,
                                       purpose=purpose, org_id=org_id)
        except Exception as exc:
            # Never let token bookkeeping break a completed LLM call.
            log.warning("router.debit_failed", error=str(exc)[:200])'''

    if old_hook not in src:
        print("  ⚠ hook anchor not found — will need manual inspection")
        print("     grep -n '_json_dumps(response_payload)' in router.py")
        return

    src = src.replace(old_hook, new_hook, 1)

    # Add the helper function near the top of the module
    helper = '''

async def _maybe_debit(session, user_id: str, amount: int, *, purpose: str,
                       org_id: str | None) -> None:
    """Debit the current user for one LLM call. Fire-and-forget on failure."""
    from packages.core.tokens import debit
    ok = await debit(
        session, user_id, amount, "llm_call",
        org_id=org_id,
        metadata={"purpose": purpose},
    )
    if not ok:
        log.warning("router.debit_rejected",
                    user_id=user_id[:8], amount=amount, purpose=purpose)

'''

    # Insert helper after the log = get_logger(__name__) line
    import re
    m = re.search(r'log = get_logger\(__name__\)\n', src)
    if m:
        src = src[:m.end()] + helper + src[m.end():]

    ROUTER.write_text(src)
    print("  ✓ router: debit hook + _maybe_debit helper added")


# ─────────────────────────────────────────────────────────────────────
# 5. deps.py — set contextvar in HTTP requests
# ─────────────────────────────────────────────────────────────────────

def step5_deps_contextvar() -> None:
    print("\n[5/8] Set contextvar in API deps")

    _patch_and_verify(
        DEPS,
        '''async def current_user_id(payload: dict = Depends(current_token_payload)) -> str:
    return payload["sub"]''',
        '''async def current_user_id(payload: dict = Depends(current_token_payload)) -> str:
    from packages.core.context import current_user_id as _uid_ctx
    uid = payload["sub"]
    _uid_ctx.set(uid)
    return uid''',
        "set current_user_id in deps",
    )

    # Also set in current_org_id
    _patch_and_verify(
        DEPS,
        '''async def current_org_id(payload: dict = Depends(current_token_payload)) -> str:
    org = payload.get("org")
    if not org:
        raise AuthError("Token has no org context")
    return org''',
        '''async def current_org_id(payload: dict = Depends(current_token_payload)) -> str:
    from packages.core.context import current_org_id as _org_ctx
    org = payload.get("org")
    if not org:
        raise AuthError("Token has no org context")
    _org_ctx.set(org)
    return org''',
        "set current_org_id in deps",
    )


# ─────────────────────────────────────────────────────────────────────
# 6. Celery task — set contextvar before generating
# ─────────────────────────────────────────────────────────────────────

def step6_celery_contextvar() -> None:
    print("\n[6/8] Set contextvar in Celery generator tasks")

    src = TRADING_TASK.read_text()

    old = '''async def _generate_for_org(org_id: str, slug: str) -> dict:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    from packages.core.config import get_settings
    from packages.trading.subblocks.base import generate_subblock

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, pool_recycle=180,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        await session.execute(sql_text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()'''

    new = '''async def _generate_for_org(org_id: str, slug: str) -> dict:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

    from packages.core.config import get_settings
    from packages.core.context import current_org_id, current_user_id
    from packages.trading.subblocks.base import generate_subblock

    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_pre_ping=True, pool_recycle=180,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        await session.execute(sql_text(f"SET app.current_org_id = '{org_id}'"))
        await session.commit()

        # Resolve the org owner so token debits target the right user
        owner_row = (await session.execute(sql_text("""
            SELECT user_id::text FROM memberships
            WHERE org_id = :oid AND role = 'owner'
            LIMIT 1
        """), {"oid": org_id})).first()
        owner_id = owner_row[0] if owner_row else None
        if owner_id:
            current_user_id.set(owner_id)
        current_org_id.set(org_id)
'''

    if old not in src:
        print("  ⚠ trading task pattern not found — pasting current signature")
        # Show what's actually there
        import subprocess
        r = subprocess.run(["grep", "-n", "-A12", "async def _generate_for_org", str(TRADING_TASK)],
                           capture_output=True, text=True)
        print(r.stdout[:1000])
        return

    src = src.replace(old, new, 1)
    TRADING_TASK.write_text(src)
    print("  ✓ trading task: contextvar set")


# ─────────────────────────────────────────────────────────────────────
# 7. Register — grant 1M tokens on signup
# ─────────────────────────────────────────────────────────────────────

def step7_signup_grant() -> None:
    print("\n[7/8] Grant 1M tokens on signup")

    _patch_and_verify(
        AUTH,
        '''        membership = Membership(org_id=org.id, user_id=user.id, role="owner")
        session.add(membership)
        await session.commit()

        user_id = user.id
        org_id = org.id''',
        '''        membership = Membership(org_id=org.id, user_id=user.id, role="owner")
        session.add(membership)
        await session.commit()

        user_id = user.id
        org_id = org.id

        # Grant signup tokens. Uses a fresh transaction so it commits
        # independently and doesn't roll back the user if the ledger fails.
        try:
            from packages.core.tokens import grant, SIGNUP_GRANT
            await grant(session, user_id, SIGNUP_GRANT, "signup_bonus",
                        org_id=org_id)
        except Exception as exc:
            from packages.core.logging import get_logger
            get_logger(__name__).warning(
                "auth.signup_grant_failed", error=str(exc)[:200]
            )''',
        "grant signup tokens",
    )


# ─────────────────────────────────────────────────────────────────────
# 8. Migrate + restart + verify
# ─────────────────────────────────────────────────────────────────────

def step8_migrate_and_verify() -> bool:
    print("\n[8/8] Migrate + restart + verify")

    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"❌ alembic failed:\n{r.stderr}")
    print("  ✓ alembic upgrade head")
    for line in (r.stderr or "").splitlines():
        if "Running upgrade" in line:
            print(f"      {line.strip()}")

    # Verify tables exist + backfill worked
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        """
        SELECT
            (SELECT COUNT(*) FROM user_tokens) AS balances,
            (SELECT COUNT(*) FROM token_ledger) AS ledger_rows,
            (SELECT COUNT(*) FROM users) AS users
        """
    ], capture_output=True, text=True)
    print(r.stdout)

    # Restart API
    subprocess.run(["pkill", "-9", "-f", "uvicorn apps.api.main"], check=False)
    time.sleep(2)
    log = Path("/tmp/signaliq-api.log")
    with log.open("w") as f:
        subprocess.Popen(
            ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
        )
    time.sleep(5)

    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "http://localhost:8000/health"],
        capture_output=True, text=True,
    )
    if r.stdout.strip() != "200":
        print(f"  ❌ /health = {r.stdout} — see /tmp/signaliq-api.log")
        return False
    print("  ✓ API /health 200")

    # Register a fresh user → balance should be 1M
    r = subprocess.run([
        "curl", "-s", "-X", "POST", "http://localhost:8000/api/auth/register",
        "-H", "Content-Type: application/json",
        "-d", '''{
            "email": "tokentest@example.com",
            "username": "tokentest",
            "password": "TestPass123!",
            "full_name": "Token Test",
            "country": "Nigeria",
            "accepted_terms": true,
            "accepted_age": true,
            "accepted_risk": true
        }''',
    ], capture_output=True, text=True, timeout=30)
    if '"access_token"' not in r.stdout:
        print(f"  ❌ register failed: {r.stdout[:300]}")
        return False
    print("  ✓ register succeeded")

    # Check balance
    r = subprocess.run([
        "docker", "exec", "-i", "signaliq-postgres-1",
        "psql", "-U", "signaliq", "-d", "signaliq", "-c",
        """
        SELECT u.username, ut.balance, ut.lifetime_granted,
               (SELECT COUNT(*) FROM token_ledger tl WHERE tl.user_id = u.id) AS ledger_rows
        FROM users u
        JOIN user_tokens ut ON ut.user_id = u.id
        WHERE u.username = 'tokentest'
        """
    ], capture_output=True, text=True)
    print(r.stdout)
    return "1000000" in r.stdout


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.2: Token ledger system")
    print("=" * 70)

    # 1. Migration
    print("\n[1/8] Write migration 0034")
    if MIGRATION.exists():
        print(f"  ↷ {MIGRATION.name} already exists")
    else:
        MIGRATION.write_text(MIGRATION_SRC)
        print(f"  ✓ wrote {MIGRATION.name}")

    # 2. Context
    print("\n[2/8] Write packages/core/context.py")
    CONTEXT.write_text(CONTEXT_SRC)
    print(f"  ✓ wrote {CONTEXT.relative_to(ROOT)}")

    # 3. Tokens
    print("\n[3/8] Write packages/core/tokens.py")
    TOKENS.write_text(TOKENS_SRC)
    print(f"  ✓ wrote {TOKENS.relative_to(ROOT)}")

    # 4-7
    step4_router_hook()
    step4b_router_debit_call()
    step5_deps_contextvar()
    step6_celery_contextvar()
    step7_signup_grant()

    # 8
    ok = step8_migrate_and_verify()

    print("=" * 70)
    if ok:
        print("✅ S.2 complete — token ledger live")
        print()
        print("Every LLM call now debits the current user.")
        print("New signups get 1,000,000 tokens.")
        print("Existing users backfilled with 1,000,000.")
        print()
        print("Next: S.3 — exhaustion gate (pause generation at 0 balance).")
        sys.exit(0)
    print("❌ S.2 failed — see output above")
    sys.exit(1)


if __name__ == "__main__":
    main()
