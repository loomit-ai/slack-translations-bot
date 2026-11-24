"""IF-02 and ADR-005: the Lingua classifier, on the Test plan's made-up fixtures."""

import asyncio
import secrets
import threading
import time
from collections.abc import AsyncIterator, Sequence

import pytest
from tests.support.model_fakes import RecordingTelemetry, SteppingClock

from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelError, ModelErrorCategory
from slack_translator.domain.languages import (
    LanguageCandidate,
    LanguageCode,
    candidates_of,
    parse_language_seed,
)
from slack_translator.infrastructure.lingua_classifier import LinguaClassifier, Scorer

K1 = "파란 상자에 종이 별이 세 개 있어요."
R1 = "В синей коробке лежат три бумажные звезды."
V1 = "Có ba ngôi sao giấy trong hộp màu xanh."
Z1 = "请把三颗纸星星放进蓝色盒子里。"
Z2 = "請把三顆紙星星放進藍色盒子裡。"
E1 = "I put three paper stars in the blue box."
S1 = "Puse tres estrellas de papel en la caja azul."
F1 = "La boîte bleue contient trois étoiles en papier."
J1 = "青い箱に紙の星が三つ入っています。"

SEED = candidates_of(
    parse_language_seed("en:team,es:team,ru:customer,ko:customer,vi:customer,zh:customer")
)


def candidate(code: str, name: str) -> LanguageCandidate:
    return LanguageCandidate(LanguageCode(code), name)


@pytest.fixture
async def classifier(
    recorder: RecordingTelemetry, clock: SteppingClock
) -> AsyncIterator[LinguaClassifier]:
    adapter = LinguaClassifier(telemetry=recorder.as_port(), clock=clock, timeout=5.0)
    yield adapter
    await adapter.aclose()


def with_scorer(
    recorder: RecordingTelemetry,
    scorer: Scorer,
    *,
    timeout: float = 5.0,
    clock: SteppingClock | None = None,
) -> LinguaClassifier:
    return LinguaClassifier(
        telemetry=recorder.as_port(),
        clock=clock or SteppingClock(),
        timeout=timeout,
        scorer=scorer,
    )


@pytest.mark.parametrize(
    ("text", "code"),
    [(K1, "ko"), (R1, "ru"), (V1, "vi"), (Z1, "zh"), (Z2, "zh"), (E1, "en"), (S1, "es")],
)
async def test_configured_languages_are_recognised(
    classifier: LinguaClassifier, text: str, code: str
) -> None:
    result = await classifier.detect(text, SEED)

    assert result.language == code
    assert 0.0 < result.confidence <= 1.0


async def test_chinese_does_not_name_a_script(classifier: LinguaClassifier) -> None:
    assert (await classifier.detect(Z1, SEED)).script is None
    assert (await classifier.detect(Z2, SEED)).script is None


async def test_a_language_outside_the_candidates_is_other_with_lingua_confidence(
    classifier: LinguaClassifier,
) -> None:
    outside = await classifier.detect(F1, SEED)
    inside = await classifier.detect(F1, [*SEED, candidate("fr", "French")])

    assert outside.language == "other"
    assert inside.language == "fr"
    assert outside.confidence == inside.confidence > 0.5


async def test_japanese_is_other_until_it_is_configured(classifier: LinguaClassifier) -> None:
    assert (await classifier.detect(J1, SEED)).language == "other"
    assert (await classifier.detect(J1, [*SEED, candidate("ja", "Japanese")])).language == "ja"


async def test_a_supported_language_that_is_not_a_candidate_is_other(
    classifier: LinguaClassifier,
) -> None:
    only_english = [candidate("en", "English")]

    assert (await classifier.detect(K1, only_english)).language == "other"
    assert (await classifier.detect(K1, [])).language == "other"


async def test_text_with_no_signal_is_other_with_zero_confidence(
    classifier: LinguaClassifier,
) -> None:
    assert await classifier.detect("12345 !!!", SEED) == Detection(
        language="other", confidence=0.0, script=None
    )


@pytest.mark.parametrize("text", [S1, E1, V1, F1])
async def test_the_same_text_gives_the_same_result(classifier: LinguaClassifier, text: str) -> None:
    results = {await classifier.detect(text, SEED) for _ in range(25)}

    assert len(results) == 1


async def test_models_load_on_demand_and_preload_is_repeatable(
    recorder: RecordingTelemetry, clock: SteppingClock
) -> None:
    adapter = LinguaClassifier(telemetry=recorder.as_port(), clock=clock, timeout=5.0)
    try:
        assert not adapter.is_loaded
        await adapter.preload()
        assert adapter.is_loaded
        await adapter.preload()
        assert (await adapter.detect(K1, SEED)).language == "ko"
    finally:
        await adapter.aclose()


async def test_a_first_detect_without_preload_still_works(
    recorder: RecordingTelemetry, clock: SteppingClock
) -> None:
    adapter = LinguaClassifier(telemetry=recorder.as_port(), clock=clock, timeout=5.0)
    try:
        assert (await adapter.detect(R1, SEED)).language == "ru"
        assert adapter.is_loaded
    finally:
        await adapter.aclose()


async def test_success_emits_one_record_without_the_text(
    recorder: RecordingTelemetry,
) -> None:
    adapter = LinguaClassifier(
        telemetry=recorder.as_port(), clock=SteppingClock(tick=0.05), timeout=5.0
    )
    try:
        await adapter.detect(K1, SEED)
    finally:
        await adapter.aclose()

    [record] = recorder.records
    assert record.operation == "detect"
    assert record.provider == "lingua"
    assert record.model.startswith("lingua-")
    assert record.attempt == 1
    assert record.latency_ms == 50
    assert record.outcome == "ok"
    assert record.http_status is None
    assert record.input_chars == len(K1)
    assert (record.prompt_tokens, record.completion_tokens) == (None, None)
    assert K1 not in repr(record)


# The rules that need a detector we can control: timeout, failure, concurrency.


def scores(*pairs: tuple[str, float]) -> Scorer:
    def scorer(text: str) -> Sequence[tuple[str, float]]:
        return pairs

    return scorer


async def test_a_result_above_one_or_below_zero_is_clamped(recorder: RecordingTelemetry) -> None:
    high = with_scorer(recorder, scores(("ko", 1.0000001)))
    low = with_scorer(recorder, scores(("ko", -0.2)))
    try:
        assert (await high.detect(K1, SEED)).confidence == 1.0
        assert await low.detect(K1, SEED) == Detection("other", 0.0)
    finally:
        await high.aclose()
        await low.aclose()


async def test_an_empty_answer_from_the_detector_is_other_with_zero_confidence(
    recorder: RecordingTelemetry,
) -> None:
    adapter = with_scorer(recorder, scores())
    try:
        assert await adapter.detect(K1, SEED) == Detection("other", 0.0)
    finally:
        await adapter.aclose()


async def test_a_slow_detection_times_out_and_the_next_one_works(
    recorder: RecordingTelemetry,
) -> None:
    calls = 0

    def sometimes_slow(text: str) -> Sequence[tuple[str, float]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            time.sleep(0.4)
        return (("ko", 0.99),)

    adapter = with_scorer(recorder, sometimes_slow, timeout=0.1)
    try:
        with pytest.raises(ModelError) as raised:
            await adapter.detect(K1, SEED)
        assert raised.value.category is ModelErrorCategory.TIMEOUT
        assert raised.value.operation == "detect"
        assert (await adapter.detect(K1, SEED)).language == "ko"
    finally:
        await adapter.aclose()

    assert [r.outcome for r in recorder.records] == ["timeout", "ok"]


async def test_a_detector_failure_is_an_invalid_response_without_the_text(
    recorder: RecordingTelemetry,
) -> None:
    marker = f"SOURCE-{secrets.token_hex(6)}"

    def broken(text: str) -> Sequence[tuple[str, float]]:
        raise RuntimeError(f"cannot read {text}")

    adapter = with_scorer(recorder, broken)
    try:
        with pytest.raises(ModelError) as raised:
            await adapter.detect(f"{marker} {K1}", SEED)
    finally:
        await adapter.aclose()

    error = raised.value
    assert error.category is ModelErrorCategory.INVALID_RESPONSE
    assert marker not in f"{error!s} {error!r} {error.args}"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert [r.outcome for r in recorder.records] == ["invalid_response"]
    assert marker not in repr(recorder.records)


async def test_at_most_four_detections_run_at_once(recorder: RecordingTelemetry) -> None:
    lock = threading.Lock()
    running = 0
    peak = 0

    def counting(text: str) -> Sequence[tuple[str, float]]:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return (("ko", 0.99),)

    adapter = with_scorer(recorder, counting)
    try:
        results = await asyncio.gather(*(adapter.detect(K1, SEED) for _ in range(12)))
    finally:
        await adapter.aclose()

    assert all(r.language == "ko" for r in results)
    assert peak == 4
    assert len(recorder.records) == 12


async def test_the_event_loop_stays_free_while_a_detection_runs(
    recorder: RecordingTelemetry,
) -> None:
    def slow(text: str) -> Sequence[tuple[str, float]]:
        time.sleep(0.3)
        return (("ko", 0.99),)

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    adapter = with_scorer(recorder, slow)
    watcher = asyncio.create_task(ticker())
    try:
        await adapter.detect(K1, SEED)
    finally:
        watcher.cancel()
        await adapter.aclose()

    assert ticks >= 10


async def test_cancelling_a_detection_emits_no_record(recorder: RecordingTelemetry) -> None:
    started = threading.Event()

    def slow(text: str) -> Sequence[tuple[str, float]]:
        started.set()
        time.sleep(0.2)
        return (("ko", 0.99),)

    adapter = with_scorer(recorder, slow)
    task = asyncio.create_task(adapter.detect(K1, SEED))
    try:
        await asyncio.to_thread(started.wait, 2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await adapter.aclose()

    assert recorder.records == []
