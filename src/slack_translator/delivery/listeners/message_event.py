# pyright: basic
"""FLOW-02 · The `message` event for public and private channels (message.channels, message.groups).

Bolt acknowledges events itself (REQ-01). Everything else runs as registered background work, so
a test can wait for it and shutdown can give it time. Slack's envelope is read here and nowhere
else; intake sees only the facts it needs. Message text goes to the action and is never logged.
"""

import uuid
from typing import Any, Protocol

from slack_bolt.async_app import AsyncApp

from slack_translator.actions.classify_message import ClassifyMessage
from slack_translator.delivery.background import TaskRegistry
from slack_translator.domain.intake import MessageEvent
from slack_translator.domain.ports import Telemetry
from slack_translator.infrastructure.call_context import correlation_scope

NAME = "message_event"


class IntakeContext(Protocol):
    """The part of `ListenerContext` this listener uses."""

    @property
    def classify_message(self) -> ClassifyMessage: ...

    @property
    def registry(self) -> TaskRegistry: ...

    @property
    def telemetry(self) -> Telemetry: ...


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value != "" else None


def parse_message_event(body: dict[str, Any]) -> MessageEvent | None:
    """Slack's `event_callback` envelope to a `MessageEvent`, or `None` if it cannot be placed."""
    event = body.get("event")
    if not isinstance(event, dict) or event.get("type") != "message":
        return None
    channel = _text(event.get("channel"))
    ts = _text(event.get("ts"))
    if channel is None or ts is None:
        return None
    thread_ts = _text(event.get("thread_ts"))
    text = event.get("text")
    return MessageEvent(
        event_id=_text(body.get("event_id")),
        channel=channel,
        ts=ts,
        thread_root_ts=thread_ts if thread_ts != ts else None,
        user_id=_text(event.get("user")),
        text=text if isinstance(text, str) else None,
        subtype=_text(event.get("subtype")),
        bot_id=_text(event.get("bot_id")),
    )


def register(app: AsyncApp, context: IntakeContext) -> None:
    @app.event("message")
    async def message_event(body: dict[str, Any]) -> None:
        event = parse_message_event(body)
        if event is None:
            context.telemetry.internal_error(exception_type="InvalidPayload", correlation_id=None)
            return
        correlation_id = uuid.uuid4().hex
        with correlation_scope(correlation_id):
            await context.registry.run(context.classify_message.run(event, correlation_id))
