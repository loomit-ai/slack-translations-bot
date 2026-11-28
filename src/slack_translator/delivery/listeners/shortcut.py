# pyright: basic
"""SLK-01 · The message shortcut `translate_message` (FLOW-01)."""

import uuid
from typing import Any

from slack_bolt.async_app import AsyncAck, AsyncApp

from slack_translator.delivery.listeners.context import ListenerContext
from slack_translator.delivery.payloads import parse_shortcut
from slack_translator.domain.call_context import correlation_scope

NAME = "translate_message"


def register(app: AsyncApp, context: ListenerContext) -> None:
    @app.message_shortcut(NAME)
    async def translate_message(ack: AsyncAck, body: dict[str, Any]) -> None:
        started = context.clock.now()
        await ack()
        context.telemetry.ack_observed(kind="shortcut", seconds=context.clock.now() - started)

        request = parse_shortcut(body, correlation_id=uuid.uuid4().hex)
        if request is None:
            context.telemetry.internal_error(exception_type="InvalidPayload", correlation_id=None)
            return
        with correlation_scope(request.correlation_id):
            await context.registry.run(context.translate_message.run(request))
