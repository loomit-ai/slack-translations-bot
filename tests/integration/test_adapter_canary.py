"""FF-03 for the model adapters: no message text in any log line or counter, on every path.

The source text, the translation and the text echoed in a provider error each carry a unique
marker. The real app, translator, Lingua and telemetry run against the fake endpoint and the fake
Slack through success, timeout, retry (with and without the error note) and failure; then every
log line and the whole metrics exposition are searched for each marker, raw and JSON-escaped
(TC-056).
"""

import json
import secrets
from typing import Any

from prometheus_client import generate_latest
from slack_bolt.request.async_request import AsyncBoltRequest
from tests.integration.conftest import SETTINGS
from tests.support.fake_ports import FakeClock
from tests.support.fake_slack import FakeSlackServer
from tests.support.fake_translator import (
    Answer,
    Fail,
    FakeTranslatorServer,
    Hang,
    Malformed,
)
from tests.support.payloads import CHANNEL, MEMBER, TS, retry_payload, shortcut_payload

from slack_translator.delivery.app import Application, Overrides, create_app
from slack_translator.delivery.settings import Settings
from slack_translator.infrastructure.lingua_classifier import LinguaClassifier
from slack_translator.infrastructure.telemetry import LogTelemetry


def marker(kind: str) -> str:
    return f"{kind}-{secrets.token_hex(8)}"


async def deliver(app: Application, payload: dict[str, Any]) -> None:
    await app.bolt.async_dispatch(AsyncBoltRequest(body=payload, mode="socket_mode"))
    await app.registry.drain()


async def test_no_marker_reaches_a_log_line_or_a_counter() -> None:
    source, translation, echoed, key = (
        marker("SOURCE"),
        marker("TRANSLATION"),
        marker("ECHOED"),
        marker("KEY"),
    )
    text = f"{source} 파란 상자에 종이 별이 세 개 있어요."
    lines: list[str] = []
    telemetry = LogTelemetry(app_env="development", version="0.1.0", sink=lines.append)
    slack = FakeSlackServer()
    await slack.start()
    slack.add_user(MEMBER)
    slack.add_channel(CHANNEL)

    async with FakeTranslatorServer() as fake:
        settings = Settings.from_environ(
            {
                **SETTINGS,
                "TRANSLATOR_BASE_URL": fake.base_url,
                "TRANSLATOR_API_KEY": key,
                "TRANSLATOR_TIMEOUT": "1",
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
        try:
            await app.prepare()

            async def shortcut(body: str) -> None:
                await deliver(app, shortcut_payload(text=body, response_url=slack.response_url()))

            fake.enqueue(Answer(translation))  # success
            await shortcut(text)

            fake.enqueue(Hang(), Answer(translation))  # timeout, then success
            await shortcut(text)

            fake.enqueue(Fail(503, echo_input=True), Answer(translation))  # one retry
            await shortcut(text)

            fake.enqueue(Fail(429, retry_after=1, echo_input=True), Fail(500, echo_input=True))
            await shortcut(text)  # two failures

            fake.enqueue(Fail(401, echo_input=True))  # not retried
            await shortcut(text)

            fake.enqueue(Malformed("truncated"), Malformed("empty_content"))
            await shortcut(text)

            fake.set_default(Fail(500, echo_input=True))  # an echo of the marker in the body
            await shortcut(f"{echoed} {text}")
            fake.set_default(Answer(translation))

            slack.add_message(CHANNEL, TS, text)  # Retry click: Translating…, the note, the result
            fake.enqueue(Fail(503, echo_input=True), Answer(translation))
            await deliver(app, retry_payload(response_url=slack.response_url()))
        finally:
            await app.close()
            await slack.stop()

    exposition = generate_latest(telemetry.registry).decode()
    everything = "\n".join([*lines, exposition])
    for secret in (source, translation, echoed, key, text):
        assert secret not in everything
        assert json.dumps(secret)[1:-1] not in everything
    assert CHANNEL in everything, "the collection control must be visible"
    assert len(lines) >= 20
    assert 'outcome="timeout"' in exposition
    assert 'outcome="invalid_response"' in exposition
    events = [json.loads(line) for line in lines]
    assert {e["attempt"] for e in events if e["event"] == "model.call"} == {1, 2}
