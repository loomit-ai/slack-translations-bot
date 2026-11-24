"""IF-03 · One retry around any `Translator`.

Retries after a timeout, a 429, a 5xx or a dropped connection, and (Product Owner decision 5)
after an empty or malformed answer. Other 4xx answers are not retried. The pause is 0.3 to 1.0
seconds, or the provider's `Retry-After` when it is 5 seconds or less; a longer one means no
second attempt. After the second failure the second error is raised, never a third call.

`on_retry` is shared by every request, so it cannot know which message to update. For a
per-request note use `retry_notice_scope` in `call_context`; both run when set.
"""

import contextlib
import random
from collections.abc import Awaitable, Callable
from typing import Final, Protocol, runtime_checkable

from slack_translator.domain.errors import RETRYABLE_MODEL_ERRORS, ModelError, ModelErrorCategory
from slack_translator.domain.languages import LanguageCode
from slack_translator.domain.ports import Clock, Translator
from slack_translator.infrastructure.call_context import attempt_scope, current_retry_notice

# Called once, with the first attempt's failure category, after the decision to retry and before
# the pause and the second attempt.
OnRetry = Callable[[ModelErrorCategory], Awaitable[None]]

MIN_PAUSE: Final = 0.3
MAX_PAUSE: Final = 1.0
MAX_RETRY_AFTER: Final = 5.0

# Decision 5 beats IF-03: an unusable answer gets one more try, like a transient failure.
RETRIED: Final = RETRYABLE_MODEL_ERRORS | {ModelErrorCategory.INVALID_RESPONSE}


@runtime_checkable
class _Closeable(Protocol):
    async def aclose(self) -> None: ...


class RetryingTranslator:
    def __init__(
        self,
        inner: Translator,
        *,
        clock: Clock,
        uniform: Callable[[float, float], float] = random.uniform,
        on_retry: OnRetry | None = None,
    ) -> None:
        self._inner = inner
        self._clock = clock
        self._uniform = uniform
        self._on_retry = on_retry

    async def aclose(self) -> None:
        if isinstance(self._inner, _Closeable):
            await self._inner.aclose()

    async def translate(
        self,
        text: str,
        target_language: str,
        source_language_hint: LanguageCode | None = None,
    ) -> str:
        try:
            return await self._inner.translate(text, target_language, source_language_hint)
        except ModelError as caught:
            failure = caught

        pause = self._pause_before_retry(failure)
        if pause is None:
            raise failure

        # A failing callback must never stop the retry, so its errors are dropped.
        if self._on_retry is not None:
            with contextlib.suppress(Exception):
                await self._on_retry(failure.category)
        notice = current_retry_notice()
        if notice is not None:
            with contextlib.suppress(Exception):
                await notice(failure)
        await self._clock.sleep(pause)
        with attempt_scope(2):
            return await self._inner.translate(text, target_language, source_language_hint)

    def _pause_before_retry(self, failure: ModelError) -> float | None:
        if failure.category not in RETRIED:
            return None
        if failure.retry_after is None:
            return self._uniform(MIN_PAUSE, MAX_PAUSE)
        if failure.retry_after > MAX_RETRY_AFTER:
            return None
        return failure.retry_after
