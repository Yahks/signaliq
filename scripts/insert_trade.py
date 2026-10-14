"""Insert a trade for testing. Not part of the pipeline.

Usage:
  python scripts/insert_trade.py \\
    --org-id <uuid> --user-id <uuid> \\
    --symbol XLK --side buy --quantity 100 \\
    --entry-price 199.50 \\
    [--entry-at 2026-10-03T14:30:00Z] \\
    [--stop-price 196.50] [--target-price 202.60] \\
    [--notes "chased the close"] \\
    [--source manual]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure the repo root is on sys.path when this script is run
# directly (python scripts/insert_trade.py). Without this, the
# `packages` import fails.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.db.session import get_session_factory


async def insert(args: argparse.Namespace) -> None:
    factory = get_session_factory()
    async with factory() as session:  # type: AsyncSession
        await session.execute(
            text("SELECT set_config('app.current_org_id', :oid, false)"),
            {"oid": args.org_id},
        )
        entry_at = args.entry_at or datetime.now(UTC)
        row = (await session.execute(
            text("""
                INSERT INTO trades
                    (org_id, user_id, symbol, side, quantity,
                     entry_price, entry_at, stop_price,
                     target_price, notes, source)
                VALUES
                    (:oid, :uid, :sym, :side, :qty,
                     :price, :at, :stop,
                     :target, :notes, :src)
                RETURNING id
            """),
            {
                "oid": args.org_id, "uid": args.user_id,
                "sym": args.symbol.upper(), "side": args.side.lower(),
                "qty": args.quantity, "price": args.entry_price,
                "at": entry_at, "stop": args.stop_price,
                "target": args.target_price, "notes": args.notes,
                "src": args.source,
            },
        )).scalar()
        await session.commit()
        print(f"inserted trade id={row}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", choices=["buy", "sell"], default="buy")
    ap.add_argument("--quantity", type=float, required=True)
    ap.add_argument("--entry-price", type=float, required=True)
    ap.add_argument("--entry-at", type=str, default=None)
    ap.add_argument("--stop-price", type=float, default=None)
    ap.add_argument("--target-price", type=float, default=None)
    ap.add_argument("--notes", type=str, default=None)
    ap.add_argument("--source",
                    choices=["manual", "alpaca", "ibkr", "tradier", "paper"],
                    default="manual")
    args = ap.parse_args()
    if args.entry_at:
        args.entry_at = datetime.fromisoformat(
            args.entry_at.replace("Z", "+00:00")
        )
    asyncio.run(insert(args))


if __name__ == "__main__":
    main()
