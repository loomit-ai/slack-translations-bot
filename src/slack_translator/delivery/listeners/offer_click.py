# pyright: basic
"""SLK-01 · The offer button `translate_offer` (FLOW-03)."""

import uuid
from typing import Any

from slack_bolt.async_app import AsyncAck, AsyncApp

from slack_translator.delivery.listeners.context import ListenerContext
from slack_translator.delivery.payloads import parse_offer_click
from slack_translator.delivery.views.ui_02_offer import OFFER_ACTION_ID
from slack_translator.domain.call_context import correlation_scope

NAME = OFFER_ACTION_ID


def register(app: AsyncApp, context: ListenerContext) -> None:
    @app.action(NAME)
    async def translate_offer(ack: AsyncAck, body: dict[str, Any]) -> None:
        started = context.clock.now()
        await ack()
        context.telemetry.ack_observed(kind="action", seconds=context.clock.now() - started)

        request = parse_offer_click(body, correlation_id=uuid.uuid4().hex)
        if request is None:
            context.telemetry.internal_error(exception_type="InvalidPayload", correlation_id=None)
            return
        with correlation_scope(request.correlation_id):
            await context.registry.run(context.translate_message.run(request))
