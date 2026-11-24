"""IF-03 · The one retry: whether to make it, and how long to wait first."""

import pytest

from slack_translator.domain.errors import ModelError, ModelErrorCategory
from slack_translator.domain.retry import MAX_PAUSE, MAX_RETRY_AFTER, MIN_PAUSE, retry_pause


def failure(category: ModelErrorCategory, retry_after: float | None = None) -> ModelError:
    return ModelError(category, "translate", None, retry_after)


def middle(low: float, high: float) -> float:
    return (low + high) / 2


@pytest.mark.parametrize(
    "category",
    [
        ModelErrorCategory.TIMEOUT,
        ModelErrorCategory.RATE_LIMITED,
        ModelErrorCategory.UNAVAILABLE,
        ModelErrorCategory.INVALID_RESPONSE,
    ],
)
def test_a_failure_that_can_improve_waits_between_0_3_and_1_second(
    category: ModelErrorCategory,
) -> None:
    assert retry_pause(failure(category), middle) == 0.65


def test_the_pause_is_drawn_from_the_allowed_range() -> None:
    seen: list[tuple[float, float]] = []

    def record(low: float, high: float) -> float:
        seen.append((low, high))
        return low

    retry_pause(failure(ModelErrorCategory.TIMEOUT), record)

    assert seen == [(MIN_PAUSE, MAX_PAUSE)] == [(0.3, 1.0)]


def test_a_failure_that_cannot_improve_is_not_retried() -> None:
    assert retry_pause(failure(ModelErrorCategory.REJECTED), middle) is None


@pytest.mark.parametrize("retry_after", [0.0, 2.0, 5.0])
def test_a_retry_after_of_five_seconds_or_less_is_the_pause(retry_after: float) -> None:
    error = failure(ModelErrorCategory.RATE_LIMITED, retry_after)

    assert retry_pause(error, middle) == retry_after


@pytest.mark.parametrize(
    "category", [ModelErrorCategory.RATE_LIMITED, ModelErrorCategory.UNAVAILABLE]
)
def test_a_retry_after_over_five_seconds_means_no_second_attempt(
    category: ModelErrorCategory,
) -> None:
    assert MAX_RETRY_AFTER == 5.0
    assert retry_pause(failure(category, 5.01), middle) is None
    assert retry_pause(failure(category, 3600.0), middle) is None
