"""Per-request facts that the ports cannot carry (ADR-003).

`Translator.translate` takes no attempt number, and no port takes a correlation ID. The model
adapters and the telemetry read them from here instead. Everything defaults to "not set", so code
that never opens a scope behaves as before.

- `attempt_scope`: the action marks the second try of a translation, so the call record says
  `attempt=2`.
- `correlation_scope`: `delivery` opens it per request so `model.call` lines share the request's ID.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_attempt: ContextVar[int] = ContextVar("model_call_attempt", default=1)
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def current_attempt() -> int:
    return _attempt.get()


def current_correlation_id() -> str | None:
    return _correlation_id.get()


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
