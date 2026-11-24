"""REQ-03 · Duplicate events: check and insert in one step, 10 minutes, 10,000 keys."""

import asyncio

from tests.support.fake_ports import FakeClock

from slack_translator.infrastructure.dedupe import DuplicateGuard


def test_the_first_sight_of_a_key_passes_and_every_repeat_is_a_duplicate() -> None:
    guard = DuplicateGuard(FakeClock())

    assert guard.first_time("Ev1") is True
    assert guard.first_time("Ev1") is False
    assert guard.first_time("Ev1") is False
    assert guard.first_time("Ev2") is True


def test_a_key_is_forgotten_after_ten_minutes() -> None:
    clock = FakeClock()
    guard = DuplicateGuard(clock)
    guard.first_time("Ev1")

    clock.advance(599)
    assert guard.first_time("Ev1") is False

    clock.advance(2)
    assert guard.first_time("Ev1") is True


def test_the_store_is_bounded_and_forgets_the_oldest_key_first() -> None:
    guard = DuplicateGuard(FakeClock(), max_keys=3)
    for key in ("a", "b", "c"):
        guard.first_time(key)

    assert guard.first_time("d") is True

    assert guard.first_time("a") is True
    assert guard.first_time("d") is False


def test_the_default_bound_is_ten_thousand_keys() -> None:
    guard = DuplicateGuard(FakeClock())
    for number in range(10_001):
        guard.first_time(f"Ev{number}")

    assert guard.first_time("Ev0") is True
    assert guard.first_time("Ev10000") is False


async def test_two_events_handled_at_once_let_exactly_one_through() -> None:
    guard = DuplicateGuard(FakeClock())

    async def arrive() -> bool:
        return guard.first_time("Ev1")

    results = await asyncio.gather(*(arrive() for _ in range(20)))

    assert results.count(True) == 1
