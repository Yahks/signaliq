"""Import trades from a YAML file into trade_log."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from packages.db.session import get_session_factory
from packages.core.logging import get_logger
from packages.trading.trade_log import import_yaml_trades

log = get_logger(__name__)


async def _run(yaml_path: str, org_id: str) -> None:
    yaml_text = Path(yaml_path).read_text()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :oid, false)"),
            {"oid": org_id},
        )
        result = await import_yaml_trades(session, org_id, yaml_text)
        print(f"imported={result['imported']} "
              f"skipped={result['skipped']} "
              f"total={result['total']} "
              f"equity={result.get('equity')}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("yaml_path")
    p.add_argument("org_id")
    args = p.parse_args()
    asyncio.run(_run(args.yaml_path, args.org_id))


if __name__ == "__main__":
    main()
