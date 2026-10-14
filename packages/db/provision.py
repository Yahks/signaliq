"""Org provisioning.

Called once when an org is created. Idempotent — safe to run
multiple times, safe to backfill older orgs.

Creates the rows the trading pipeline depends on:
  - trader_process: the rules, sizing, account name
  - org_block_settings: enables the blocks that should
    auto-generate on signup

Without these, a new org's first article fails: no rules to
write about, no account name, no auto-enabled blocks.
"""
from __future__ import annotations

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import get_logger

log = get_logger(__name__)


DEFAULT_PROCESS = {
    "daily_scan": (
        "Scan NQ at 9:30 ET for a 10-day high breach. Reject "
        "entries when the 5-day ATR is above $30."
    ),
    "entry_logic": (
        "Enter on a breakout above the 10-day high with at least "
        "0.5% clearance. Reject entries when the 5-day ATR is "
        "above $30."
    ),
    "position_sizing": "contracts = int($2000 / (ATR * $20))",
    "exit_logic": (
        "Stop at 0.75 x ATR below entry. Exit on stop hit or "
        "end of session."
    ),
    "trade_history_summary": "account_equity: 200000",
    "account_name": "my demo account",
}

DEFAULT_BLOCKS = ("trading",)


async def provision_org(
    session: AsyncSession, org_id: str, user_id: str | None = None,
) -> None:
    """Create the rows a new org needs to run the trading pipeline.

    Idempotent. Uses ON CONFLICT DO NOTHING so re-running has no
    effect on an already-provisioned org.

    Sets the RLS tenant context before writing, so this works
    whether the caller is running under the app's RLS policy or
    not.
    """
    # Set the tenant context for RLS on trader_process and
    # org_block_settings.
    await session.execute(
        sql_text("SELECT set_config('app.current_org_id', :oid, false)"),
        {"oid": org_id},
    )

    # trader_process — the rules, sizing, account name.
    await session.execute(sql_text("""
        INSERT INTO trader_process (
            org_id, daily_scan, entry_logic, position_sizing,
            exit_logic, trade_history_summary, account_name
        )
        VALUES (
            :oid, :ds, :el, :ps, :xl, :ths, :an
        )
        ON CONFLICT (org_id) DO NOTHING
    """), {
        "oid": org_id,
        "ds": DEFAULT_PROCESS["daily_scan"],
        "el": DEFAULT_PROCESS["entry_logic"],
        "ps": DEFAULT_PROCESS["position_sizing"],
        "xl": DEFAULT_PROCESS["exit_logic"],
        "ths": DEFAULT_PROCESS["trade_history_summary"],
        "an": DEFAULT_PROCESS["account_name"],
    })

    # org_block_settings — enable the blocks that should auto-run.
    for slug in DEFAULT_BLOCKS:
        await session.execute(sql_text("""
            INSERT INTO org_block_settings (org_id, block_slug, auto_enabled)
            VALUES (:oid, :slug, true)
            ON CONFLICT (org_id, block_slug) DO NOTHING
        """), {"oid": org_id, "slug": slug})

    # S.9.17 — default schedule. Disabled until the org has 10 API
    # keys. class_room fires first — it only needs the trader_process
    # row, which we just created. The rest are added once trade_log
    # or market_conditions are populated.
    await session.execute(sql_text("""
        UPDATE orgs
        SET settings = jsonb_set(
            COALESCE(settings, '{}'::jsonb),
            '{schedule}',
            jsonb_build_object(
                'enabled',        false,
                'interval_hours', 5,
                'subblocks',      jsonb_build_array('class_room')
            )
        )
        WHERE id = :oid
    """), {"oid": org_id})

    # S.9.17 — notify the owner that keys are needed.
    if user_id is None:
        row = (await session.execute(sql_text("""
            SELECT user_id::text FROM memberships
            WHERE org_id = :oid AND role = 'owner' LIMIT 1
        """), {"oid": org_id})).first()
        user_id = row[0] if row else None

    if user_id:
        await session.execute(sql_text("""
            INSERT INTO notifications (org_id, user_id, kind, title, body)
            VALUES (
                :oid, :uid, 'credentials_needed',
                'Add 10 Groq API keys to enable generation',
                'The pipeline stays paused until 10 API keys are added. '
                'Go to Settings → API keys to add them. Once the 10th key '
                'is in, generation starts automatically every 5 hours.'
            )
        """), {"oid": org_id, "uid": user_id})

    await session.commit()
    log.info("org.provisioned", org_id=org_id[:8])
