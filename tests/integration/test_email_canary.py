"""FF-03 for CR-09 part C: an email address, or its domain, never reaches a log line or a metric."""

import json
from typing import Any

from prometheus_client import generate_latest
from slack_bolt.request.async_request import AsyncBoltRequest
from tests.integration.conftest import SETTINGS
from tests.support.fake_ports import FakeClock, ScriptedClassifier, ScriptedTranslator
from tests.support.fake_slack import FakeSlackServer
from tests.support.payloads import CHANNEL, shortcut_payload

from slack_translator.delivery.app import Overrides, create_app
from slack_translator.delivery.settings import Settings
from slack_translator.infrastructure.telemetry import LogTelemetry

SERVED = {"id": "U0SERVED", "team_id": "T0HOME", "profile": {"email": "canary-served@x3mads.com"}}
REFUSED = {
    "id": "U0REFUSED",
    "team_id": "T0HOME",
    "profile": {"email": "canary-refused@other-marker.example"},
}
MARKERS = ("canary-", "other-marker.example", "x3mads.com", "@")


async def test_no_address_or_domain_appears_in_any_log_line_or_metric() -> None:
    slack = FakeSlackServer()
    await slack.start()
    slack.add_channel(CHANNEL)
    slack.users.update({"U0SERVED": SERVED, "U0REFUSED": REFUSED})
    lines: list[str] = []
    telemetry = LogTelemetry(app_env="development", version="test", sink=lines.append)
    app = create_app(
        Settings.from_environ(SETTINGS),
        Overrides(
            classifier=ScriptedClassifier(),
            translator=ScriptedTranslator("Where is the blue box?"),
            telemetry=telemetry,
            clock=FakeClock(),
            log_guard=lambda: None,
            slack_api_base_url=slack.api_base_url,
        ),
    )
    try:
        await app.prepare()
        for user in ("U0SERVED", "U0REFUSED"):
            payload: dict[str, Any] = shortcut_payload(
                user=user, response_url=slack.response_url(), text="파란 상자는 어디에 있나요?"
            )
            await app.bolt.async_dispatch(AsyncBoltRequest(body=payload, mode="socket_mode"))
            await app.registry.drain()
    finally:
        await app.close()
        await slack.stop()

    events = [json.loads(line) for line in lines]
    refused = [event for event in events if event["event"] == "request.refused"]
    assert [event["refusal_reason"] for event in refused] == ["email_domain"]
    assert any(event["event"] == "translate.completed" for event in events)

    everything = "\n".join(lines) + generate_latest(telemetry.registry).decode()
    for marker in MARKERS:
        assert marker not in everything, marker
