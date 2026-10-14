"""Per-process asyncio loop for the worker.

Under --pool=solo, all tasks run in one process, one thread. A single
process-wide loop means the asyncpg engine pool is always bound to the
same loop, so cached connections never complain about being "attached
to a different loop". The previous threading.local() version was
designed for prefork; on solo it created a new loop per task thread and
split the engine's pool across loops.
"""
import asyncio

_LOOP: asyncio.AbstractEventLoop | None = None


def get_loop() -> asyncio.AbstractEventLoop:
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP


def run(coro):
    return get_loop().run_until_complete(coro)
