"""Token balance service.

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
TOKENS_PER_USD = 1_000_000
TOKENS_PER_WORD = 0.5

TOKENS_PER_100_WORDS_UPLOAD = 1


async def charge_for_upload(
    session: AsyncSession,
    user_id: str,
    *,
    word_count: int,
    ref_id: str | None = None,
    org_id: str | None = None,
) -> int:
    """Charge site tokens for a paste/upload. 1 token per 100 words (min 1).

    Separate from charge_for_content: this meters the input, not the output.
    """
    amount = max(1, word_count // 100)
    ok = await debit(
        session, user_id, amount, "content:upload",
        org_id=org_id, ref_id=ref_id,
        metadata={"word_count": word_count, "rate": TOKENS_PER_100_WORDS_UPLOAD},
    )
    if not ok:
        log.warning("tokens.upload_charge_rejected",
                    user_id=user_id[:8], word_count=word_count, amount=amount)
        return 0
    log.info("tokens.upload_charged", user_id=user_id[:8],
             word_count=word_count, amount=amount)
    return amount

TOKENS_PER_PREDICTION = 50

async def charge_for_content(
    session, user_id, *, kind, word_count, ref_id=None, org_id=None,
) -> int:
    """Charge site tokens for persisted content.
    kind ∈ {"post", "article", "brief"}. Returns tokens charged."""
    amount = int(round(word_count * TOKENS_PER_WORD))
    if amount <= 0:
        return 0
    ok = await debit(session, user_id, amount, f"content:{kind}",
                     ref_id=ref_id, org_id=org_id,
                     metadata={"word_count": word_count, "rate": TOKENS_PER_WORD})
    return amount if ok else 0

async def charge_for_prediction(
    session, user_id, *, ref_id=None, org_id=None, match_id=None,
) -> int:
    """Charge flat fee for consuming one prediction block."""
    ok = await debit(session, user_id, TOKENS_PER_PREDICTION, "prediction",
                     ref_id=ref_id, org_id=org_id,
                     metadata={"match_id": match_id, "rate": TOKENS_PER_PREDICTION})
    return TOKENS_PER_PREDICTION if ok else 0

async def charge_for_prediction_if_new(
    session, user_id, *, org_id, match_id,
) -> int:
    """Idempotent prediction charge — once per (org, match, UTC day).
    Uses token_ledger to dedupe: if a row exists for this org + match
    + today with reason='prediction', skip."""
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
        session, user_id, org_id=org_id, match_id=match_id,
    )

def usd_from_tokens(tokens: int) -> float:
    """Convert a token count to USD at the pricing rate."""
    return round(tokens / TOKENS_PER_USD, 4)


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


# ─────────────────────────────────────────────────────────────────────
# Synchronous variants — for the router, which uses sync SQLAlchemy
# (see packages/ai/router.py's _sync_session).
# ─────────────────────────────────────────────────────────────────────

def debit_sync(
    session,
    user_id: str,
    amount: int,
    reason: str,
    *,
    org_id: str | None = None,
    ref_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Sync version of debit() — same semantics, plain session.

    Called from packages/ai/router.py after every successful LLM call.
    """
    if amount <= 0:
        return True

    row = session.execute(sql_text("""
        SELECT balance, lifetime_consumed FROM user_tokens
        WHERE user_id = :uid FOR UPDATE
    """), {"uid": user_id}).first()

    if not row:
        log.warning("tokens.debit_sync_no_row", user_id=user_id[:8])
        return False

    old_balance, consumed = int(row[0]), int(row[1])
    if old_balance < amount:
        log.warning("tokens.debit_sync_insufficient",
                    user_id=user_id[:8], need=amount, have=old_balance)
        return False

    new_balance = old_balance - amount

    session.execute(sql_text("""
        UPDATE user_tokens
        SET balance = :b, lifetime_consumed = :lc, updated_at = now()
        WHERE user_id = :uid
    """), {"b": new_balance, "lc": consumed + amount, "uid": user_id})

    session.execute(sql_text("""
        INSERT INTO token_ledger
            (user_id, org_id, delta, reason, ref_id, balance_after, metadata)
        VALUES
            (:uid, :oid, :d, :r, :rid, :b, CAST(:m AS jsonb))
    """), {
        "uid": user_id, "oid": org_id, "d": -amount, "r": reason,
        "rid": ref_id, "b": new_balance, "m": _json(metadata or {}),
    })

    session.commit()

    if new_balance < LOW_BALANCE_THRESHOLD:
        log.warning("tokens.low_balance",
                    user_id=user_id[:8], balance=new_balance)

    return True


# ─────────────────────────────────────────────────────────────────────
# Exhaustion gate — the check before any generation kicks off
# ─────────────────────────────────────────────────────────────────────

async def can_generate(session: AsyncSession, user_id: str) -> bool:
    """True if the user has any positive balance. False at 0."""
    return (await get_balance(session, user_id)) > 0


def can_generate_sync(session, user_id: str) -> bool:
    """Sync version — used by the router's safety net.

    Reads the balance row directly. Returns True if no row exists
    (fail-open, so a legitimate user without a tokens row isn't
    silently blocked — S.6 admin backfill covers those).
    """
    row = session.execute(sql_text("""
        SELECT balance FROM user_tokens WHERE user_id = :uid
    """), {"uid": user_id}).first()
    if not row:
        return True   # fail-open
    return int(row[0]) > 0
