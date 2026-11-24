"""IF-03 retry rules, on their own and with the real adapter against the fake endpoint."""

import asyncio
import random

import pytest
from pydantic import SecretStr
from tests.support.fake_translator import Answer, Fail, FakeTranslatorServer, Hang, Malformed
from tests.support.model_fakes import RecordingTelemetry, SteppingClock

from slack_translator.domain.errors import ModelError, ModelErrorCategory
from slack_translator.domain.languages import LanguageCode
from slack_translator.domain.ports import Clock, Translator
from slack_translator.infrastructure.call_context import current_attempt, retry_notice_scope
from slack_translator.infrastructure.openai_translator import OpenAITranslator
from slack_translator.infrastructure.retrying_translator import RetryingTranslator

KOREAN = "파란 상자에 종이 별이 세 개 있어요."
MIDDLE = 0.65


def middle(low: float, high: float) -> float:
    return (low + high) / 2


def failure(
    category: ModelErrorCategory, retry_after: float | None = None, status: int | None = None
) -> ModelError:
    return ModelError(category, "translate", status, retry_after)


class ScriptedTranslator:
    def __init__(self, *outcomes: str | BaseException) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, str, LanguageCode | None]] = []
        self.attempts: list[int] = []

    async def translate(
        self, text: str, target_language: str, source_language_hint: LanguageCode | None = None
    ) -> str:
        self.calls.append((text, target_language, source_language_hint))
        self.attempts.append(current_attempt())
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def retrying(inner: Translator, clock: Clock) -> RetryingTranslator:
    return RetryingTranslator(inner, clock=clock, uniform=middle)


async def test_a_good_first_answer_is_returned_without_a_pause(clock: SteppingClock) -> None:
    inner = ScriptedTranslator("done")

    assert await retrying(inner, clock).translate("hola", "en") == "done"

    assert len(inner.calls) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize(
    "category",
    [
        ModelErrorCategory.TIMEOUT,
        ModelErrorCategory.RATE_LIMITED,
        ModelErrorCategory.UNAVAILABLE,
        ModelErrorCategory.INVALID_RESPONSE,
    ],
)
async def test_a_transient_failure_gets_one_more_try_after_a_short_pause(
    clock: SteppingClock, category: ModelErrorCategory
) -> None:
    inner = ScriptedTranslator(failure(category), "second time lucky")

    result = await retrying(inner, clock).translate("hola", "en", LanguageCode("es"))

    assert result == "second time lucky"
    assert inner.calls == [("hola", "en", "es"), ("hola", "en", "es")]
    assert inner.attempts == [1, 2]
    assert clock.sleeps == [MIDDLE]


async def test_the_default_pause_stays_between_0_3_and_1_second(clock: SteppingClock) -> None:
    random.seed(7)
    for _ in range(40):
        inner = ScriptedTranslator(failure(ModelErrorCategory.TIMEOUT), "ok")
        await RetryingTranslator(inner, clock=clock).translate("hola", "en")

    assert len(clock.sleeps) == 40
    assert all(0.3 <= pause <= 1.0 for pause in clock.sleeps)
    assert len(set(clock.sleeps)) > 1


@pytest.mark.parametrize("retry_after", [0.0, 2.0, 5.0])
async def test_a_short_retry_after_is_the_pause(clock: SteppingClock, retry_after: float) -> None:
    inner = ScriptedTranslator(failure(ModelErrorCategory.RATE_LIMITED, retry_after, 429), "ok")

    await retrying(inner, clock).translate("hola", "en")

    assert clock.sleeps == [retry_after]
    assert len(inner.calls) == 2


@pytest.mark.parametrize(
    "category", [ModelErrorCategory.RATE_LIMITED, ModelErrorCategory.UNAVAILABLE]
)
async def test_a_retry_after_over_5_seconds_means_no_second_attempt(
    clock: SteppingClock, category: ModelErrorCategory
) -> None:
    first = failure(category, 5.01, 429)
    inner = ScriptedTranslator(first, "never used")

    with pytest.raises(ModelError) as raised:
        await retrying(inner, clock).translate("hola", "en")

    assert raised.value is first
    assert len(inner.calls) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("category", [ModelErrorCategory.REJECTED])
async def test_a_failure_that_cannot_improve_is_not_retried(
    clock: SteppingClock, category: ModelErrorCategory
) -> None:
    first = failure(category, status=401)
    inner = ScriptedTranslator(first, "never used")

    with pytest.raises(ModelError) as raised:
        await retrying(inner, clock).translate("hola", "en")

    assert raised.value is first
    assert len(inner.calls) == 1
    assert clock.sleeps == []


async def test_two_failures_raise_the_second_error_and_make_no_third_call(
    clock: SteppingClock,
) -> None:
    second = failure(ModelErrorCategory.UNAVAILABLE, status=503)
    inner = ScriptedTranslator(failure(ModelErrorCategory.TIMEOUT), second, "never used")

    with pytest.raises(ModelError) as raised:
        await retrying(inner, clock).translate("hola", "en")

    assert raised.value is second
    assert len(inner.calls) == 2


async def test_other_exceptions_are_not_retried(clock: SteppingClock) -> None:
    inner = ScriptedTranslator(ValueError("bug"), "never used")

    with pytest.raises(ValueError, match="bug"):
        await retrying(inner, clock).translate("hola", "en")

    assert len(inner.calls) == 1


async def test_the_retry_scope_is_closed_after_the_second_attempt(clock: SteppingClock) -> None:
    inner = ScriptedTranslator(failure(ModelErrorCategory.TIMEOUT), "ok")
    await retrying(inner, clock).translate("hola", "en")

    assert current_attempt() == 1


async def test_cancelling_during_the_pause_makes_no_second_call() -> None:
    class StuckClock(SteppingClock):
        entered = asyncio.Event()

        async def sleep(self, seconds: float) -> None:
            self.entered.set()
            await asyncio.Event().wait()

    stuck = StuckClock()
    inner = ScriptedTranslator(failure(ModelErrorCategory.TIMEOUT), "never used")
    task = asyncio.create_task(retrying(inner, stuck).translate("hola", "en"))
    await stuck.entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(inner.calls) == 1


async def test_a_notice_hears_of_the_first_failure_before_the_pause() -> None:
    events: list[str] = []
    first = failure(ModelErrorCategory.TIMEOUT)

    class OrderedClock(SteppingClock):
        async def sleep(self, seconds: float) -> None:
            events.append("pause")

    async def notice(error: ModelError) -> None:
        events.append(f"notice:{error.category.value}")

    inner = ScriptedTranslator(first, "ok")
    with retry_notice_scope(notice):
        await retrying(inner, OrderedClock()).translate("hola", "en")

    assert events == ["notice:timeout", "pause"]


async def test_a_notice_that_fails_does_not_stop_the_retry(clock: SteppingClock) -> None:
    async def notice(error: ModelError) -> None:
        raise RuntimeError("Slack is down")

    inner = ScriptedTranslator(failure(ModelErrorCategory.UNAVAILABLE), "ok")
    with retry_notice_scope(notice):
        assert await retrying(inner, clock).translate("hola", "en") == "ok"


async def test_no_notice_when_there_is_nothing_to_retry(clock: SteppingClock) -> None:
    heard: list[ModelError] = []

    async def notice(error: ModelError) -> None:
        heard.append(error)

    with retry_notice_scope(notice):
        await retrying(ScriptedTranslator("fine"), clock).translate("hola", "en")
        with pytest.raises(ModelError):
            await retrying(
                ScriptedTranslator(failure(ModelErrorCategory.REJECTED, status=400)), clock
            ).translate("hola", "en")
        with pytest.raises(ModelError):
            await retrying(
                ScriptedTranslator(failure(ModelErrorCategory.RATE_LIMITED, 30.0, 429)), clock
            ).translate("hola", "en")

    assert heard == []


async def test_close_reaches_the_adapter_it_wraps(clock: SteppingClock) -> None:
    class Closeable(ScriptedTranslator):
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    inner = Closeable()
    await retrying(inner, clock).aclose()
    assert inner.closed

    await retrying(ScriptedTranslator(), clock).aclose()


# The same rules with the real adapter and the fake endpoint (TC-041).


async def with_real_adapter(
    fake: FakeTranslatorServer,
    clock: SteppingClock,
    recorder: RecordingTelemetry,
    api_key: str,
    deadline: float = 2.0,
) -> tuple[RetryingTranslator, OpenAITranslator]:
    adapter = OpenAITranslator(
        base_url=fake.base_url,
        model="test-model",
        api_key=SecretStr(api_key),
        timeout=deadline,
        telemetry=recorder.as_port(),
        clock=clock,
    )
    return RetryingTranslator(adapter, clock=clock, uniform=middle), adapter


@pytest.mark.parametrize(
    "first",
    [
        Fail(429, retry_after=1),
        Fail(500),
        Fail(503),
        Malformed("empty_content"),
        Malformed("no_choices"),
        Malformed("truncated"),
    ],
)
async def test_first_failure_then_success_gives_one_result(
    fake: FakeTranslatorServer,
    clock: SteppingClock,
    recorder: RecordingTelemetry,
    api_key: str,
    first: Fail | Malformed,
) -> None:
    translator, adapter = await with_real_adapter(fake, clock, recorder, api_key)
    fake.enqueue(first, Answer("Three paper stars."))
    try:
        result = await translator.translate(KOREAN, "en", LanguageCode("ko"))
    finally:
        await adapter.aclose()

    assert result == "Three paper stars."
    assert len(fake.requests) == 2
    assert [r.attempt for r in recorder.records] == [1, 2]
    assert recorder.records[-1].outcome == "ok"


async def test_a_timeout_then_success_gives_one_result(
    fake: FakeTranslatorServer, clock: SteppingClock, recorder: RecordingTelemetry, api_key: str
) -> None:
    translator, adapter = await with_real_adapter(fake, clock, recorder, api_key, deadline=0.2)
    fake.enqueue(Hang(), Answer("Three paper stars."))
    try:
        assert await translator.translate(KOREAN, "en") == "Three paper stars."
    finally:
        await adapter.aclose()

    assert [r.outcome for r in recorder.records] == ["timeout", "ok"]


@pytest.mark.parametrize("fault", [Fail(429), Fail(502), Malformed("not_json")])
async def test_two_failures_give_one_retry_then_the_error(
    fake: FakeTranslatorServer,
    clock: SteppingClock,
    recorder: RecordingTelemetry,
    api_key: str,
    fault: Fail | Malformed,
) -> None:
    translator, adapter = await with_real_adapter(fake, clock, recorder, api_key)
    fake.set_default(fault)
    try:
        with pytest.raises(ModelError):
            await translator.translate(KOREAN, "en")
    finally:
        await adapter.aclose()

    assert len(fake.requests) == 2


async def test_a_rejected_request_is_sent_once(
    fake: FakeTranslatorServer, clock: SteppingClock, recorder: RecordingTelemetry, api_key: str
) -> None:
    translator, adapter = await with_real_adapter(fake, clock, recorder, api_key)
    fake.set_default(Fail(401))
    try:
        with pytest.raises(ModelError) as raised:
            await translator.translate(KOREAN, "en")
    finally:
        await adapter.aclose()

    assert raised.value.category is ModelErrorCategory.REJECTED
    assert len(fake.requests) == 1
