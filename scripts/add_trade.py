"""Insert a single trade into trade_log.

Usage:
  python scripts/add_trade.py <org_id> \
      --traded-at 2026-05-08T09:45:00-05:00 \
      --instrument NQ \
      --direction long \
      --setup "post-open momentum, ATR below threshold" \
      --entry 20390 \
      --contracts 1 \
      --stop 20374.25 \
      --atr-at-entry 21.0 \
      --exit 20373 \
      --exit-reason "manual close" \
      --result -340 \
      --hold-time 19m

Idempotent: duplicates by (org_id, traded_at, instrument, entry)
are skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.db.session import get_session_factory
from packages.db.tenant import set_current_org
from packages.trading.trade_log import import_yaml_trades


def _yaml_from_args(args: argparse.Namespace) -> str:
    """Build a YAML string for import_yaml_trades from CLI args."""
    lines = [
        "equity: 200000",
        "trades:",
        f"  - traded_at: {args.traded_at}",
        f"    instrument: {args.instrument}",
        f"    direction: {args.direction}",
        f"    setup: {args.setup!r}",
        f"    entry: {args.entry}",
        f"    contracts: {args.contracts}",
        f"    stop: {args.stop if args.stop is not None else 'null'}",
        f"    atr_at_entry: {args.atr_at_entry}",
        f"    target: {args.target if args.target is not None else 'null'}",
        f"    exit: {args.exit}",
        f"    exit_reason: {args.exit_reason!r}",
        f"    result: {args.result}",
        f"    hold_time: {args.hold_time!r}",
        f"    lesson: {args.lesson!r}",
    ]
    return "\n".join(lines)


async def main(args: argparse.Namespace) -> None:
    yaml_text = _yaml_from_args(args)
    factory = get_session_factory()
    async with factory() as session:
        await set_current_org(session, args.org_id)
        result = await import_yaml_trades(session, args.org_id, yaml_text)
    print(
        f"Imported {result['imported']} / {result['total']} "
        f"({result['skipped']} skipped)."
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Insert a single trade.")
    p.add_argument("org_id")
    p.add_argument("--traded-at", required=True)
    p.add_argument("--instrument", required=True)
    p.add_argument("--direction", required=True, choices=["long", "short"])
    p.add_argument("--setup", default="")
    p.add_argument("--entry", type=float, required=True)
    p.add_argument("--contracts", type=int, default=1)
    p.add_argument("--stop", type=float, default=None)
    p.add_argument("--atr-at-entry", type=float, default=None)
    p.add_argument("--target", type=float, default=None)
    p.add_argument("--exit", type=float, required=True)
    p.add_argument("--exit-reason", default="")
    p.add_argument("--result", type=float, required=True)
    p.add_argument("--hold-time", default="")
    p.add_argument("--lesson", default="")
    args = p.parse_args()
    asyncio.run(main(args))
