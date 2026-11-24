"""Per-request facts that the frozen ports cannot carry (ADR-003).

`Translator.translate` and `LanguageClassifier.detect` take no attempt number, no correlation ID
and no callback. The adapters read them from here instead. Everything defaults to "not set", so
code that never opens a scope behaves as before.

- `attempt_scope`: `RetryingTranslator` marks its second try, so the call record says `attempt=2`.
- `correlation_scope`: `delivery` opens it per request so `model.call` lines share the request's ID.
- `retry_notice_scope`: `delivery` registers an async callback that runs between a failed first
  attempt and the retry (the "An error has occurred…" progress note).
"""

from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar

from slack_translator.domain.errors import ModelError

RetryNotice = Callable[[ModelError], Awaitable[None]]

_attempt: ContextVar[int] = ContextVar("model_call_attempt", default=1)
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_retry_notice: ContextVar[RetryNotice | None] = ContextVar("retry_notice", default=None)


def current_attempt() -> int:
    return _attempt.get()


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def current_retry_notice() -> RetryNotice | None:
    return _retry_notice.get()


@contextmanager
def attempt_scope(attempt: int) -> Generator[None]:
    token = _attempt.set(attempt)
    try:
        yield
    finally:
        _attempt.reset(token)


@contextmanager
def correlation_scope(correlation_id: str) -> Generator[None]:
    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


@contextmanager
def retry_notice_scope(notice: RetryNotice) -> Generator[None]:
    token = _retry_notice.set(notice)
    try:
        yield
    finally:
        _retry_notice.reset(token)
