"""TS-05 · Work that continues after `ack()` is registered here, so tests can wait for it.

Tests call `await registry.drain()` instead of sleeping, and shutdown gives in-flight work a
bounded time to finish (REQ-11).
"""

import asyncio
from collections.abc import Coroutine
from typing import Any


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    async def run(self, work: Coroutine[Any, Any, None]) -> None:
        """Start `work`, remember it until it ends, and wait for it."""
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await task

    async def drain(self, within: float | None = None) -> None:
        """Wait until nothing is in flight. Gives up after `within` seconds."""
        for _ in range(3):
            await asyncio.sleep(0)
        while self._tasks:
            pending = list(self._tasks)
            _, still_running = await asyncio.wait(pending, timeout=within)
            if still_running:
                return

    @property
    def in_flight(self) -> int:
        return len(self._tasks)
