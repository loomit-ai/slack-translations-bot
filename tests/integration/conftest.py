"""TS-04 · Drive the real app through Bolt with fakes for Slack and both models."""

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_bolt.response import BoltResponse
from tests.support.fake_ports import (
    FakeClock,
    RecordingTelemetry,
    ScriptedClassifier,
    ScriptedTranslator,
)
from tests.support.fake_slack import FakeSlackServer
from tests.support.payloads import CHANNEL, MEMBER

from slack_translator.delivery.app import Application, Overrides, create_app
from slack_translator.delivery.settings import Settings

SETTINGS = {
    "CHANNEL_ALLOWLIST": CHANNEL,
    "TRANSLATOR_BASE_URL": "http://127.0.0.1:9",
    "TRANSLATOR_MODEL": "test-model",
    "TRANSLATOR_API_KEY": "test-translator-key",
    "SLACK_BOT_TOKEN": "xoxb-test-bot",
    "SLACK_APP_TOKEN": "xapp-test-app",
    "APP_ENV": "development",
    "CLASSIFIER_TIMEOUT": "0.1",
}


@dataclass
class Harness:
    app: Application
    slack: FakeSlackServer
    classifier: ScriptedClassifier
    translator: ScriptedTranslator
    telemetry: RecordingTelemetry
    clock: FakeClock
    timeline: list[str]

    async def deliver(self, payload: dict[str, Any]) -> tuple[BoltResponse | None, float]:
        """Deliver one interaction. Returns Bolt's answer and how long the acknowledgement took."""
        request = AsyncBoltRequest(body=payload, mode="socket_mode")
        started = time.perf_counter()
        response = await self.app.bolt.async_dispatch(request)
        return response, time.perf_counter() - started

    async def deliver_and_settle(self, payload: dict[str, Any]) -> BoltResponse | None:
        response, _ = await self.deliver(payload)
        await self.app.registry.drain()
        return response

    def private_texts(self) -> list[str]:
        """The fallback text of every private message shown, in order."""
        return [
            str(call.payload.get("text"))
            for call in self.slack.calls
            if call.kind == "private_write" and "text" in call.payload
        ]


@pytest.fixture(name="h")
async def harness() -> AsyncIterator[Harness]:
    timeline: list[str] = []
    slack = FakeSlackServer(timeline)
    await slack.start()
    slack.add_user(MEMBER)
    slack.add_channel(CHANNEL, is_member=True)

    classifier = ScriptedClassifier(timeline=timeline)
    translator = ScriptedTranslator("Where is the blue box?", timeline=timeline)
    telemetry = RecordingTelemetry(timeline)
    clock = FakeClock()
    app = create_app(
        Settings.from_environ(SETTINGS),
        Overrides(
            classifier=classifier,
            translator=translator,
            telemetry=telemetry,
            clock=clock,
            slack_api_base_url=slack.api_base_url,
        ),
    )
    await app.prepare()
    slack.calls.clear()
    telemetry.events.clear()
    timeline.clear()
    try:
        yield Harness(app, slack, classifier, translator, telemetry, clock, timeline)
    finally:
        await app.close()
        await slack.stop()
