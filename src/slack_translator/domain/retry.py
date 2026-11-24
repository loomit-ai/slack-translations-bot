"""IF-03 · The one retry after a failed model call.

A pure rule: the caller makes the second attempt and does the waiting. A failure that can
improve (timeout, rate limit, unavailable, an unusable answer) waits 0.3 to 1.0 seconds, or the
provider's `Retry-After` when that is 5 seconds or less. A longer `Retry-After` means no second
attempt. Any other failure is not retried.
"""

from collections.abc import Callable
from typing import Final

from slack_translator.domain.errors import ModelError

MIN_PAUSE: Final = 0.3
MAX_PAUSE: Final = 1.0
MAX_RETRY_AFTER: Final = 5.0


def retry_pause(error: ModelError, uniform: Callable[[float, float], float]) -> float | None:
    """Seconds to wait before the second attempt, or `None` for no second attempt."""
    if not error.retryable:
        return None
    if error.retry_after is None:
        return uniform(MIN_PAUSE, MAX_PAUSE)
    if error.retry_after > MAX_RETRY_AFTER:
        return None
    return error.retry_after
