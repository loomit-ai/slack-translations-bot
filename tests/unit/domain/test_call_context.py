"""Per-request facts that the ports do not carry: the attempt number and the correlation ID."""

import asyncio

import pytest

from slack_translator.domain.call_context import (
    attempt_scope,
    correlation_scope,
    current_attempt,
    current_correlation_id,
)


def test_outside_any_scope_it_is_the_first_attempt_and_there_is_no_correlation_id() -> None:
    assert current_attempt() == 1
    assert current_correlation_id() is None


def test_a_scope_sets_its_value_and_restores_the_one_before() -> None:
    with attempt_scope(2):
        assert current_attempt() == 2
        with attempt_scope(3):
            assert current_attempt() == 3
        assert current_attempt() == 2
    assert current_attempt() == 1

    with correlation_scope("outer"):
        with correlation_scope("inner"):
            assert current_correlation_id() == "inner"
        assert current_correlation_id() == "outer"
    assert current_correlation_id() is None


def test_a_scope_is_closed_even_when_the_block_raises() -> None:
    with pytest.raises(RuntimeError), attempt_scope(2), correlation_scope("c1"):
        raise RuntimeError

    assert current_attempt() == 1
    assert current_correlation_id() is None


async def test_each_task_sees_its_own_correlation_id() -> None:
    seen: dict[str, str | None] = {}

    async def request(name: str) -> None:
        with correlation_scope(name):
            await asyncio.sleep(0)
            seen[name] = current_correlation_id()

    await asyncio.gather(request("a"), request("b"), request("c"))

    assert seen == {"a": "a", "b": "b", "c": "c"}
    assert current_correlation_id() is None
