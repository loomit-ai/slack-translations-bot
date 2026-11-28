"""ttl_cache · A small bounded store with a lifetime and an injected clock (TS-06)."""

from tests.support.fake_ports import FakeClock

from slack_translator.infrastructure.ttl_cache import TtlCache


def test_a_value_is_kept_until_its_lifetime_ends() -> None:
    clock = FakeClock()
    cache: TtlCache[str, int] = TtlCache(clock, ttl=600, max_size=10)
    cache.set("a", 1)
    clock.advance(599)
    assert cache.get("a") == 1
    clock.advance(1)
    assert cache.get("a") is None


def test_a_missing_key_is_none() -> None:
    assert TtlCache[str, int](FakeClock(), ttl=1, max_size=1).get("x") is None


def test_the_oldest_entry_goes_first_when_the_store_is_full() -> None:
    clock = FakeClock()
    cache: TtlCache[str, int] = TtlCache(clock, ttl=600, max_size=2)
    cache.set("a", 1)
    clock.advance(1)
    cache.set("b", 2)
    clock.advance(1)
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_setting_a_key_again_renews_its_lifetime() -> None:
    clock = FakeClock()
    cache: TtlCache[str, int] = TtlCache(clock, ttl=10, max_size=5)
    cache.set("a", 1)
    clock.advance(8)
    cache.set("a", 2)
    clock.advance(8)
    assert cache.get("a") == 2


def test_a_discarded_key_is_gone_and_discarding_a_missing_key_is_harmless() -> None:
    cache: TtlCache[str, int] = TtlCache(FakeClock(), ttl=600, max_size=5)
    cache.set("a", 1)
    cache.discard("a")
    cache.discard("never set")
    assert cache.get("a") is None
