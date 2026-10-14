"""S.7.2 — single block-enabled enforcement helper.

Three places currently check org_block_settings for the trading block,
each with the same fail-open bug:

  router.py:98      `if block_row is not None and not block_row[0]: raise`
  trading.py:74     `# Missing row → treat as enabled (fail-open)`
  trading.py:267    `# Missing row → treat as enabled (fail-open for orgs created before S.4)`

All three read "no row means enable." That was a deliberate
pre-onboarding choice — every org got implicit access to trading and
news. Under the onboarding model, no row means the user hasn't
selected that block, and the correct answer is "disabled."

This commit:

  1. Adds packages/core/blocks.py with the canonical slug constants,
     ONBOARDABLE_BLOCKS, MAX_BLOCKS_PER_ORG, is_block_enabled(),
     enabled_blocks(). One source of truth for "which blocks exist"
     and "is this org allowed to use this block."

  2. Replaces the three ad-hoc checks with is_block_enabled(). After
     this, a missing row is disabled everywhere. Existing orgs are
     unaffected: the S.7.1 read showed all 7 orgs already have
     explicit news+trading rows. New orgs start with zero rows until
     the onboarding picker writes them.

  3. Keeps _block_for_purpose in router.py — it's the purpose-to-slug
     mapping, not the gate. The gate becomes is_block_enabled().

Verify after:
  - A trading subblock still generates for Demo's org (has rows).
  - A fake org with no rows cannot generate.
  - Toggling auto_enabled=false on trading still blocks.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKS = ROOT / "packages" / "core" / "blocks.py"
ROUTER = ROOT / "packages" / "ai" / "router.py"
TRADING_TASK = ROOT / "apps" / "worker" / "tasks" / "trading.py"
TRADING_API = ROOT / "apps" / "api" / "routers" / "trading.py"

MARK = "S.7.2"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def apply_once(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() in src:
        print(f"  ↷ {label}: already applied")
        return
    if old not in src:
        fail(f"{label}: anchor not found in {path.name}\n"
             f"--- expected (first 500) ---\n{old[:500]}")
    path.write_text(src.replace(old, new, 1))
    try:
        ast.parse(path.read_text())
    except SyntaxError as e:
        fail(f"after '{label}': {e}")
    print(f"  ✓ {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. The new module
# ─────────────────────────────────────────────────────────────────────

BLOCKS_PY = '''"""Block registry and enablement helpers.

A "block" is a service the user selects at signup: football, trading,
news (gossip and jobs are declared here for forward compatibility but
are not yet onboardable). A block is enabled for an org when there is
an org_block_settings row with auto_enabled = true.

Missing row means DISABLED. This is the onboarding model: a brand-new
org has no rows, so no blocks. The picker (S.7.4/S.7.5) writes rows
when the user selects services.

The slug constants here are the canonical set. Every org_block_settings
row is FK-checked against blocks.slug (migration 0047), and blocks is
seeded with rows for football, trading, news. Adding a new block means:
add a blocks row, add a constant here, add it to the picker.
"""
from __future__ import annotations

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

# Canonical slugs. These match blocks.slug exactly.
BLOCK_FOOTBALL = "football"
BLOCK_TRADING  = "trading"
BLOCK_NEWS     = "news"
BLOCK_GOSSIP   = "gossip"
BLOCK_JOBS     = "jobs"

# Blocks a new user can pick at signup. Gossip and jobs are excluded
# until their content pipelines exist.
ONBOARDABLE_BLOCKS: tuple[str, ...] = (
    BLOCK_FOOTBALL,
    BLOCK_TRADING,
    BLOCK_NEWS,
)

# The picker requires 1 <= N <= 2. Zero blocks is allowed after signup
# (a user can remove their last block), but not at signup.
MIN_BLOCKS_AT_SIGNUP = 1
MAX_BLOCKS_PER_ORG = 2


async def is_block_enabled(
    session: AsyncSession, org_id: str, block_slug: str,
) -> bool:
    """True iff org_id has an org_block_settings row for block_slug
    with auto_enabled = true.

    A missing row returns False — no implicit enablement. This is the
    opposite of the pre-S.7.2 behavior; see the migration note in
    alembic/versions/0047.
    """
    row = (await session.execute(sql_text("""
        SELECT auto_enabled FROM org_block_settings
        WHERE org_id = :oid AND block_slug = :slug
    """), {"oid": org_id, "slug": block_slug})).first()
    return bool(row and row[0])


def is_block_enabled_sync(session, org_id: str, block_slug: str) -> bool:
    """Sync variant for callers that use a sync Session (router gate,
    worker tasks that don't have an async session at the call site)."""
    row = session.execute(sql_text("""
        SELECT auto_enabled FROM org_block_settings
        WHERE org_id = :oid AND block_slug = :slug
    """), {"oid": org_id, "slug": block_slug}).first()
    return bool(row and row[0])


async def enabled_blocks(session: AsyncSession, org_id: str) -> list[str]:
    """All enabled block slugs for the org, in a stable order."""
    rows = (await session.execute(sql_text("""
        SELECT block_slug FROM org_block_settings
        WHERE org_id = :oid AND auto_enabled = true
        ORDER BY block_slug
    """), {"oid": org_id})).all()
    return [r[0] for r in rows]
'''


def step1_new_module() -> None:
    print("\n[1/4] packages/core/blocks.py")
    if BLOCKS.exists():
        print("  ↷ already exists")
        return
    BLOCKS.write_text(BLOCKS_PY)
    try:
        ast.parse(BLOCKS.read_text())
    except SyntaxError as e:
        fail(f"blocks.py syntax error: {e}")
    print("  ✓ created")


# ─────────────────────────────────────────────────────────────────────
# 2. router.py — use is_block_enabled_sync
# ─────────────────────────────────────────────────────────────────────

def step2_router() -> None:
    print("\n[2/4] router.py — gate via is_block_enabled_sync")
    apply_once(
        ROUTER,
        '''        # ─── Gate 3: block enabled ───────────────────────────────────
        block_slug = _block_for_purpose(purpose)
        if block_slug:
            block_row = session.execute(
                sql_text("""
                    SELECT auto_enabled FROM org_block_settings
                    WHERE org_id = :oid AND block_slug = :slug
                """),
                {"oid": org_ctx, "slug": block_slug},
            ).first()
            if block_row is not None and not block_row[0]:
                raise BlockDisabledError(
                    f"The {block_slug} block is disabled for your organisation. "
                    f"Enable it on the dashboard to generate."
                )''',
        '''        # ─── Gate 3: block enabled ───────────────────────────────────
        # S.7.2 — missing row means DISABLED. Under the onboarding model
        # a brand-new org has no org_block_settings rows until the picker
        # writes them, so the pre-S.7.2 "no row = enable" reading would
        # have allowed generation for orgs that had not selected the
        # block.
        block_slug = _block_for_purpose(purpose)
        if block_slug:
            from packages.core.blocks import is_block_enabled_sync
            if not is_block_enabled_sync(session, org_ctx, block_slug):
                raise BlockDisabledError(
                    f"The {block_slug} block is not enabled for your "
                    f"organisation. Add it in settings to generate."
                )''',
        "router gate",
    )


# ─────────────────────────────────────────────────────────────────────
# 3. worker task trading.py — use is_block_enabled
# ─────────────────────────────────────────────────────────────────────

def step3_trading_task() -> None:
    print("\n[3/4] worker/tasks/trading.py — gate via is_block_enabled")
    apply_once(
        TRADING_TASK,
        '''        # Auto-toggle gate — skip when the trading block is disabled for this org.
        auto_row = (await session.execute(sql_text("""
            SELECT auto_enabled FROM org_block_settings
            WHERE org_id = :oid AND block_slug = 'trading'
        """), {"oid": org_id})).first()
        # Missing row → treat as enabled (fail-open)
        if auto_row and not auto_row[0]:
            log.info("trading.generation_skipped_auto_disabled",
                     org_id=org_id, slug=slug)
            return {"status": "skipped_auto_disabled", "org_id": org_id, "slug": slug}''',
        '''        # S.7.2 — auto-toggle gate. Missing row means disabled: the
        # org has not selected the trading block, or an operator removed
        # it. Either way, do not generate.
        from packages.core.blocks import is_block_enabled
        if not await is_block_enabled(session, org_id, "trading"):
            log.info("trading.generation_skipped_auto_disabled",
                     org_id=org_id, slug=slug)
            return {"status": "skipped_auto_disabled", "org_id": org_id, "slug": slug}''',
        "worker task gate",
    )


# ─────────────────────────────────────────────────────────────────────
# 4. API trading.py — use is_block_enabled
# ─────────────────────────────────────────────────────────────────────

def step4_trading_api() -> None:
    print("\n[4/4] api/routers/trading.py — gate via is_block_enabled")
    apply_once(
        TRADING_API,
        '''    # Block gate — refuse if the 'trading' block is disabled for this org.
    # The user must explicitly enable the block to generate content.
    from packages.db.session import get_owner_session_factory as _osf
    _block_factory = _osf()
    async with _block_factory() as _bs:
        _block_row = (await _bs.execute(sql_text("""
            SELECT auto_enabled FROM org_block_settings
            WHERE org_id = :oid AND block_slug = 'trading'
        """), {"oid": org_id})).first()
        # Missing row → treat as enabled (fail-open for orgs created before S.4)
        if _block_row is not None and not _block_row[0]:
            raise HTTPException(
                status_code=403,
                detail="Trading block is disabled for your organisation. "
                       "Enable it on the dashboard to generate content.",''',
        '''    # S.7.2 — block gate. Missing row means disabled.
    from packages.db.session import get_owner_session_factory as _osf
    from packages.core.blocks import is_block_enabled
    _block_factory = _osf()
    async with _block_factory() as _bs:
        if not await is_block_enabled(_bs, org_id, "trading"):
            raise HTTPException(
                status_code=403,
                detail="Trading block is not enabled for your organisation. "
                       "Add it in settings to generate content.",''',
        "API gate",
    )


def main() -> None:
    print("=" * 70)
    print("S.7.2 — single block-enabled enforcement helper")
    print("=" * 70)
    step1_new_module()
    step2_router()
    step3_trading_task()
    step4_trading_api()
    print()
    print("=" * 70)
    print("Done. Missing row = disabled, everywhere.")
    print("=" * 70)


if __name__ == "__main__":
    main()
