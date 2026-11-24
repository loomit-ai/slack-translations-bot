"""FLOW-01 with the real adapters: Lingua, the translator and the retry wrapper.

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
    obey_injected_instructions,
)
from tests.support.payloads import CHANNEL, MEMBER, shortcut_payload

from slack_translator.delivery.app import Application, Overrides, create_app
from slack_translator.delivery.settings import Settings
from slack_translator.infrastructure.lingua_classifier import LinguaClassifier

KOREAN = "파란 상자에 종이 별이 세 개 있어요."
ENGLISH = "I put three paper stars in the blue box on the table yesterday."


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
            # Lingua scores a plain English sentence around 0.65 over all 75 languages, so the
            # 0.8 default never says "already in English". The decision-4 test will set the value.
            "CLASSIFIER_MIN_CONFIDENCE": "0.5",
        }
    )
    app = create_app(
        settings,
        Overrides(
            classifier=LinguaClassifier(telemetry=telemetry, clock=FakeClock(), timeout=3.0),
            telemetry=telemetry,
            clock=FakeClock(),
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
