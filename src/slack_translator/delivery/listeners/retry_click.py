# pyright: basic
"""SLK-01 · The Retry button `translate_retry` (FLOW-01, Retry click)."""

import uuid
from typing import Any

from slack_bolt.async_app import AsyncAck, AsyncApp

from slack_translator.delivery.listeners.context import ListenerContext
from slack_translator.delivery.payloads import parse_retry_click
from slack_translator.delivery.views.ui_08_failed import RETRY_ACTION_ID

NAME = RETRY_ACTION_ID


def register(app: AsyncApp, context: ListenerContext) -> None:
    @app.action(NAME)
    async def translate_retry(ack: AsyncAck, body: dict[str, Any]) -> None:
        started = context.clock.now()
        await ack()
        context.telemetry.ack_observed(kind="action", seconds=context.clock.now() - started)

        request = parse_retry_click(body, correlation_id=uuid.uuid4().hex)
        if request is None:
            context.telemetry.internal_error(exception_type="InvalidPayload", correlation_id=None)
            return
        await context.registry.run(context.translate_message.run(request))
