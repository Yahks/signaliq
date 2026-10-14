"""Run the article-schedule check every 60 seconds.

Container-friendly. Runs as a long-lived process under
docker compose (service: scheduler). Replaces host crontab,
which does not exist on Windows.

    python -m scripts.scheduler_loop
"""

import asyncio
import time

from packages.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


async def _tick() -> None:
    from scripts.fire_scheduled import main as fire
    await fire()


def run() -> None:
    log.info("scheduler_loop.start", interval_seconds=60)
    while True:
        try:
            asyncio.run(_tick())
        except Exception as exc:
            log.warning("scheduler_loop.tick_failed", error=str(exc)[:200])
        time.sleep(60)


if __name__ == "__main__":
    run()
