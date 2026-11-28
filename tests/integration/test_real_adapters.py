"""FLOW-01 with the real adapters: Lingua and the translator, with the action making the retry.

They run against the fake translator endpoint (TS-02) and the fake Slack (TS-01).
Made-up text only.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from slack_bolt.request.async_request import AsyncBoltRequest
from tests.integration.conftest import SETTINGS
from tests.support.fake_ports import FakeClock, RecordingTelemetry
from tests.support.fake_slack import RESPONSE_URL, FakeSlackServer
from tests.support.fake_translator import (
    Answer,
    Fail,
    FakeTranslatorServer,
    Malformed,
    obey_injected_instructions,
)
from tests.support.payloads import CHANNEL, MEMBER, TS, retry_payload, shortcut_payload

from slack_translator.delivery.app import Application, Overrides, create_app
from slack_translator.delivery.settings import Settings
from slack_translator.infrastructure.lingua_classifier import LinguaClassifier

KOREAN = "파란 상자에 종이 별이 세 개 있어요."
ENGLISH = "I put three paper stars in the blue box on the table yesterday."
ENGLISH_FIXTURE = "I put three paper stars in the blue box."


@dataclass
class RealHarness:
    app: Application
    slack: FakeSlackServer
    translator: FakeTranslatorServer
    telemetry: RecordingTelemetry

    async def shortcut(self, text: str) -> list[dict[str, Any]]:
        payload = shortcut_payload(text=text, response_url=self.slack.response_url())
        await self.app.bolt.async_dispatch(AsyncBoltRequest(body=payload, mode="socket_mode"))
        await self.app.registry.drain()
        return [call.payload for call in self.slack.calls_of(RESPONSE_URL)]

    async def retry(self) -> list[dict[str, Any]]:
        payload = retry_payload(response_url=self.slack.response_url())
        await self.app.bolt.async_dispatch(AsyncBoltRequest(body=payload, mode="socket_mode"))
        await self.app.registry.drain()
        return [call.payload for call in self.slack.calls_of(RESPONSE_URL)]


@pytest.fixture(name="real")
async def real_harness() -> AsyncIterator[RealHarness]:
    slack = FakeSlackServer()
    await slack.start()
    slack.add_user(MEMBER)
    slack.add_channel(CHANNEL)
    translator = FakeTranslatorServer()
    await translator.start()
    telemetry = RecordingTelemetry()
    settings = Settings.from_environ(
        {
            **SETTINGS,
            "TRANSLATOR_BASE_URL": translator.base_url,
            # Real Lingua loads its models on first use, which can pass the 0.1 s of the other
            # harness; the deadline is not what these tests are about.
            "CLASSIFIER_TIMEOUT": "3",
        }
    )
    classifier = LinguaClassifier(telemetry=telemetry, clock=FakeClock(), timeout=3.0)
    await classifier.preload()
    app = create_app(
        settings,
        Overrides(
            classifier=classifier,
            telemetry=telemetry,
            clock=FakeClock(),
            log_guard=lambda: None,
            slack_api_base_url=slack.api_base_url,
        ),
    )
    await app.prepare()
    slack.calls.clear()
    try:
        yield RealHarness(app, slack, translator, telemetry)
    finally:
        await app.close()
        await translator.stop()
        await slack.stop()


async def test_a_korean_message_is_detected_translated_and_shown_privately(
    real: RealHarness,
) -> None:
    real.translator.enqueue(Answer(text="There are three paper stars in the blue box."))

    [post] = await real.shortcut(KOREAN)

    assert post["text"] == (
        "English translation from Korean\nThere are three paper stars in the blue box."
    )
    [request] = real.translator.requests
    assert KOREAN in request.user_text
    assert KOREAN not in request.system_message
    real.slack.assert_no_public_writes()


async def test_an_english_message_is_recognised_and_never_sent_to_the_translator(
    real: RealHarness,
) -> None:
    [post] = await real.shortcut(ENGLISH)
    assert post["text"] == "This message is already in English."
    assert real.translator.requests == []


async def test_the_test_plan_english_fixture_is_already_in_english_at_the_default(
    real: RealHarness,
) -> None:
    [post] = await real.shortcut(ENGLISH_FIXTURE)

    assert post["text"] == "This message is already in English."
    assert real.translator.requests == []


async def test_one_provider_hiccup_is_retried_without_the_member_noticing(
    real: RealHarness,
) -> None:
    real.translator.enqueue(Fail(503), Answer(text="Three paper stars."))
    [post] = await real.shortcut(KOREAN)
    assert post["text"] == "English translation from Korean\nThree paper stars."
    assert len(real.translator.requests) == 2


async def test_two_failures_end_in_a_private_error_with_retry(real: RealHarness) -> None:
    real.translator.enqueue(Fail(503), Fail(503, echo_input=True))
    [post] = await real.shortcut(KOREAN)
    assert post["text"] == "We couldn’t translate this message. Retry translation."
    assert len(real.translator.requests) == 2
    assert KOREAN not in repr(real.telemetry.events)


async def test_a_translator_that_obeys_the_text_cannot_plant_a_mention_or_link(
    real: RealHarness,
) -> None:
    real.translator.set_default(Answer(text=obey_injected_instructions))
    [post] = await real.shortcut(KOREAN)
    shown = "".join(b["text"]["text"] for b in post["blocks"] if b["type"] == "section")
    assert "<!channel>" not in shown
    assert "<https://injected.example" not in shown
    assert "&lt;!channel&gt;" in shown


async def test_the_retry_path_shows_the_error_note_while_the_translator_retries(
    real: RealHarness,
) -> None:
    real.slack.add_message(CHANNEL, TS, KOREAN)
    real.translator.enqueue(Fail(503), Answer(text="Three paper stars."))

    posts = await real.retry()

    assert [post["text"] for post in posts] == [
        "Translating…",
        "An error has occurred…",
        "English translation from Korean\nThree paper stars.",
    ]
    assert all(post["replace_original"] is True for post in posts)
    real.slack.assert_no_public_writes()


async def test_the_menu_path_shows_no_note_while_the_translator_retries(
    real: RealHarness,
) -> None:
    real.translator.enqueue(Fail(503), Answer(text="Three paper stars."))
    posts = await real.shortcut(KOREAN)
    assert [post["text"] for post in posts] == [
        "English translation from Korean\nThree paper stars."
    ]


FAILED = "We couldn’t translate this message. Retry translation."


@pytest.mark.parametrize(
    "first",
    [
        Fail(429, retry_after=1),
        Fail(500),
        Fail(503),
        Malformed("empty_content"),
        Malformed("no_choices"),
        Malformed("truncated"),
        Malformed("object_finish_reason"),
        Malformed("list_finish_reason"),
        Malformed("number_finish_reason"),
    ],
)
async def test_a_first_failure_then_success_gives_one_result_after_exactly_one_retry(
    real: RealHarness, first: Fail | Malformed
) -> None:
    real.translator.enqueue(first, Answer(text="Three paper stars."))

    posts = await real.shortcut(KOREAN)

    assert [post["text"] for post in posts] == [
        "English translation from Korean\nThree paper stars."
    ]
    assert len(real.translator.requests) == 2
    records = [e.fields["record"] for e in real.telemetry.named("model_call")]
    assert [r.attempt for r in records if r.operation == "translate"] == [1, 2]


@pytest.mark.parametrize(
    "fault",
    [Fail(429), Fail(502), Malformed("not_json"), Malformed("object_finish_reason")],
)
async def test_two_failures_make_one_retry_and_end_in_the_error_with_retry(
    real: RealHarness, fault: Fail | Malformed
) -> None:
    real.translator.set_default(fault)

    posts = await real.shortcut(KOREAN)

    assert [post["text"] for post in posts] == [FAILED]
    assert len(real.translator.requests) == 2


async def test_a_rejected_request_is_sent_once(real: RealHarness) -> None:
    real.translator.set_default(Fail(401))

    posts = await real.shortcut(KOREAN)

    assert [post["text"] for post in posts] == [FAILED]
    assert len(real.translator.requests) == 1
