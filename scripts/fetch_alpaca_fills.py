"""Fetch today's Alpaca fills and upsert into the trades table.

Idempotent: skips fills whose external_id is already in trades.

Usage:
  export ALPACA_API_KEY=...
  export ALPACA_SECRET_KEY=...
  export ALPACA_PAPER=true    # or 'false' for live
  python scripts/fetch_alpaca_fills.py \
      --org-id <uuid> --user-id <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.session import get_session_factory
from packages.trading.sources.alpaca import fetch_today_fills


async def upsert_fills(
    session: AsyncSession,
    org_id: str,
    user_id: str,
    fills: list[dict],
) -> tuple[int, int]:
    """Insert fills that aren't already in trades. Returns (inserted, skipped)."""
    inserted = 0
    skipped = 0
    for fill in fills:
        external_id = fill.get("id")
        if not external_id:
            continue
        existing = (await session.execute(
            text("""
                SELECT 1 FROM trades
                WHERE org_id = :oid AND source = 'alpaca'
                  AND external_id = :ext LIMIT 1
            """),
            {"oid": org_id, "ext": external_id},
        )).first()
        if existing:
            skipped += 1
            continue
        tx_at = fill["transaction_time"]
        at = datetime.fromisoformat(tx_at.replace("Z", "+00:00"))
        await session.execute(
            text("""
                INSERT INTO trades
                    (org_id, user_id, symbol, side, quantity,
                     entry_price, entry_at, source, external_id)
                VALUES
                    (:oid, :uid, :sym, :side, :qty,
                     :price, :at, 'alpaca', :ext)
            """),
            {
                "oid": org_id, "uid": user_id,
                "sym": fill["symbol"].upper(),
                "side": fill["side"].lower(),
                "qty": float(fill["qty"]),
                "price": float(fill["price"]),
                "at": at,
                "ext": external_id,
            },
        )
        inserted += 1
    await session.commit()
    return inserted, skipped


async def run(args: argparse.Namespace) -> None:
    fills = await fetch_today_fills()
    if not fills:
        print("no fills fetched")
        return
    factory = get_session_factory()
    async with factory() as session:  # type: AsyncSession
        await session.execute(
            text("SELECT set_config('app.current_org_id', :oid, false)"),
            {"oid": args.org_id},
        )
        inserted, skipped = await upsert_fills(
            session, args.org_id, args.user_id, fills,
        )
        print(f"inserted {inserted}, skipped {skipped}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", required=True)
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
