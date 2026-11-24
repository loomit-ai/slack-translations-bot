"""A small bounded store whose entries expire. Time comes from the injected `Clock` (TS-06)."""

from slack_translator.domain.ports import Clock


class TtlCache[K, V]:
    def __init__(self, clock: Clock, ttl: float, max_size: int) -> None:
        self._clock = clock
        self._ttl = ttl
        self._max_size = max_size
        self._entries: dict[K, tuple[float, V]] = {}

    def get(self, key: K) -> V | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock.now() >= expires_at:
            del self._entries[key]
            return None
        return value

    def discard(self, key: K) -> None:
        self._entries.pop(key, None)

    def set(self, key: K, value: V) -> None:
        self._entries.pop(key, None)
        while len(self._entries) >= self._max_size:
            del self._entries[next(iter(self._entries))]
        self._entries[key] = (self._clock.now() + self._ttl, value)
