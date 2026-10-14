"""Bridge Alpaca fills from `trades` into `trade_log`.

Reads fills from `trades` with source='alpaca' that don't yet
appear in `trade_log` (matched by traded_at + instrument + entry
approximately), translates them, and inserts.

Usage:
  python scripts/bridge_alpaca_to_trade_log.py <org_id>
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.db.session import get_session_factory
from packages.db.tenant import set_current_org


async def main(org_id: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await set_current_org(session, org_id)

        rows = (await session.execute(text("""
            SELECT id::text, symbol, side, quantity, entry_price, entry_at
            FROM trades
            WHERE org_id = :oid AND source = 'alpaca'
            ORDER BY entry_at ASC
        """), {"oid": org_id})).all()

        imported = 0
        skipped = 0
        for r in rows:
            tid, symbol, side, qty, price, at = r
            # Skip if a trade_log row already exists at same time+instrument.
            dup = (await session.execute(text("""
                SELECT 1 FROM trade_log
                WHERE org_id = :oid
                  AND instrument = :inst
                  AND traded_at = :ts
                  AND deleted_at IS NULL
                LIMIT 1
            """), {"oid": org_id, "inst": symbol, "ts": at})).first()
            if dup:
                skipped += 1
                continue

            await session.execute(text("""
                INSERT INTO trade_log (
                    org_id, traded_at, instrument, direction, setup,
                    entry, contracts, source
                ) VALUES (
                    :oid, :ts, :inst, :dir, :setup,
                    :entry, :qty, 'alpaca'
                )
            """), {
                "oid": org_id, "ts": at, "inst": symbol,
                "dir": "long" if side.lower() in ("buy", "long") else "short",
                "setup": f"alpaca fill {tid[:8]}",
                "entry": float(price), "qty": int(qty),
            })
            imported += 1

        await session.commit()
    print(f"Imported {imported} fills from trades → trade_log ({skipped} skipped).")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("org_id")
    args = p.parse_args()
    asyncio.run(main(args.org_id))
