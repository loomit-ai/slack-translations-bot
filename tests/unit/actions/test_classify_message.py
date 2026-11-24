"""FLOW-02 steps 1 to 5: events are filtered, de-duplicated, classified, counted and handed on."""

import asyncio
from collections.abc import Sequence
from dataclasses import fields, replace
from typing import Any

import pytest
from tests.support.fake_ports import FakeClock, RecordingTelemetry

from slack_translator.actions.classify_message import ClassifyMessage, IntakePolicy
from slack_translator.domain.detection import Detection
from slack_translator.domain.errors import ModelError, ModelErrorCategory
from slack_translator.domain.intake import MessageEvent
from slack_translator.domain.languages import (
    LanguageCandidate,
    LanguageCode,
    candidates_of,
    parse_language_seed,
)
from slack_translator.domain.messages import MessageRef, OfferRequest
from slack_translator.infrastructure.dedupe import DuplicateGuard

CHANNEL = "C0123ABCDE"
LANGUAGES = parse_language_seed("en:team,es:team,ru:customer,ko:customer,vi:customer,zh:customer")
KOREAN = "파란 상자에 종이 별이 세 개 있어요."


def code(value: str) -> LanguageCode:
    return LanguageCode(value)


def message(**changes: Any) -> MessageEvent:
    base = MessageEvent(
        event_id="Ev0123ABCDE",
        channel=CHANNEL,
        ts="1700000000.000100",
        thread_root_ts=None,
        user_id="U0123ABCDE",
        text=KOREAN,
        subtype=None,
        bot_id=None,
    )
    return replace(base, **changes)


class RecordingClassifier:
    """TS-03. Remembers what it was asked; answers, fails or waits as told."""

    def __init__(self, result: Detection | Exception | None = None) -> None:
        self.result: Detection | Exception = result or Detection(code("ko"), 0.95)
        self.texts: list[str] = []
        self.candidates: list[Sequence[LanguageCandidate]] = []
        self.hold: asyncio.Event | None = None

    async def detect(self, text: str, candidates: Sequence[LanguageCandidate]) -> Detection:
        self.texts.append(text)
        self.candidates.append(candidates)
        if self.hold is not None:
            await self.hold.wait()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class RecordingOffers:
    def __init__(self) -> None:
        self.requests: list[OfferRequest] = []
        self.error: Exception | None = None

    async def send(self, request: OfferRequest) -> None:
        self.requests.append(request)
        if self.error is not None:
            raise self.error


class Rig:
    def __init__(
        self, *, result: Detection | Exception | None = None, timeout: float = 3.0
    ) -> None:
        self.classifier = RecordingClassifier(result)
        self.telemetry = RecordingTelemetry()
        self.offers = RecordingOffers()
        self.clock = FakeClock()
        self.intake = ClassifyMessage(
            policy=IntakePolicy(
                languages=LANGUAGES, min_confidence=0.7, classifier_timeout=timeout
            ),
            classifier=self.classifier,
            telemetry=self.telemetry,
            seen=DuplicateGuard(self.clock),
            offers=self.offers,
            clock=self.clock,
            is_served=lambda channel: channel == CHANNEL,
        )

    async def run(self, event: MessageEvent, correlation_id: str = "corr-1") -> None:
        await self.intake.run(event, correlation_id)

    def classified(self) -> list[dict[str, Any]]:
        return [e.fields for e in self.telemetry.named("classify_completed")]

    def dropped(self) -> list[dict[str, Any]]:
        return [e.fields for e in self.telemetry.named("event_dropped")]

    def internal_errors(self) -> list[dict[str, Any]]:
        return [e.fields for e in self.telemetry.named("internal_error")]


async def test_a_confident_customer_message_is_counted_and_handed_to_the_offer_step() -> None:
    rig = Rig(result=Detection(code("ko"), 0.95))

    await rig.run(message(thread_root_ts="1700000000.000050"))

    [request] = rig.offers.requests
    assert request == OfferRequest(
        correlation_id="corr-1",
        ref=MessageRef(channel=CHANNEL, ts="1700000000.000100", thread_root_ts="1700000000.000050"),
        author_id="U0123ABCDE",
        language=code("ko"),
        script=None,
        received_at=1000.0,
    )
    assert rig.classified() == [
        {
            "language": "ko",
            "result": "customer",
            "confidence": 0.95,
            "script": None,
            "entry": None,
            "correlation_id": "corr-1",
            "channel_id": CHANNEL,
        }
    ]
    assert rig.dropped() == []


async def test_chinese_carries_its_script_to_the_offer_step() -> None:
    rig = Rig(result=Detection(code("zh"), 1.0, "Hant"))

    await rig.run(message(text="請把三顆紙星星放進藍色盒子裡。"))

    [request] = rig.offers.requests
    assert (request.language, request.script) == ("zh", "Hant")
    assert rig.classified()[0]["script"] == "Hant"


async def test_detection_sees_the_cleaned_text_and_every_configured_language() -> None:
    rig = Rig()

    await rig.run(message(text="Hola <@U0123ABCDE> :tada: <https://x.com|link> gracias"))

    assert rig.classifier.texts == ["Hola gracias"]
    assert rig.classifier.candidates == [candidates_of(LANGUAGES)]
    assert {c.code for c in rig.classifier.candidates[0]} == {"en", "es", "ru", "ko", "vi", "zh"}


async def test_the_offer_request_never_holds_the_message_text() -> None:
    rig = Rig()

    await rig.run(message(text=f"SECRET-MARKER {KOREAN}"))

    [request] = rig.offers.requests
    assert "SECRET-MARKER" not in repr(request)
    assert {f.name for f in fields(OfferRequest)}.isdisjoint({"text", "cleaned_text", "message"})


@pytest.mark.parametrize(
    ("detection", "result", "language"),
    [
        (Detection(code("en"), 0.95), "team", "en"),
        (Detection(code("es"), 0.8), "team", "es"),
        (Detection("other", 0.97), "other", "other"),
        (Detection(code("ko"), 0.69), "low_confidence", "ko"),
        (Detection(code("vi"), 0.5), "low_confidence", "vi"),
    ],
)
async def test_everything_but_a_confident_customer_language_is_counted_and_stops(
    detection: Detection, result: str, language: str
) -> None:
    rig = Rig(result=detection)

    await rig.run(message())

    assert rig.offers.requests == []
    [counted] = rig.classified()
    assert (counted["result"], counted["language"]) == (result, language)
    assert rig.internal_errors() == []


async def test_a_customer_language_exactly_at_the_threshold_gets_an_offer() -> None:
    rig = Rig(result=Detection(code("ru"), 0.7))

    await rig.run(message())

    assert len(rig.offers.requests) == 1


@pytest.mark.parametrize(
    "failure",
    [
        ModelError(ModelErrorCategory.TIMEOUT, "detect"),
        ModelError(ModelErrorCategory.INVALID_RESPONSE, "detect"),
        TimeoutError(),
    ],
)
async def test_a_classifier_failure_is_counted_as_an_error_and_ends_quietly(
    failure: Exception,
) -> None:
    rig = Rig(result=failure)

    await rig.run(message())

    assert rig.offers.requests == []
    [counted] = rig.classified()
    assert (counted["result"], counted["language"], counted["confidence"]) == (
        "error",
        "other",
        None,
    )
    assert rig.internal_errors() == []


async def test_a_classifier_that_never_answers_is_stopped_at_its_deadline() -> None:
    rig = Rig(timeout=0.05)
    rig.classifier.hold = asyncio.Event()

    await rig.run(message())

    assert rig.classified()[0]["result"] == "error"
    assert rig.offers.requests == []


async def test_an_unexpected_exception_is_counted_and_never_escapes() -> None:
    rig = Rig(result=RuntimeError("SECRET-MARKER boom"))

    await rig.run(message())

    assert rig.offers.requests == []
    [error] = rig.internal_errors()
    assert error == {"exception_type": "RuntimeError", "correlation_id": "corr-1"}


async def test_an_offer_step_that_fails_is_counted_after_the_classification() -> None:
    rig = Rig()
    rig.offers.error = RuntimeError("Slack is down")

    await rig.run(message())

    assert rig.classified()[0]["result"] == "customer"
    [error] = rig.internal_errors()
    assert error["exception_type"] == "RuntimeError"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"channel": "C9999999999"}, "not_allowlisted"),
        ({"subtype": "message_changed"}, "subtype"),
        ({"subtype": "message_deleted"}, "subtype"),
        ({"bot_id": "B0123ABCDE"}, "bot"),
        ({"text": ""}, "empty"),
        ({"text": None}, "empty"),
        ({"text": "<@U0123ABCDE> :tada:"}, "too_short"),
        ({"text": "ok"}, "too_short"),
    ],
)
async def test_an_excluded_message_never_reaches_the_classifier(
    changes: dict[str, Any], reason: str
) -> None:
    rig = Rig()

    await rig.run(message(**changes))

    assert rig.classifier.texts == []
    assert rig.offers.requests == []
    assert rig.classified() == []
    assert rig.dropped() == [
        {
            "reason": reason,
            "slack_event_id": "Ev0123ABCDE",
            "channel_id": changes.get("channel", CHANNEL),
        }
    ]


async def test_the_same_event_delivered_twice_is_classified_once() -> None:
    rig = Rig()

    await rig.run(message())
    await rig.run(message())

    assert len(rig.classifier.texts) == 1
    assert len(rig.offers.requests) == 1
    assert [d["reason"] for d in rig.dropped()] == ["duplicate"]


async def test_the_same_event_delivered_at_once_is_classified_once() -> None:
    rig = Rig()
    rig.classifier.hold = asyncio.Event()

    first = asyncio.create_task(rig.run(message()))
    second = asyncio.create_task(rig.run(message()))
    await asyncio.sleep(0.01)
    rig.classifier.hold.set()
    await asyncio.gather(first, second)

    assert len(rig.classifier.texts) == 1
    assert len(rig.offers.requests) == 1
    assert [d["reason"] for d in rig.dropped()] == ["duplicate"]


async def test_the_offer_request_says_when_the_event_arrived() -> None:
    rig = Rig()
    rig.clock.advance(42)

    await rig.run(message())

    assert rig.offers.requests[0].received_at == 1042.0


async def test_a_message_that_is_not_in_a_thread_has_no_thread_root() -> None:
    rig = Rig()

    await rig.run(message(thread_root_ts=None))

    assert rig.offers.requests[0].ref.thread_root_ts is None


async def test_a_new_event_id_is_processed_and_an_old_one_expires() -> None:
    rig = Rig()

    await rig.run(message(event_id="Ev1"))
    await rig.run(message(event_id="Ev2"))
    rig.clock.advance(601)
    await rig.run(message(event_id="Ev1"))

    assert len(rig.offers.requests) == 3
    assert rig.dropped() == []


async def test_an_event_dropped_by_a_filter_leaves_no_duplicate_key() -> None:
    rig = Rig()

    await rig.run(message(subtype="message_changed"))
    await rig.run(message())

    assert len(rig.offers.requests) == 1
    assert [d["reason"] for d in rig.dropped()] == ["subtype"]


async def test_without_an_event_id_the_channel_and_timestamp_stand_in() -> None:
    rig = Rig()

    await rig.run(message(event_id=None))
    await rig.run(message(event_id=None))
    await rig.run(message(event_id=None, ts="1700000001.000200"))

    assert len(rig.offers.requests) == 2
    assert [d["reason"] for d in rig.dropped()] == ["duplicate"]


@pytest.mark.parametrize("author", ["U0CUSTOMER1", "U0MEMBER001", None])
async def test_there_is_no_author_check_and_the_author_goes_on_to_the_offer_step(
    author: str | None,
) -> None:
    rig = Rig()

    await rig.run(message(user_id=author))

    [request] = rig.offers.requests
    assert request.author_id == author
