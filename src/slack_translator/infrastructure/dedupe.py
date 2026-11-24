"""REQ-03 · Duplicate message events: one check-and-insert step, 10 minutes, 10,000 keys."""

from slack_translator.domain.ports import Clock
from slack_translator.infrastructure.ttl_cache import TtlCache

KEY_LIFETIME = 600.0
MAX_KEYS = 10_000


class DuplicateGuard:
    """Remembers event keys. `first_time` checks and inserts with no `await` in between, so two
    deliveries of one event can never both pass."""

    def __init__(
        self, clock: Clock, *, ttl: float = KEY_LIFETIME, max_keys: int = MAX_KEYS
    ) -> None:
        self._seen = TtlCache[str, bool](clock, ttl, max_keys)

    def first_time(self, key: str) -> bool:
        if self._seen.get(key) is not None:
            return False
        self._seen.set(key, True)
        return True
