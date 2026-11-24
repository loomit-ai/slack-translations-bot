"""FLOW-02 · The `message` event listener: what it reads from Slack's envelope, what it starts."""

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any

import pytest
from slack_bolt.async_app import AsyncApp
from slack_bolt.authorization import AuthorizeResult
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_sdk.web.async_client import AsyncWebClient
from tests.support.fake_ports import RecordingTelemetry

from slack_translator.delivery.background import TaskRegistry
from slack_translator.delivery.listeners.message_event import NAME, parse_message_event, register
from slack_translator.domain.intake import MessageEvent
from slack_translator.infrastructure.call_context import current_correlation_id

CHANNEL = "C0123ABCDE"


def envelope(event: dict[str, Any] | None = None, **top: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "token": "unused",
        "team_id": "T0HOME",
        "api_app_id": "A0APP",
        "type": "event_callback",
        "event_id": "Ev0123ABCDE",
        "event_time": 1700000000,
        "event": {
            "type": "message",
            "channel": CHANNEL,
            "channel_type": "channel",
            "user": "U0CUSTOMER",
            "text": "파란 상자에 종이 별이 세 개 있어요.",
            "ts": "1700000000.000100",
            "event_ts": "1700000000.000100",
        },
    }
    if event is not None:
        body["event"] = {**body["event"], **event}
    body.update(top)
    return body


def parsed(event: dict[str, Any] | None = None, **top: Any) -> MessageEvent | None:
    return parse_message_event(envelope(event, **top))


def test_a_plain_message_is_read_into_the_facts_intake_needs() -> None:
    assert parsed() == MessageEvent(
        event_id="Ev0123ABCDE",
        channel=CHANNEL,
        ts="1700000000.000100",
        thread_root_ts=None,
        user_id="U0CUSTOMER",
        text="파란 상자에 종이 별이 세 개 있어요.",
        subtype=None,
        bot_id=None,
        channel_type="channel",
    )


def test_a_reply_in_a_thread_names_its_thread_root() -> None:
    event = parsed({"thread_ts": "1700000000.000050"})

    assert event is not None
    assert event.thread_root_ts == "1700000000.000050"


def test_the_first_message_of_a_thread_has_no_thread_root() -> None:
    event = parsed({"thread_ts": "1700000000.000100"})

    assert event is not None
    assert event.thread_root_ts is None


def test_subtype_and_bot_are_kept_so_the_filters_can_judge_them() -> None:
    event = parsed({"subtype": "bot_message", "bot_id": "B0BOT", "user": None})

    assert event is not None
    assert (event.subtype, event.bot_id, event.user_id) == ("bot_message", "B0BOT", None)


@pytest.mark.parametrize("subtype", ["message_changed", "message_deleted", "channel_join"])
def test_events_without_text_or_author_still_parse(subtype: str) -> None:
    body = envelope({"subtype": subtype})
    for key in ("text", "user"):
        del body["event"][key]

    event = parse_message_event(body)

    assert event is not None
    assert (event.subtype, event.text, event.user_id) == (subtype, None, None)


def test_empty_strings_count_as_missing() -> None:
    event = parsed({"subtype": "", "bot_id": "", "user": "", "thread_ts": "", "channel_type": ""})

    assert event is not None
    assert (
        event.subtype,
        event.bot_id,
        event.user_id,
        event.thread_root_ts,
        event.channel_type,
    ) == (None, None, None, None, None)


def test_a_text_that_is_not_text_is_treated_as_missing() -> None:
    event = parsed({"text": 5})

    assert event is not None
    assert event.text is None


def test_without_an_event_id_the_id_is_missing_not_invented() -> None:
    body = envelope()
    del body["event_id"]

    event = parse_message_event(body)

    assert event is not None
    assert event.event_id is None


@pytest.mark.parametrize(
    "body",
    [
        envelope({"channel": None}),
        envelope({"channel": ""}),
        envelope({"channel": 7}),
        envelope({"ts": None}),
        envelope({"ts": ""}),
        envelope({"type": "reaction_added"}),
        {**envelope(), "event": "not a dict"},
        {**envelope(), "event": None},
        {},
    ],
)
def test_an_event_that_cannot_be_placed_is_not_parsed(body: dict[str, Any]) -> None:
    assert parse_message_event(body) is None


# Through Bolt: the listener is registered and Slack's own envelope reaches it.


@dataclass
class FakeIntake:
    runs: list[tuple[MessageEvent, str, str | None]] = field(default_factory=list)
    arrived: asyncio.Event = field(default_factory=asyncio.Event)

    async def run(self, event: MessageEvent, correlation_id: str) -> None:
        self.runs.append((event, correlation_id, current_correlation_id()))
        self.arrived.set()


@dataclass
class FakeContext:
    classify_message: FakeIntake
    registry: TaskRegistry
    telemetry: RecordingTelemetry


def bolt_app() -> AsyncApp:
    token = f"xoxb-{secrets.token_hex(6)}"

    async def authorize() -> AuthorizeResult:
        return AuthorizeResult(
            enterprise_id=None,
            team_id="T0HOME",
            bot_token=token,
            bot_id="B0BOT",
            bot_user_id="U0BOT",
        )

    return AsyncApp(
        client=AsyncWebClient(token=token),
        authorize=authorize,
        process_before_response=False,
        request_verification_enabled=False,
    )


async def deliver(context: FakeContext, body: dict[str, Any], expect_run: bool = True) -> None:
    app = bolt_app()
    register(app, context)  # type: ignore[arg-type]
    await app.async_dispatch(AsyncBoltRequest(body=body, mode="socket_mode"))
    if expect_run:
        await asyncio.wait_for(context.classify_message.arrived.wait(), timeout=2)
    else:
        await asyncio.sleep(0.1)
    await context.registry.drain()


@pytest.fixture
def context() -> FakeContext:
    return FakeContext(FakeIntake(), TaskRegistry(), RecordingTelemetry())


async def test_a_message_event_reaches_intake_with_a_correlation_id_in_scope(
    context: FakeContext,
) -> None:
    await deliver(context, envelope())

    [(event, correlation_id, scoped)] = context.classify_message.runs
    assert event.channel == CHANNEL
    assert event.text == "파란 상자에 종이 별이 세 개 있어요."
    assert correlation_id
    assert scoped == correlation_id


@pytest.mark.parametrize(
    "event",
    [
        {"subtype": "message_changed"},
        {"subtype": "message_deleted"},
        {"subtype": "bot_message", "bot_id": "B0OTHER"},
        {"subtype": "thread_broadcast", "thread_ts": "1700000000.000050"},
        {"subtype": "file_share"},
    ],
)
async def test_events_with_a_subtype_are_not_swallowed_by_bolt(
    context: FakeContext, event: dict[str, Any]
) -> None:
    await deliver(context, envelope(event))

    assert len(context.classify_message.runs) == 1


async def test_an_envelope_that_cannot_be_read_is_counted_and_starts_nothing(
    context: FakeContext,
) -> None:
    await deliver(context, envelope({"channel": None}), expect_run=False)

    assert context.classify_message.runs == []
    [error] = context.telemetry.named("internal_error")
    assert error.fields == {"exception_type": "InvalidPayload", "correlation_id": None}


def test_the_listener_has_a_name_for_the_acknowledgement_registry() -> None:
    assert NAME == "message_event"
