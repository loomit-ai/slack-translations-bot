"""TS-06 · The real `Clock`: a monotonic reading and a real sleep."""

import asyncio
import time


class SystemClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
